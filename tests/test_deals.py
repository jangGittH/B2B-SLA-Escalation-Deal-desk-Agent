from runtime_helpers import wait_workflow
"""Unit 7 business-state checks; isolated real PostgreSQL, no live CRM/model calls."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import os, subprocess, sys
import pytest
from sqlalchemy import select, update, func
from fastapi.testclient import TestClient
from test_persistence import database
from work_assistant.api import create_app
from work_assistant.db.store import ConflictError
from work_assistant.db.models import entities
from work_assistant.sla import tables as sla
from work_assistant.workflows import tables as shared
from work_assistant.workflows.engine import WorkflowEngine
from work_assistant.workflows.service import WorkflowService, digest, now
from work_assistant.deals import tables as t
from work_assistant.deals.domain import StartDealWorkflow, DecideDeal, EditDeal, CreateDeal, EditCost, PricingPolicy, Deal
from work_assistant.deals.workflow import DealWorkflowService
from work_assistant.deals.service import DealService
from work_assistant.deals.calculator import calculate


def start(db,deal='DEAL-101',key='start'):
    s=DealWorkflowService(db)
    row=s.start('alex',StartDealWorkflow(deal_id=deal,expected_deal_version=1,request_key=key))
    assert WorkflowEngine(s).drive(row['id'])
    return s,row['id']


def decide(s,wid,decision='approve',**kw):
    return s.decide('lin',wid,DecideDeal(approval_id=s.phase(wid)['approval_id'],expected_version=1,request_key='decide',decision=decision,**kw))


def edits(row,**kw):
    return EditDeal(**({k:row[k] for k in ('title','requested_discount','renewal_end','evidence_note')}|
        {'expected_version':row['version'],'change_reason':'Updated confirmed commercial request'}|kw))


def count(db):
    with db.engine.connect() as c:
        return c.execute(select(func.count()).select_from(t.effects)).scalar_one()


def test_approved_renewal_exact_contract_memo_receipt_and_sla_unchanged(database):
    s,w=start(database); before=s.get('alex',w)
    assert before['status']=='waiting_for_approval'
    assert before['approval']['payload']['amount']=='900.00'
    assert before['approval']['payload']['margin_fraction']=='0.333333'
    assert 'Deal review memo' in before['memo']['content']
    decide(s,w); assert WorkflowEngine(s).drive(w)
    final=s.get('lin',w)
    assert final['status']=='completed' and final['outcome']['verified']
    assert final['outcome']['contract_version']==2 and not final['outcome']['real_crm_updated']
    with database.engine.connect() as c:
        contract=c.execute(select(t.sandbox_contracts).where(t.sandbox_contracts.c.organization_id=='northstar')).mappings().one()
        source=c.execute(select(sla.contracts).where(sla.contracts.c.organization_id=='northstar')).mappings().one()
    assert contract['monthly_fee']==Decimal('900') and contract['ends_at'].year==2028
    assert source['monthly_fee']==Decimal('1000') and source['version']==1 and source['ends_at'].year==2027
    assert count(database)==1


@pytest.mark.parametrize('discount,fee',[('0','1000.00'),('0.05','950.00'),('0.10','900.00'),('0.0333','966.70')])
def test_modified_discount_recalculates_exact_fee(database,discount,fee):
    s,w=start(database); a=s.get('lin',w)['approval']
    preview=s.preview('lin',w,a['id'],1,Decimal(discount))
    assert preview['amount']==fee
    decide(s,w,'modify',discount_fraction=discount,reason='Reduce commercial concession')
    WorkflowEngine(s).drive(w)
    assert s.get('lin',w)['outcome']['amount']==fee and count(database)==1


def test_reviewer_cannot_increase_discount_or_forge_action(database):
    s,w=start(database)
    with pytest.raises(ConflictError): decide(s,w,'modify',discount_fraction='0.15',reason='Increase')
    assert count(database)==0
    decide(s,w)
    with database.engine.begin() as c:
        approval=c.execute(select(shared.approvals).where(shared.approvals.c.workflow_id==w)).mappings().one()
        payload=approval['approved_payload']|{'amount':'1.00'}
        c.execute(update(shared.approvals).where(shared.approvals.c.id==approval['id']).values(approved_payload=payload,approved_hash=digest(payload)))
    assert not WorkflowEngine(s).drive(w)
    assert count(database)==0 and s.phase(w)['status']=='failed'


def test_policy_exception_waits_then_owner_edit_resumes(database):
    s,w=start(database,deal='DEAL-102')
    row=s.get('alex',w)
    assert row['status']=='waiting_for_input'
    assert {'discount_exceeds_policy','margin_below_policy'}<=set(row['missing_inputs'])
    deal=s.records.detail('alex','DEAL-102')['deal']
    s.records.edit('alex','DEAL-102',edits(deal,requested_discount='0.15'))
    s.resume('alex',w,row['version']); WorkflowEngine(s).drive(w)
    assert s.get('lin',w)['approval']['payload']['amount']=='850.00'


def test_missing_cost_requires_operations_attestation_then_fresh_review(database):
    with database.engine.begin() as c:
        c.execute(update(t.policies).where(t.policies.c.organization_id=='northstar').values(monthly_cost=None,cost_evidence=''))
    s,w=start(database); row=s.get('alex',w)
    assert row['missing_inputs']==['confirmed_policy_cost']
    body=EditCost(expected_version=1,monthly_cost='600',cost_evidence='Synthetic cost attested by Operations',change_reason='Resolve missing basis')
    with pytest.raises(PermissionError): s.records.edit_cost('alex','renewal-standard',body)
    s.records.edit_cost('lin','renewal-standard',body)
    with pytest.raises(ConflictError): s.records.edit_cost('lin','renewal-standard',body)
    s.resume('alex',w,row['version']); WorkflowEngine(s).drive(w)
    assert s.get('lin',w)['status']=='waiting_for_approval'


@pytest.mark.parametrize('change',['request','policy','source_contract','sandbox_contract','memo','expiry','reviewer','source'])
def test_material_changes_block_approved_contract_update(database,change):
    s,w=start(database); decide(s,w)
    with database.engine.begin() as c:
        if change=='request':
            c.execute(update(t.requests).where(t.requests.c.organization_id=='northstar',t.requests.c.id=='DEAL-101').values(version=2,evidence_note='Changed'))
        elif change=='policy':
            c.execute(update(t.policies).where(t.policies.c.organization_id=='northstar').values(version=2,monthly_cost='700'))
        elif change=='source_contract':
            c.execute(update(sla.contracts).where(sla.contracts.c.organization_id=='northstar').values(version=2))
        elif change=='sandbox_contract':
            c.execute(update(t.sandbox_contracts).where(t.sandbox_contracts.c.organization_id=='northstar').values(version=2,monthly_fee='950'))
        elif change=='memo':
            c.execute(update(t.memos).where(t.memos.c.id==s.phase(w)['memo_id']).values(version=2))
        elif change=='expiry':
            c.execute(update(shared.approvals).where(shared.approvals.c.workflow_id==w).values(expires_at=now()-timedelta(seconds=1)))
        elif change=='source':
            doc=database.document('operating-guide'); data=doc.model_copy(update={'version':doc.version+1}).model_dump(mode='json')
            c.execute(update(entities['documents']).where(entities['documents'].c.id==doc.id).values(payload=data))
        else:
            actor=database.employee('lin').model_copy(update={'team_ids':()})
            c.execute(update(entities['employees']).where(entities['employees'].c.id=='lin').values(payload=actor.model_dump(mode='json')))
    WorkflowEngine(s).drive(w)
    assert count(database)==0
    assert s.phase(w)['status'] in ('waiting_for_input','failed')


def test_identity_owner_tenant_and_workflow_kind_are_enforced(database):
    s,w=start(database)
    for who in ('mei','outsider'):
        with pytest.raises(LookupError): s.get(who,w)
    body=StartDealWorkflow(deal_id='DEAL-101',expected_deal_version=1,request_key='unauthorized')
    with pytest.raises(PermissionError): s.start('mei',body)
    with pytest.raises(LookupError): s.start('outsider',body)
    with pytest.raises(LookupError): WorkflowService(database).get('alex',w)
    with pytest.raises(PermissionError):
        s.decide('alex',w,DecideDeal(approval_id=s.phase(w)['approval_id'],expected_version=1,request_key='self',decision='approve'))
    assert WorkflowService(database).list('alex')==[] and count(database)==0


def test_rejection_and_cancel_do_not_apply_contract(database):
    s,w=start(database); decide(s,w,'reject',reason='Commercial evidence insufficient')
    WorkflowEngine(s).drive(w)
    assert s.get('alex',w)['outcome']['reason']=='Commercial evidence insufficient'
    with pytest.raises(ConflictError): s.resume('alex',w,s.phase(w)['version'])
    s,w=start(database,key='explicit-new'); row=s.phase(w)
    s.cancel('alex',w,row['version']); WorkflowEngine(s).drive(w)
    assert s.phase(w)['status']=='cancelled' and count(database)==0


def test_duplicate_requests_decisions_and_two_drivers_apply_once(database):
    s,w=start(database)
    again=s.start('alex',StartDealWorkflow(deal_id='DEAL-101',expected_deal_version=1,request_key='start'))
    assert again['id']==w
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:decide(s,w),range(2)))
        list(pool.map(lambda _:WorkflowEngine(s).drive(w),range(2)))
    assert count(database)==1 and s.phase(w)['status']=='completed'
    with pytest.raises(ConflictError): s.start('alex',StartDealWorkflow(deal_id='DEAL-101',expected_deal_version=1,request_key='new'))


def test_two_renewals_on_same_contract_invalidate_second_approval(database):
    service=DealService(database)
    service.create('alex',CreateDeal(id='DEAL-SECOND',title='Second renewal',contract_id='acme-2026',pricing_policy_id='renewal-standard',
        requested_discount='0.05',renewal_end='2028-01-01T00:00:00Z'))
    s,w=start(database); _,second=start(database,'DEAL-SECOND','second')
    decide(s,w); decide(s,second); WorkflowEngine(s).drive(w); WorkflowEngine(s).drive(second)
    assert s.phase(second)['status']=='waiting_for_input' and count(database)==1


def test_crash_after_commit_recovers_without_second_contract_revision(database):
    s,w=start(database); decide(s,w)
    script='''import os
from work_assistant.db.store import Database
from work_assistant.deals.workflow import DealWorkflowService
from work_assistant.workflows.engine import WorkflowEngine
db=Database(os.environ['DEAL_TEST_URL'])
def crash(name,wid):
    if name=='execute': os._exit(77)
WorkflowEngine(DealWorkflowService(db),after_step=crash).drive(os.environ['DEAL_TEST_ID'])
'''
    result=subprocess.run([sys.executable,'-c',script],env=os.environ|{'DEAL_TEST_URL':database.url,'DEAL_TEST_ID':w},capture_output=True,timeout=20)
    assert result.returncode==77,result.stderr.decode()
    assert count(database)==1
    assert WorkflowEngine(DealWorkflowService(database)).drive(w)
    assert s.get('alex',w)['outcome']['contract_version']==2 and count(database)==1


def test_readback_mismatch_is_partial_and_never_reapplies(database):
    s,w=start(database); decide(s,w); assert s.execute(w)=='verify'
    with database.engine.begin() as c:
        c.execute(update(t.sandbox_contracts).where(t.sandbox_contracts.c.organization_id=='northstar').values(version=3,monthly_fee='800'))
    s.verify(w); WorkflowEngine(s).drive(w)
    assert s.phase(w)['status']=='partial' and count(database)==1
    with pytest.raises(ConflictError): s.cancel('alex',w,s.phase(w)['version'])


def test_http_deal_review_schema_and_restart(database):
    def app(): return create_app({'alex':'alex','lin':'lin','outside':'outsider'},database_url=database.url,model_factory=lambda:None)
    owner={'Authorization':'Bearer alex'};reviewer={'Authorization':'Bearer lin'}
    with TestClient(app()) as c:
        assert c.get('/v1/deals').status_code==401
        assert len(c.get('/v1/deals',headers=owner).json()['deals'])==2
        response=c.post('/v1/deal-workflows',headers=owner,json={'deal_id':'DEAL-101','expected_deal_version':1,'request_key':'http'})
        assert response.status_code==202,response.text
        w=response.json()['id']
    with TestClient(app()) as c:
        a=wait_workflow(c,'/v1/deal-workflows/'+w,reviewer)['approval']
        body={'approval_id':a['id'],'expected_version':1,'decision':'approve','request_key':'http-decision'}
        assert c.post('/v1/deal-workflows/'+w+'/decision',headers=owner,json=body).status_code==403
        assert c.post('/v1/deal-workflows/'+w+'/decision',headers=reviewer,json=body|{'organization_id':'otherco'}).status_code==422
        assert c.post('/v1/deal-workflows/'+w+'/decision',headers=reviewer,json=body).status_code==202
        assert wait_workflow(c,'/v1/deal-workflows/'+w,owner,'completed')['outcome']['verified']


@pytest.mark.parametrize('discount,cost,minimum,expected,status',[
    ('0.20','600','0.25','800.00','ready'),
    ('0.20','600.01','0.25','800.00','missing_inputs'),
    ('0.2001','600','0.25','799.90','missing_inputs'),
    ('0','0','1','1000.00','ready'),
    ('0.10',None,'0.25','900.00','missing_inputs'),
    (None,'600','0.25',None,'missing_inputs'),
])
def test_exact_margin_boundaries(discount,cost,minimum,expected,status):
    policy=PricingPolicy(id='p',organization_id='o',name='Test policy',currency='USD',monthly_list_price='1000',
        monthly_cost=cost,cost_evidence='Attested' if cost is not None else '',maximum_discount='0.20',minimum_margin=minimum,
        effective_from='2026-01-01T00:00:00Z',effective_until='2027-01-01T00:00:00Z')
    deal=Deal(id='d',organization_id='o',title='Test',contract_id='c',pricing_policy_id='p',owner_id='alex',
        requested_discount=discount,renewal_end='2028-01-01T00:00:00Z',updated_at='2026-11-16T00:00:00Z')
    contract={'signed':True,'version':1,'currency':'USD','starts_at':datetime(2026,1,1,tzinfo=timezone.utc)}
    sandbox={'source_contract_version':1,'currency':'USD','ends_at':datetime(2027,1,1,tzinfo=timezone.utc)}
    result=calculate(deal,policy,contract,sandbox,datetime(2026,11,16,tzinfo=timezone.utc))
    assert result['status']==status and result['proposal_amount']==expected


@pytest.mark.parametrize('field,value,missing',[
    ('currency','EUR','matching_currency'),
    ('effective_until',datetime(2026,10,1,tzinfo=timezone.utc),'applicable_pricing_policy'),
    ('renewal_end','2029-01-01T00:00:00+00:00','renewal_end_outside_allowed_extension'),
    ('renewal_end',None,'renewal_end'),
])
def test_missing_terms_and_invalid_policy_never_request_approval(database,field,value,missing):
    with database.engine.begin() as c:
        table=t.requests if field=='renewal_end' else t.policies
        c.execute(update(table).where(table.c.organization_id=='northstar').values(**{field:value}))
    s,w=start(database)
    row=s.get('alex',w)
    assert missing in row['missing_inputs'] and row['approval'] is None and count(database)==0


def test_replaced_approval_id_and_stale_edit_are_rejected(database):
    s,w=start(database); old=s.get('alex',w)['approval']
    row=s.records.detail('alex','DEAL-101')['deal']
    s.records.edit('alex','DEAL-101',edits(row,requested_discount='0.05'))
    with pytest.raises(ConflictError): s.records.edit('alex','DEAL-101',edits(row))
    s.resume('alex',w,s.phase(w)['version']); WorkflowEngine(s).drive(w)
    new=s.get('alex',w)['approval']; assert new['id']!=old['id'] and new['version']==old['version']
    with pytest.raises(ConflictError):
        s.decide('lin',w,DecideDeal(approval_id=old['id'],expected_version=1,request_key='stale',decision='approve'))
    assert count(database)==0


def test_conversation_tools_bind_deal_workflow_and_do_not_approve(database,tmp_path):
    from test_agent import ScriptedModel, turn, call, final
    model=ScriptedModel([turn(call('get_deal',{'deal_id':'DEAL-101'})),
        turn(call('start_deal_workflow',{'deal_id':'DEAL-101','expected_deal_version':1})),
        final('The renewal proposal requires independent review.',['deal:DEAL-101'])])
    app=create_app({'alex':'alex'},database_url=database.url,model_factory=lambda:model,artifact_root=tmp_path)
    with TestClient(app) as c:
        h={'Authorization':'Bearer alex'}
        cid=c.post('/v1/conversations',headers=h,json={'title':'Renewal review'}).json()['id']
        r=c.post(f'/v1/conversations/{cid}/messages',headers=h,json={'question':'Start review for DEAL-101','request_key':'ask'})
        assert r.status_code==200,r.text
        workflows=c.get('/v1/deal-workflows',headers=h).json()['workflows']
        assert len(workflows)==1 and workflows[0]['conversation_id']==cid
        assert wait_workflow(c,'/v1/deal-workflows/'+workflows[0]['id'],h)['status']=='waiting_for_approval' and count(database)==0
    from work_assistant.tools import ToolGateway
    from work_assistant.knowledge import KnowledgeService
    gateway=ToolGateway(KnowledgeService(database),'alex','en')
    assert gateway.execute('update_contract',{})['error']['code']=='APPROVAL_REQUIRED'
    assert gateway.execute('get_deal',{'deal_id':'OTHER-DEAL'})['error']['code']=='NOT_FOUND'
