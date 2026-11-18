"""Unit 10 security and behavior boundaries, using the real tool/business services."""
import asyncio
import copy
from datetime import datetime,timezone
from unittest.mock import AsyncMock
import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,update
from google.genai import types
from test_persistence import database,HEADERS,conversation,message
from structured_model import CaseModel
from work_assistant.api import create_app
from work_assistant.agent import AgentRequest,GeminiModel
from work_assistant.agents.runtime import CollaborationAgent
from work_assistant.agents.budget import Budget
from work_assistant.agents.contracts import Plan,Claim,Support
from work_assistant.agents.model import StructuredReply
from work_assistant.knowledge import KnowledgeService
from work_assistant.repository import FixtureRepository
from work_assistant.db.models import entities
from work_assistant.verification.claims import validate_claims


def run(db,question='Investigate INC-105 and start review',model=None,**kwargs):
    agent=CollaborationAgent(KnowledgeService(db),model or CaseModel(),**kwargs)
    return asyncio.run(agent.ask('mei',AgentRequest(question=question)))


def test_supervisor_calls_independent_specialists_and_human_review(database):
    model=CaseModel()
    with TestClient(create_app({'mei':'mei','lin':'lin','alex':'alex'},database_url=database.url,model_factory=lambda:model,embedded_worker=False)) as client:
        result=message(client,conversation(client),question='Investigate INC-105 and start review')
        assert result.status_code==200,result.text
        saved=result.json();response=saved['result']
        assert response['status']=='answered',response
        assert response['agent_mode']=='supervisor' and response['verification']['checked_claims']>0
        assert {'supervisor','knowledge','analyst','action','critic'}<={r for r,_ in model.calls}
        plan=client.get('/v1/runs/'+saved['id']+'/plan',headers=HEADERS)
        assert plan.status_code==200 and len(plan.json()['steps'])==4
        assert client.get('/v1/runs/'+saved['id']+'/plan',headers={'Authorization':'Bearer alex'}).status_code==404
        from work_assistant.workflows.tables import workflows,credits
        with database.engine.connect() as c:
            row=c.execute(select(workflows)).mappings().one()
            assert row['origin_run_id']==saved['id'] and row['status']=='queued'
            assert not c.execute(select(credits)).first()
        events=saved['events'];starts=[e['payload']['role'] for e in events if e['kind']=='specialist_started']
        assert starts[:2]==['knowledge','analyst']
        # Dependency contexts are limited to explicit handoffs; no cross-worker conversation dump.
        knowledge=next(p for r,p in model.calls if r=='knowledge')
        assert knowledge['handoffs']=={} and knowledge['sources']==[]


def test_ambiguous_request_persists_clarification_without_tools(database):
    result=run(database,'Please handle the customer issue')
    assert result.status=='needs_clarification' and result.tool_calls==0


def test_single_agent_baseline_uses_same_tool_and_critic_boundaries(database):
    model=CaseModel();agent=CollaborationAgent(KnowledgeService(database),model)
    result=asyncio.run(agent.ask('mei',AgentRequest(question='Investigate INC-105 and start review',mode='single')))
    assert result.status=='answered' and result.agent_mode=='single'
    assert 'supervisor' not in [r for r,_ in model.calls] and 'critic' in [r for r,_ in model.calls]


def test_false_claim_with_real_citation_is_rejected_then_repaired():
    model=CaseModel('false_claim')
    result=run(FixtureRepository.load(),'What is the response target?',model)
    assert result.status=='answered' and 'guaranteed resolution' not in result.answer
    assert result.verification['repairs']==1 and any(r=='repair' for r,_ in model.calls)


def test_planner_rejects_cycles_unknown_dependencies_wrong_roles_and_disconnected_work():
    model=CaseModel()
    valid=asyncio.run(model.structured('supervisor',{'question':'Investigate INC-105 and start review'},Plan,'en')).data
    assert Plan.model_validate(valid)
    for change in ('cycle','tool','terminal','orphan','input'):
        bad=copy.deepcopy(valid)
        if change=='cycle':bad['nodes'][0]['dependencies']=['action']
        if change=='tool':bad['nodes'][0]['tools']=['start_sla_workflow']
        if change=='terminal':bad['terminal']='action'
        if change=='orphan':bad['nodes'][2]['dependencies']=['analyst'];bad['nodes'][2]['required_inputs']=['analyst']
        if change=='input':bad['nodes'][0]['required_inputs']=['analyst']
        with pytest.raises(ValueError):Plan.model_validate(bad)


def test_role_tool_escalation_is_denied_even_with_valid_plan(database):
    class Bad(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='knowledge':return self.tool('start_sla_workflow',{'ticket_id':'INC-105','expected_ticket_version':1})
            return await super().structured(role,payload,*args)
    result=run(database,model=Bad())
    assert result.status=='invalid_response'
    from work_assistant.workflows.tables import workflows
    with database.engine.connect() as c:assert not c.execute(select(workflows)).first()


def test_numeric_and_root_cause_claims_cannot_be_approved_by_critic():
    doc=KnowledgeService(FixtureRepository.load()).read('mei','acme-sla','en')
    support=Support(source_id=doc.id,version=doc.version,quote=doc.content)
    for text in ['The response target is 30 minutes.','The root cause is maintenance.','Credit was issued to the customer.']:
        assert validate_claims([Claim(text=text,evidence=[support])],{doc.id:doc})


def test_changed_contract_replans_dependent_work_and_preserves_independent_read(database):
    class Changing(CaseModel):
        async def structured(self,role,payload,*args):
            response=await super().structured(role,payload,*args)
            if role=='analyst' and response.data.get('result') and not self.fired:
                self.fired=True
                from work_assistant.sla.tables import contracts
                with database.engine.begin() as c:c.execute(update(contracts).where(contracts.c.organization_id=='northstar').values(version=2))
            return response
    model=Changing()
    result=run(database,model=model)
    assert result.status=='answered',result
    assert result.budget['replans']==1 and result.plan['revision']==2
    assert len([p for r,p in model.calls if r=='knowledge'])==2  # independent read retained
    assert len([p for r,p in model.calls if r=='analyst'])==4


def test_aggregate_budget_includes_workers_and_stops_before_new_effect(database):
    result=run(database,budget_factory=lambda:Budget(max_model_calls=2))
    assert result.status=='budget_exceeded' and result.model_calls==2
    from work_assistant.workflows.tables import workflows
    with database.engine.connect() as c:assert not c.execute(select(workflows)).first()


def test_model_transient_retry_is_bounded_and_does_not_retry_denial():
    import httpx
    class Retry(CaseModel):
        async def structured(self,role,payload,*args):
            if not self.fired:self.fired=True;raise httpx.ReadTimeout('private provider detail')
            return await super().structured(role,payload,*args)
    result=run(FixtureRepository.load(),'What is the response target?',Retry())
    assert result.status=='answered' and result.model_calls==6


def test_premature_not_found_is_repaired():
    class Premature(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='knowledge' and not self.fired:
                self.fired=True
                return StructuredReply({'result':{'status':'not_found'}})
            return await super().structured(role,payload,*args)
    result=run(FixtureRepository.load(),'What is the response target?',Premature())
    assert result.status=='answered' and result.budget['repairs']==1


@pytest.mark.parametrize('language,expected',[('en','timed out'),('zh','超时')])
def test_persistent_timeout_explains_failure_without_provider_details(language,expected):
    class Unavailable(CaseModel):
        async def structured(self,*args):
            raise TimeoutError('private provider detail')
    agent=CollaborationAgent(KnowledgeService(FixtureRepository.load()),Unavailable())
    result=asyncio.run(agent.ask('mei',AgentRequest(question='Investigate INC-105',language=language)))
    assert result.status=='invalid_response' and result.model_calls==2
    assert expected in result.answer and 'private provider detail' not in result.answer


def test_pricing_unknown_and_exact_cached_thinking_cost():
    from work_assistant.runtime.pricing import estimate
    at=datetime(2026,11,16,tzinfo=timezone.utc)
    usage={'prompt_token_count':1000,'candidates_token_count':100,'thoughts_token_count':20,'cached_content_token_count':200,'total_token_count':1120}
    assert estimate('gemini-3.5-flash-lite',usage,at)['cost_usd']=='0.000546'
    assert estimate('unpriced',usage,at)['cost_usd'] is None
    assert estimate('gemini-3.5-flash-lite',usage|{'total_token_count':9999},at)['cost_usd'] is None
    assert estimate('gemini-3.5-flash-lite',usage,datetime(2027,1,1,tzinfo=timezone.utc))['cost_usd'] is None


def test_structured_gemini_roles_have_distinct_prompts_and_no_automatic_tools():
    model=object.__new__(GeminiModel);model.model='test';model.client=AsyncMock()
    model.client.models.generate_content.return_value=types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[types.Part(text='{"decision":"clarify","nodes":[],"terminal":""}')]))])
    result=asyncio.run(model.structured('supervisor',{'question':'which customer'},Plan,'en'))
    assert result.data['decision']=='clarify'
    config=model.client.models.generate_content.call_args.kwargs['config']
    assert config.response_mime_type=='application/json' and not config.tools
    assert 'acyclic' in config.system_instruction and config.automatic_function_calling.disable


def test_numeric_display_and_ticket_identifiers_do_not_invent_quantities():
    from work_assistant.knowledge import DocumentView
    doc=KnowledgeService(FixtureRepository.load()).read('mei','acme-sla','en')
    doc=doc.model_copy(update={'id':'case:INC-105','content':'Recorded proposal amount: 45.00 USD.'})
    claim=Claim(text='For INC-105, the proposed amount is USD 45.',evidence=[Support(source_id=doc.id,version=doc.version,quote=doc.content)])
    assert not validate_claims([claim],{doc.id:doc})
    bad=claim.model_copy(update={'text':'For INC-105, the proposed amount is USD 450.'})
    assert validate_claims([bad],{doc.id:doc})


def test_independent_reads_actually_overlap(database,monkeypatch):
    from threading import Barrier
    from work_assistant.tools import ToolGateway
    original=ToolGateway.execute;barrier=Barrier(2)
    def synchronized(gateway,name,args):
        if name=='read_document':barrier.wait(timeout=3)
        return original(gateway,name,args)
    monkeypatch.setattr(ToolGateway,'execute',synchronized)
    class Parallel(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='supervisor':
                return StructuredReply({'decision':'execute','terminal':'critic','nodes':[
                    {'id':ident,'role':'knowledge','objective':document,'dependencies':[],
                     'tools':['read_document'],'output_schema':'handoff_v1','completion':'evidence_read','max_calls':2}
                    for ident,document in [('left','acme-sla'),('right','inc-104-runbook')]]+[
                    {'id':'critic','role':'critic','objective':'Verify facts','dependencies':['left','right'],
                     'tools':[],'output_schema':'claim_verdicts_v1','completion':'claims_verified'}]})
            if role=='knowledge' and not payload['observations']:return self.tool('read_document',{'document_id':payload['objective']})
            return await super().structured(role,payload,*args)
    result=run(database,'What is the response target?',Parallel())
    assert result.status=='answered' and result.tool_calls==2


def test_citation_without_read_is_repaired_before_final_answer():
    class Premature(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='knowledge' and not self.fired:
                self.fired=True
                return StructuredReply({'result':{'status':'complete','source_ids':['acme-sla'],'claims':[], 'unresolved':[]}})
            return await super().structured(role,payload,*args)
    result=run(FixtureRepository.load(),'What is the response target?',Premature())
    assert result.status=='answered' and result.budget['repairs']==1


def test_durable_replan_replaces_only_read_epoch_and_keeps_final_result_accessible(database):
    class Changing(CaseModel):
        async def structured(self,role,payload,*args):
            reply=await super().structured(role,payload,*args)
            if role=='analyst' and reply.data.get('result') and not self.fired:
                self.fired=True
                from work_assistant.sla.tables import contracts
                with database.engine.begin() as c:c.execute(update(contracts).where(contracts.c.organization_id=='northstar').values(version=2))
            return reply
    model=Changing()
    with TestClient(create_app({'mei':'mei'},database_url=database.url,model_factory=lambda:model,embedded_worker=False)) as client:
        saved=message(client,conversation(client),question='Investigate INC-105 and start review')
        assert saved.status_code==200,saved.text
        data=saved.json();assert data['result']['status']=='answered' and data['result']['budget']['replans']==1
        assert client.get('/v1/runs/'+data['id'],headers=HEADERS).json()['result'] is not None
        assert len(client.get('/v1/runs/'+data['id']+'/plan',headers=HEADERS).json()['revisions'])==2


@pytest.mark.parametrize('mode',['single','supervisor'])
@pytest.mark.parametrize('question,reply,route',[
    ('hello','Hello! What would you like to work on?','conversation'),
    ('can you understan what i m saying?','Yes, I understand you. What would you like help with?','conversation'),
    ('你能理解我说的话吗？','能理解。你可以直接告诉我想解决什么问题。','conversation'),
    ('Help me handle it','Which incident or deal would you like me to investigate?','clarify'),
])
def test_natural_conversation_has_no_business_tools_or_evidence_repair(question,reply,route,mode):
    class Conversational(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='intake':
                self.calls.append((role,payload))
                return StructuredReply({'route':route,'reply':reply})
            return await super().structured(role,payload,*args)
    model=Conversational()
    result=asyncio.run(CollaborationAgent(KnowledgeService(FixtureRepository.load()),model).ask(
        'mei',AgentRequest(question=question,mode=mode,language='zh' if '你' in question else 'en')))
    assert result.answer==reply
    assert result.status==('needs_clarification' if route=='clarify' else 'answered')
    assert result.tool_calls==0 and result.model_calls==2 and result.budget['repairs']==0
    assert result.sources==[] and result.verification is None
    assert [r for r,_ in model.calls]==['intake','conversation_guard']


@pytest.mark.parametrize('mode',['single','supervisor'])
def test_previous_question_is_chat_recall_not_an_investigation(mode):
    agent=CollaborationAgent(KnowledgeService(FixtureRepository.load()),CaseModel())
    history=[{'question':'Hello','answer':'Hi'},{'question':'Start a review for INC-105','answer':'private reply'}]
    result=asyncio.run(agent.ask('mei',AgentRequest(question='? what did I asked before?',mode=mode),history))
    assert result.status=='answered' and result.answer=='Your previous message was: “Start a review for INC-105”'
    assert result.tool_calls==result.model_calls==result.budget['repairs']==0


def test_missing_previous_question_is_not_invented():
    result=asyncio.run(CollaborationAgent(KnowledgeService(FixtureRepository.load()),CaseModel()).ask(
        'mei',AgentRequest(question='我刚才问了什么？',language='zh')))
    assert result.status=='not_found' and '没有' in result.answer and result.model_calls==0


def test_durable_history_recall_uses_only_the_current_conversation(database):
    with TestClient(create_app({'mei':'mei'},database_url=database.url,model_factory=CaseModel,embedded_worker=False)) as client:
        chat=conversation(client)
        question='What is the response target?'
        assert message(client,chat,question=question).json()['result']['status']=='answered'
        recall='? what did I asked before?'
        result=message(client,chat,question=recall,key='recall').json()['result']
        assert result['answer']==f'Your previous message was: “{question}”'
        assert result['model_calls']==result['tool_calls']==0
        other=message(client,conversation(client),question=recall,key='other-chat').json()['result']
        assert other['status']=='not_found'


def test_intake_can_route_paraphrased_recall_without_investigation():
    class Recall(CaseModel):
        async def structured(self,role,*args):
            assert role=='intake'
            return StructuredReply({'route':'recall_question'})
    history=[{'question':'Hello','answer':'Hi'}]
    result=asyncio.run(CollaborationAgent(KnowledgeService(FixtureRepository.load()),Recall()).ask(
        'mei',AgentRequest(question='Remind me of my last message please'),history))
    assert result.answer=='Your previous message was: “Hello”' and result.model_calls==1 and not result.tool_calls


def test_conversation_cannot_bypass_business_verification_and_followup_context():
    class Misrouted(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='intake':return StructuredReply({'route':'conversation','reply':'The SLA guarantees resolution in 30 minutes.'})
            if role=='conversation_guard':return StructuredReply({'safe':False,'reason':'Unsupported contract assertion.'})
            return await super().structured(role,payload,*args)
    model=Misrouted()
    history=[{'question':'Which customer?','answer':'Acme'}]
    result=asyncio.run(CollaborationAgent(KnowledgeService(FixtureRepository.load()),model).ask(
        'mei',AgentRequest(question='What is the response target?',mode='single'),history))
    assert result.status=='answered' and '60 minutes' in result.answer and '30 minutes' not in result.answer
    assert result.verification and result.tool_calls>0
    assert next(p for r,p in model.calls if r=='single')['history']==history


def test_repair_stop_explains_actual_limit():
    class Unsupported(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='critic':return StructuredReply({'verdicts':[{'index':c['index'],'verdict':'unsupported','reason':'Insufficient support.'} for c in payload['claims']]})
            return await super().structured(role,payload,*args)
    result=run(FixtureRepository.load(),'What is the response target?',Unsupported())
    assert result.status=='budget_exceeded' and 'two correction attempts' in result.answer
    assert result.budget['limit_reached']=='repair budget'
    assert 'narrow' not in result.answer


def test_exact_case_counts_are_tenant_scoped_uncapped_and_fresh(database):
    import json
    from sqlalchemy import insert
    from work_assistant.sla.tables import tickets
    from work_assistant.tools import ToolGateway
    from work_assistant.verification.claims import current_sources, EvidenceChanged
    knowledge=KnowledgeService(database)
    gateway=ToolGateway(knowledge,'mei','en')
    initial=gateway.execute('count_workspace_cases',{})
    assert initial['ok']
    data=json.loads(initial['document']['content'])
    assert data['sla_cases']==2 and data['deal_requests']==2 and data['combined_records']==4
    outside=json.loads(ToolGateway(knowledge,'outsider','en').execute('count_workspace_cases',{})['document']['content'])
    assert outside['sla_cases']==1 and outside['deal_requests']==1
    assert not gateway.execute('count_workspace_cases',{'employee_id':'outsider'})['ok']
    with database.engine.begin() as c:
        template=dict(c.execute(select(tickets).where(tickets.c.organization_id=='northstar')).mappings().first())
        c.execute(insert(tickets),[template|{'id':f'INC-COUNT-{i}'} for i in range(110)])
    changed=json.loads(gateway.execute('count_workspace_cases',{})['document']['content'])
    assert changed['sla_cases']==112 and changed['combined_records']==114
    with pytest.raises(EvidenceChanged):
        current_sources(knowledge,'mei',{'case-summary':knowledge.read('mei','case-summary','en').model_copy(update={'content':initial['document']['content']})})


@pytest.mark.parametrize('mode',['single','supervisor'])
def test_count_plan_uses_aggregate_evidence_without_specific_case(database,mode):
    import json
    class Counting(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='supervisor':
                return StructuredReply({'nodes':[
                    {'id':'counts','role':'analyst','objective':'Count current records','tools':['count_workspace_cases'],
                     'output_schema':'handoff_v1','completion':'calculation_checked'},
                    {'id':'critic','role':'critic','objective':'Check counts','dependencies':['counts'],
                     'output_schema':'claim_verdicts_v1','completion':'claims_verified'}],'terminal':'critic'})
            if role in ('analyst','single'):
                if not payload['observations']:return self.tool('count_workspace_cases',{})
                doc=payload['observations'][0]['result']['document']
                data=json.loads(doc['content'])
                return StructuredReply({'result':{'source_ids':[doc['id']],'claims':[{
                    'text':f"Our organization has {data['sla_cases']} SLA cases and {data['deal_requests']} deal requests.",
                    'evidence':[{'source_id':doc['id'],'version':doc['version'],'quote':doc['content']}]}]}})
            return await super().structured(role,payload,*args)
    result=asyncio.run(CollaborationAgent(KnowledgeService(database),Counting()).ask(
        'mei',AgentRequest(question='how many cases do we have now',mode=mode)))
    assert result.status=='answered',result
    assert '2 SLA cases' in result.answer and result.sources[0].id=='case-summary'
    assert result.tool_calls==1 and result.verification


@pytest.mark.parametrize('mode',['single','supervisor'])
def test_accepted_read_offer_resolves_task_and_reads_without_mutation(database,mode):
    class Followup(CaseModel):
        async def structured(self,role,payload,*args):
            if role=='critic' and not self.fired:
                self.fired=True
                return StructuredReply({'verdicts':[{'index':0,'verdict':'uncertain','reason':'Recheck the supporting excerpt.'}]})
            if role=='repair':
                assert payload['question']=='Show the details of existing deal requests.'
                assert payload['original_message']=='yea' and payload['history']
                return StructuredReply(payload['draft'])
            if role=='intake':
                assert payload['question']=='yea' and 'existing deal requests' in payload['history'][-1]['answer']
                return StructuredReply({'route':'investigate','reply':'','resolved_request':'Show the details of existing deal requests.'})
            if role=='supervisor':
                assert payload['question']=='Show the details of existing deal requests.'
                return StructuredReply({'nodes':[
                    {'id':'deals','role':'analyst','objective':'Read existing deals','tools':['list_deals','get_deal'],'max_calls':4,'output_schema':'handoff_v1','completion':'calculation_checked'},
                    {'id':'critic','role':'critic','objective':'Check evidence','dependencies':['deals'],'output_schema':'claim_verdicts_v1','completion':'claims_verified'}],'terminal':'critic'})
            if role in ('analyst','single'):
                assert payload['original_message']=='yea' and payload['question']=='Show the details of existing deal requests.'
                if not payload['observations']:return self.tool('list_deals',{})
                if len(payload['observations'])==1:return self.tool('get_deal',{'deal_id':payload['observations'][0]['result']['deals'][0]['id']})
                doc=payload['observations'][0]['result']['document']
                return StructuredReply({'result':{'source_ids':[doc['id']],'claims':[{'text':'There are 2 deal requests in this organization.',
                    'evidence':[{'source_id':doc['id'],'version':doc['version'],'quote':doc['content']}]}]}})
            return await super().structured(role,payload,*args)
    history=[{'question':"What's a deal request?",'answer':'Would you like to review the details of the existing deal requests?'}]
    result=asyncio.run(CollaborationAgent(KnowledgeService(database),Followup()).ask('mei',AgentRequest(question='yea',mode=mode),history))
    assert result.status=='answered',result
    assert [t.tool for t in result.tool_trace]==['list_deals','get_deal']
    assert result.verification and all(t.risk_tier==0 for t in result.tool_trace)


def test_listing_provenance_is_scoped_and_detects_changed_records(database):
    from work_assistant.tools import ToolGateway
    from work_assistant.verification.claims import current_sources,EvidenceChanged
    from work_assistant.deals.tables import requests
    knowledge=KnowledgeService(database);gateway=ToolGateway(knowledge,'mei','en')
    listing=gateway.execute('list_deals',{})
    assert listing['ok'] and listing['document']['id']=='deal-list'
    assert {r['id'] for r in listing['deals']}=={'DEAL-101','DEAL-102'}
    assert 'DEAL-101' not in ToolGateway(knowledge,'outsider','en').execute('list_deals',{})['document']['content']
    with database.engine.begin() as c:c.execute(update(requests).where(requests.c.organization_id=='northstar',requests.c.id=='DEAL-101').values(title='Changed title',version=2))
    with pytest.raises(EvidenceChanged):current_sources(knowledge,'mei',gateway.evidence)


def test_percentage_display_conversion_preserves_numeric_gate():
    doc=KnowledgeService(FixtureRepository.load()).read('mei','acme-sla','en').model_copy(update={'content':'"requested_discount": "0.1000"'})
    support=Support(source_id=doc.id,version=doc.version,quote=doc.content)
    assert not validate_claims([Claim(text='The proposed discount is 10%.',evidence=[support])],{doc.id:doc})
    for text in ('The proposed discount is 30%.','The proposed amount is 10 USD.'):
        assert validate_claims([Claim(text=text,evidence=[support])],{doc.id:doc})
