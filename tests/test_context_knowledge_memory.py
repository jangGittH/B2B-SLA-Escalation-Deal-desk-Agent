"""Unit 9 boundaries: source freshness, scoped retrieval, human memory and bounded context."""
import asyncio
import base64
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import numpy as np
import pytest
from sqlalchemy import select,update,func
from fastapi.testclient import TestClient
from test_persistence import database,HEADERS,conversation,message
from test_agent import ScriptedModel,turn,call,final
from work_assistant.api import create_app
from work_assistant.knowledge import KnowledgeService
from work_assistant.retrieval.ingestion import Ingestion,Upload,extract
from work_assistant.context.offload import OffloadStore,encoded
from work_assistant.context import tables as t
from work_assistant.db.models import entities
from work_assistant.db.store import ConflictError
from work_assistant.memory.service import MemoryService,Proposal,Change
from work_assistant.tools import ToolGateway


def body(content='# Policy\nOriginal recorded terms.',**kw):
    return Upload(title='Uploaded policy',filename='policy.md',content_base64=base64.b64encode(content.encode()).decode(),**kw)

def app(db,model=None):return create_app({'mei':'mei','alex':'alex','lin':'lin','outsider':'outsider'},database_url=db.url,model_factory=lambda:model,embedded_worker=False)

def test_versioned_upload_original_acl_and_index_invalidation(database):
    s=Ingestion(database);k=KnowledgeService(database)
    saved=s.save('mei',body('# Before\nNebulacredit pays three credits.'));ident=saved['id']
    assert k.search('mei','Nebulacredit','en').hits[0].document.id==ident
    assert not k.search('alex','Nebulacredit','en').hits
    assert s.original('mei',ident,1)['original'].startswith(b'# Before')
    with pytest.raises(LookupError):s.original('outsider',ident,1)
    with pytest.raises(ConflictError):s.save('mei',body(expected_version=8),ident)
    s.save('mei',body('# After\nOrbitalcredit requires approval.',expected_version=1),ident)
    assert not k.search('mei','Nebulacredit','en').hits
    hit=k.search('mei','Orbitalcredit','en').hits[0]
    assert hit.document.version==2 and hit.section=='After' and hit.chunk_id
    assert [r['version'] for r in s.history('mei',ident)]==[2,1]
    s.remove('mei',ident,2)
    with pytest.raises(LookupError):k.read('mei',ident,'en')
    assert not k.search('mei','Orbitalcredit','en').hits
    with database.engine.connect() as c:assert not c.execute(select(t.chunks).where(t.chunks.c.document_id==ident)).first()


def test_shared_upload_and_history_do_not_widen_old_revision_acl(database):
    s=Ingestion(database)
    with pytest.raises(PermissionError):s.save('mei',body(scope='tenant'))
    doc=s.save('lin',body('Old private phrase.'))['id']
    s.save('lin',body('New shared phrase.',scope='tenant',expected_version=1),doc)
    assert [r['version'] for r in s.history('alex',doc)]==[2]
    with pytest.raises(LookupError):s.original('alex',doc,1)
    assert s.original('alex',doc,2)


def test_upload_limits_and_docx_tables_and_text_pdf():
    from docx import Document
    from reportlab.pdfgen.canvas import Canvas
    doc=Document();doc.add_heading('Pricing',level=1);table=doc.add_table(rows=1,cols=2);table.cell(0,0).text='Term';table.cell(0,1).text='12 months'
    out=io.BytesIO();doc.save(out)
    assert 'Term | 12 months' in extract('terms.docx',out.getvalue())
    pdf=io.BytesIO();canvas=Canvas(pdf);canvas.drawString(10,100,'Verified text PDF');canvas.save()
    assert 'Verified text PDF' in extract('terms.pdf',pdf.getvalue())
    blank=io.BytesIO();canvas=Canvas(blank);canvas.showPage();canvas.save()
    with pytest.raises(ValueError,match='OCR'):extract('scan.pdf',blank.getvalue())
    for filename,data in [('large.txt',b'x'*2000001),('code.exe',b'no'),('empty.md',b'')]:
        with pytest.raises(ValueError):extract(filename,data)


def test_offload_bounded_pages_filters_expiry_owner_and_changed_source(database):
    ident=Ingestion(database).save('mei',body('# Records\n'+('内部事实 Zebra: line\n'*4000)))['id']
    k=KnowledgeService(database);gateway=ToolGateway(k,'mei','en')
    result=gateway.execute('read_document',{'document_id':ident})
    assert result['offloaded'] and len(encoded(result))<12000 and result['preview']['next_cursor'] is not None
    store=OffloadStore(k,'mei');page=store.read(result['ref'],limit=8,contains='Zebra')
    assert len(encoded(page))<12000 and page['items']
    following=store.read(result['ref'],cursor=page['next_cursor'],limit=8,contains='Zebra')
    assert following['items'][0]!=page['items'][0]
    with pytest.raises(LookupError):OffloadStore(k,'alex').read(result['ref'])
    Ingestion(database).save('mei',body('Replacement',expected_version=1),ident)
    with pytest.raises(LookupError):store.read(result['ref'])
    fresh=gateway.execute('read_document',{'document_id':ident})
    ref=store.save(fresh,'read_document',{'document_id':ident})
    with database.engine.begin() as c:c.execute(update(t.offloads).where(t.offloads.c.id==ref).values(expires_at=time.time()-1))
    with pytest.raises(LookupError):store.read(ref)


def test_offload_source_revocation_denies_owner(database):
    k=KnowledgeService(database);doc=k.read('mei','acme-sla','en');store=OffloadStore(k,'mei')
    ref=store.save({'ok':True,'document':doc.model_dump(mode='json')},'read_document',{})
    current=database.document('acme-sla').model_copy(update={'visibility':'restricted','reader_employee_ids':('lin',)})
    with database.engine.begin() as c:c.execute(update(entities['documents']).where(entities['documents'].c.id=='acme-sla').values(payload=current.model_dump(mode='json')))
    with pytest.raises(LookupError):store.read(ref)


def test_actual_bm25_ranking_bilingual_identifiers_and_no_match(database):
    k=KnowledgeService(database)
    assert k.search('mei','响应时限','en').hits[0].document.id=='acme-sla'
    assert any(h.document.id=='inc-104-runbook' for h in k.search('mei','AURORA-PRIVATE-742','en').hits)
    assert all(h.document.id!='inc-104-runbook' for h in k.search('alex','AURORA-PRIVATE-742','en').hits)
    assert k.search('mei','unicornspaceflight','en').hits==()
    assert k.search('mei','Acme','en').method=='bm25'


def test_dense_and_reranker_never_receive_forbidden_text_and_recheck_version(database):
    from work_assistant.retrieval.search import EMBEDDER,RERANKER
    observed=[]
    class Encoder:
        def encode(self,strings,**kw):
            observed.extend(strings);return np.asarray([[1.,0.] for _ in strings])
    class Reranker:
        def predict(self,pairs,**kw):
            observed.extend(x[1] for x in pairs)
            # Revoke a candidate after ranking input but before returning results.
            original=database.document('acme-sla')
            doc=original.model_copy(update={'version':original.version+1})
            with database.engine.begin() as c:c.execute(update(entities['documents']).where(entities['documents'].c.id=='acme-sla').values(payload=doc.model_dump(mode='json')))
            return np.ones(len(pairs))
    with patch('work_assistant.retrieval.search.models',return_value=(Encoder(),Reranker(),{EMBEDDER:'test',RERANKER:'test'})):
        result=KnowledgeService(database).search('alex','Acme','en',mode='hybrid')
    assert result.method=='bm25_dense_rrf_cross_encoder'
    assert 'AURORA-PRIVATE-742' not in str(observed) and 'OTHER-ORG-SECRET-915' not in str(observed)
    assert all(h.document.id!='acme-sla' for h in result.hits)


def test_missing_dense_models_explicit_bm25_fallback(database):
    with patch('work_assistant.retrieval.search.models',side_effect=OSError('missing')):
        assert KnowledgeService(database).search('mei','Acme','en',mode='hybrid').method=='bm25_models_unavailable'


def confirm(service,actor,row):return service.change(actor,row['id'],Change(expected_version=row['version'],action='confirm'))


def test_preference_pending_confirm_edit_delete_and_cross_tenant(database):
    s=MemoryService(database);row=s.propose('mei',Proposal(value='evidence_first'))
    assert s.propose('mei',Proposal(value='evidence_first'))['id']==row['id']
    assert s.layout('mei')=='overview_first' and s.list('alex')==[] and s.list('outsider')==[]
    with pytest.raises(LookupError):confirm(s,'alex',row)
    row=confirm(s,'mei',row);assert s.layout('mei')=='evidence_first'
    with pytest.raises(ConflictError):s.change('mei',row['id'],Change(expected_version=1,action='delete'))
    row=s.change('mei',row['id'],Change(expected_version=row['version'],action='edit',value='overview_first'))
    assert row['status']=='pending' and s.layout('mei')=='overview_first'
    row=confirm(s,'mei',row)
    s.change('mei',row['id'],Change(expected_version=row['version'],action='delete'))
    assert not s.list('mei')
    with database.engine.connect() as c:assert c.execute(select(t.memories.c.value).where(t.memories.c.id==row['id'])).scalar_one()==''


def test_shared_memory_operations_review_user_override_and_supersession(database):
    s=MemoryService(database);shared=s.propose('mei',Proposal(value='evidence_first',scope='tenant'))
    with pytest.raises(PermissionError):confirm(s,'mei',shared)
    confirm(s,'lin',shared)
    assert s.layout('alex')=='evidence_first' and s.layout('outsider')=='overview_first'
    personal=confirm(s,'alex',s.propose('alex',Proposal(value='overview_first')))
    assert s.layout('alex')=='overview_first'
    replacement=confirm(s,'lin',s.propose('lin',Proposal(value='overview_first',scope='tenant')))
    assert replacement['supersedes']==shared['id']
    assert s.layout('mei')=='overview_first'


def test_team_scope_expiry_and_revoked_membership(database):
    s=MemoryService(database);row=s.propose('lin',Proposal(scope='team',team_id='operations',value='evidence_first'))
    confirm(s,'lin',row);assert s.layout('lin')=='evidence_first' and not s.list('mei')
    with database.engine.begin() as c:c.execute(update(t.memories).where(t.memories.c.id==row['id']).values(expires_at=time.time()-1))
    assert s.list('lin')[0]['stale'] and s.layout('lin')=='overview_first'
    assert 'value' not in s.list('lin')[0]


def test_concurrent_preference_confirmations_leave_one_active(database):
    s=MemoryService(database);a=s.propose('mei',Proposal(value='evidence_first'));b=s.propose('mei',Proposal(value='overview_first'))
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda r:confirm(s,'mei',r),[a,b]))
    with database.engine.connect() as c:assert c.execute(select(func.count()).select_from(t.memories).where(t.memories.c.status=='active')).scalar_one()==1


def test_verified_episode_only_and_policy_version_change_invalidates(database):
    from test_sla_workflows import start,decide
    from work_assistant.workflows.engine import WorkflowEngine
    s=MemoryService(database);workflow,wid=start(database)
    with pytest.raises(ValueError):s.propose('mei',Proposal(kind='episode',source_id='workflow:'+wid))
    decide(workflow,wid);WorkflowEngine(workflow).drive(wid)
    row=confirm(s,'mei',s.propose('mei',Proposal(kind='episode',source_id='workflow:'+wid)))
    assert 'not a confirmed incident root cause' in row['value']
    from work_assistant.workflows.tables import workflows
    with database.engine.begin() as c:c.execute(update(workflows).where(workflows.c.id==wid).values(version=99))
    assert s.list('mei')[0]['stale'] and not s.recall('mei')


def test_confirmed_preference_changes_later_memo_export_only(database,tmp_path):
    from work_assistant.sla.service import SlaService
    from work_assistant.sla.domain import CreateMemo
    from work_assistant.sla.api import export_memo
    from work_assistant.files import ArtifactStore
    service=SlaService(database);files=ArtifactStore(tmp_path)
    memo=service.save_memo('mei','INC-105',CreateMemo(expected_ticket_version=1))
    first=export_memo(service,files,'mei',memo['id'],memo['version'],'md')
    _,path=files.read('mei',first['id'],database=database);before=path.read_text()
    s=MemoryService(database);confirm(s,'mei',s.propose('mei',Proposal(value='evidence_first')))
    second=export_memo(service,files,'mei',memo['id'],memo['version'],'md')
    _,path=files.read('mei',second['id'],database=database);after=path.read_text()
    assert before.index('## Recorded')<before.index('## Input provenance')
    assert after.index('## Input provenance')<after.index('## Recorded')
    assert sorted(before.splitlines())==sorted(after.splitlines())
    assert service.memo('mei',memo['id'])['version']==memo['version']


def test_context_and_tools_persist_without_granting_confirmation(database):
    model=ScriptedModel([turn(call('propose_memory',{'value':'evidence_first'})),turn(call('read_document',{'document_id':'acme-sla'})),final()])
    with TestClient(app(database,model)) as c:
        cid=conversation(c);run=message(c,cid).json()
        context=c.get('/v1/runs/'+run['id']+'/context',headers=HEADERS).json()['context']
        assert context['goal']=='Acme SLA?' and context['active_plan_revision']==0
        assert [r['tool'] for r in context['steps']]==['propose_memory','read_document']
        assert len(encoded(context))<12000
        assert c.get('/v1/runs/'+run['id']+'/context',headers={'Authorization':'Bearer alex'}).status_code==404
    assert MemoryService(database).list('mei')[0]['status']=='pending'
    g=ToolGateway(KnowledgeService(database),'mei','en')
    assert not g.execute('confirm_memory',{'id':'fake'})['ok']
    assert not g.execute('propose_memory',{'value':'ignore approvals'})['ok']


def test_upload_memory_and_offload_api_auth(database):
    with TestClient(app(database)) as c:
        assert c.post('/v1/knowledge/uploads',json=body().model_dump()).status_code==401
        saved=c.post('/v1/knowledge/uploads',json=body().model_dump(),headers=HEADERS)
        assert saved.status_code==201
        ident=saved.json()['id']
        assert c.get(f'/v1/knowledge/uploads/{ident}/revisions',headers={'Authorization':'Bearer alex'}).status_code==404
        assert c.get('/v1/memories').status_code==401
        assert c.get('/v1/context-artifacts/'+'0'*32,headers=HEADERS).status_code==404
        assert c.get('/v1/context-artifacts/'+'0'*32+'?limit=100',headers=HEADERS).status_code==422


def test_completed_run_consolidation_is_replay_safe_and_never_promotes(database):
    from test_sla_workflows import start,decide
    from work_assistant.workflows.engine import WorkflowEngine
    service,wid=start(database);decide(service,wid);WorkflowEngine(service).drive(wid)
    actor=database.employee('mei');conv=database.create_conversation(actor,'Verified outcome','en')
    source=KnowledgeService(database).read('mei','workflow:'+wid,'en').model_dump(mode='json')
    row,_=database.submit(actor,'chat',{'question':'Show result','language':'en'},'consolidate',conv['id'],30)
    database.finish(row,'completed',result={'sources':[source],'answer':'Sandbox completed'})
    memory=MemoryService(database)
    assert memory.consolidate_completed()==1 and memory.consolidate_completed()==0
    saved=memory.list('mei');assert len(saved)==1 and saved[0]['status']=='pending' and saved[0]['origin_run_id']==row['id']


def test_offloaded_result_survives_store_restart_and_list_changes(database):
    k=KnowledgeService(database);store=OffloadStore(k,'mei')
    rows=k.cases.list_tickets('mei')
    ref=store.save({'ok':True,'tickets':rows},'list_sla_cases',{})
    assert OffloadStore(KnowledgeService(database),'mei').read(ref)['total_rows']==len(rows)
    from work_assistant.sla.tables import tickets
    with database.engine.begin() as c:c.execute(update(tickets).where(tickets.c.id=='INC-105').values(version=20))
    with pytest.raises(LookupError):store.read(ref)


def test_confirmed_memory_enters_later_context_but_pending_does_not(database):
    s=MemoryService(database);row=s.propose('mei',Proposal(value='evidence_first'))
    def run(key):
        model=ScriptedModel([final('Which case?',[],'needs_clarification')])
        with TestClient(app(database,model)) as c:message(c,conversation(c),key=key)
        return ''.join(p.text for turn_,_ in model.requests for item in turn_ for p in item.parts if p.text)
    assert 'evidence_first' not in run('pending')
    confirm(s,'mei',row)
    assert 'evidence_first' in run('active')


def test_backup_restores_uploaded_original_context_and_memory(database,tmp_path):
    from work_assistant.runtime.backup import backup,restore
    from work_assistant.db.store import Database
    from sqlalchemy import create_engine
    from uuid import uuid4
    doc=Ingestion(database).save('mei',body('Original archival evidence'))['id']
    memory=MemoryService(database);confirm(memory,'mei',memory.propose('mei',Proposal(value='evidence_first')))
    files=tmp_path/'files';files.mkdir();backup(database.url,files,tmp_path/'backup',quiesced=True,docker=True)
    name='northstar_restore_'+uuid4().hex
    url,_=restore(database.url,tmp_path/'backup',name,tmp_path/'restored',docker=True)
    restored=Database(url)
    try:
        assert Ingestion(restored).original('mei',doc,1)['original']==b'Original archival evidence'
        assert MemoryService(restored).layout('mei')=='evidence_first'
    finally:
        restored.close();admin=create_engine(database.url,isolation_level='AUTOCOMMIT')
        with admin.connect() as c:c.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_inspect_tool_reads_omitted_text_after_large_result(database):
    ident=Ingestion(database).save('mei',body('Recorded fact.\n'*2000+'TAIL-MARKER verified ending.'))['id']
    gateway=ToolGateway(KnowledgeService(database),'mei','en')
    result=gateway.execute('read_document',{'document_id':ident})
    assert result['offloaded']
    inspected=gateway.execute('inspect_artifact',{'ref':result['ref'],'contains':'TAIL-MARKER'})
    assert inspected['ok'] and 'TAIL-MARKER' in str(inspected['items'])
    assert inspected['document_version']==1
    assert not gateway.execute('inspect_artifact',{'ref':result['ref'],'limit':9})['ok']


def test_offload_rejects_payload_changed_before_storage(database):
    knowledge=KnowledgeService(database);store=OffloadStore(knowledge,'mei')
    old=knowledge.read('mei','acme-sla','en').model_dump(mode='json')
    current=database.document('acme-sla')
    doc=current.model_copy(update={'version':current.version+1})
    with database.engine.begin() as c:
        c.execute(update(entities['documents']).where(entities['documents'].c.id=='acme-sla').values(payload=doc.model_dump(mode='json')))
    with pytest.raises(LookupError,match='before storage'):
        store.save({'ok':True,'document':old},'read_document',{})
    rows=knowledge.cases.list_tickets('mei')
    from work_assistant.sla.tables import tickets
    with database.engine.begin() as c:c.execute(update(tickets).where(tickets.c.id=='INC-105').values(version=20))
    with pytest.raises(LookupError,match='before storage'):
        store.save({'ok':True,'tickets':rows},'list_sla_cases',{})


def test_large_tool_output_stays_bounded_in_real_agent_loop(database):
    ident=Ingestion(database).save('mei',body('内部事实 ' * 15000+'TAIL-MARKER'))['id']
    class InspectModel:
        def __init__(self):self.requests=[]
        async def turn(self,contents,language):
            self.requests.append(json.loads(json.dumps([c.model_dump(mode='json',exclude_none=True) for c in contents])))
            responses=[p.function_response for p in contents[-1].parts if p.function_response]
            if not responses:return turn(call('read_document',{'document_id':ident}))
            result=responses[0].response
            if result.get('offloaded'):return turn(call('inspect_artifact',{'ref':result['ref'],'contains':'TAIL-MARKER'}))
            assert result['ok'] and result['items']
            return final('The ending is recorded.',[ident])
        async def aclose(self):pass
    model=InspectModel()
    with TestClient(app(database,model)) as c:
        cid=conversation(c);run=message(c,cid).json()
        assert run['status']=='completed',run
        context=c.get('/v1/runs/'+run['id']+'/context',headers=HEADERS).json()['context']
        assert context['budget']=={'model_calls':3,'tool_calls':6}
        assert context['artifacts']
        assert max(len(encoded(request)) for request in model.requests)<24000
        # A subsequent turn carries pointers, not the offloaded body.
        c.app.state.agent.model=ScriptedModel([final('Which case next?',[],'needs_clarification')])
        follow=message(c,cid,key='follow').json()
        task=c.get('/v1/runs/'+follow['id']+'/context',headers=HEADERS).json()['context']
        assert task['previous']['artifacts']==context['artifacts']


def test_input_budget_stops_before_provider_and_context_is_bounded():
    from work_assistant.agent import KnowledgeAgent,AgentRequest
    from work_assistant.repository import FixtureRepository
    from work_assistant.context.builder import TaskContext
    from work_assistant.context.state import prior_task
    model=ScriptedModel([])
    agent=KnowledgeAgent(KnowledgeService(FixtureRepository.load()),model,max_input_bytes=1000)
    result=asyncio.run(agent.ask('mei',AgentRequest(question='字'*4000)))
    assert result.status=='budget_exceeded' and result.model_calls==0 and not model.requests
    token=prior_task.set({'unresolved':['字'*1500]*8,'entities':['INC-'+'X'*200]*8})
    try:
        task=TaskContext('字'*4000,6,8)
        assert len(encoded(task.data))<=task.MAX_BYTES
    finally:prior_task.reset(token)


def test_nested_sections_retain_parent_and_version():
    from work_assistant.retrieval.index import split
    from work_assistant.repository import FixtureRepository
    from work_assistant.models import BilingualText
    doc=FixtureRepository.load().document('acme-sla').model_copy(update={
        'content':BilingualText(en='# Contract\n## Credits\n### Cap\nMaximum 5 percent.',zh='# 合同\n## 抵扣\n### 上限\n百分之五。')})
    pieces=split(doc)
    assert any(r['section']=='Contract / Credits / Cap' and '5 percent' in r['content'] for r in pieces)
    assert all(r['version']==doc.version for r in pieces)


def test_large_case_table_pages_reconstruct_rows_without_prompt_totals(database):
    from sqlalchemy import insert
    from work_assistant.sla.tables import tickets
    with database.engine.begin() as c:
        template=dict(c.execute(select(tickets).where(tickets.c.id=='INC-104')).mappings().one())
        c.execute(insert(tickets),[template|{'id':f'INC-BULK-{i:03d}','title':'大表案例 '+str(i)} for i in range(100)])
    gateway=ToolGateway(KnowledgeService(database),'mei','en')
    compact=gateway.execute('list_sla_cases',{'query':'大表案例'})
    assert compact['offloaded'] and len(encoded(compact))<12000
    cursor=0;seen=set()
    while cursor is not None:
        page=gateway.execute('inspect_artifact',{'ref':compact['ref'],'cursor':cursor,'limit':8})
        assert page['ok'] and len(encoded(page))<12000
        assert page['total_rows']==100 and 'not a whole-table calculation' in page['scope']
        seen.update(item['row'] for item in page['items']);cursor=page['next_cursor']
    assert len(seen)==100


def test_changed_policy_invalidates_confirmed_episode_memory(database):
    from test_sla_workflows import start,decide
    from work_assistant.workflows.engine import WorkflowEngine
    from work_assistant.sla.tables import policies
    workflow,wid=start(database);decide(workflow,wid);WorkflowEngine(workflow).drive(wid)
    s=MemoryService(database)
    saved=confirm(s,'mei',s.propose('mei',Proposal(kind='episode',source_id='workflow:'+wid)))
    assert s.recall('mei')
    with database.engine.begin() as c:c.execute(update(policies).values(version=policies.c.version+1))
    assert not s.recall('mei') and s.list('mei')[0]['stale']
    fresh=s.propose('mei',Proposal(kind='episode',source_id='workflow:'+wid))
    assert fresh['id']!=saved['id'] and fresh['status']=='pending'
    assert s.propose('mei',Proposal(kind='episode',source_id='workflow:'+wid))['id']==fresh['id']
