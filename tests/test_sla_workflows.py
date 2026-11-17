from runtime_helpers import wait_workflow
"""Unit 6: exact resulting state, human decisions, recovery and failure boundaries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, func

from test_persistence import database, conversation, message, HEADERS
from test_sla_cases import edit_body
from work_assistant.api import create_app
from work_assistant.db.models import entities
from work_assistant.db.store import ConflictError
from work_assistant.sla.domain import EditTicket, CreateTicket, EditMemo
from work_assistant.sla import tables as sla
from work_assistant.workflows import tables as t
from work_assistant.workflows.domain import StartWorkflow, DecideApproval
from work_assistant.workflows.service import WorkflowService, digest, now
from work_assistant.workflows.engine import WorkflowEngine, saver


def start(db, ticket='INC-105', key='start', service=None):
    service=service or WorkflowService(db)
    row=service.start('mei',StartWorkflow(ticket_id=ticket,expected_ticket_version=1,request_key=key))
    assert WorkflowEngine(service).drive(row['id'])
    return service, row['id']


def decide(service,wid,decision='approve',**kwargs):
    return service.decide('lin',wid,DecideApproval(approval_id=service.phase(wid)['approval_id'],expected_version=1,request_key='decision',decision=decision,**kwargs))


def counts(db):
    with db.engine.connect() as conn:
        return tuple(conn.execute(select(func.count()).select_from(table)).scalar_one() for table in (t.credits,t.receipts))


def app(db,model=None,**kwargs):
    return create_app({'mei':'mei','lin':'lin','alex':'alex','outside':'outsider'},database_url=db.url,
                      model_factory=lambda:model,**kwargs)


def test_full_sla_approval_exact_effect_receipt_and_closure(database):
    service,wid=start(database)
    proposal=service.get('mei',wid)
    assert proposal['status']=='waiting_for_approval'
    assert proposal['approval']['payload']['amount']=='45.00'
    assert proposal['memo']['version']==1
    assert counts(database)==(0,0)
    assert 'AURORA-PRIVATE-742' not in str(service.get('lin',wid))
    with saver(database.url) as cp:
        state=WorkflowEngine(service).graph(cp).get_state({'configurable':{'thread_id':'northstar-sla-v1:'+wid}})
        assert any(task.interrupts for task in state.tasks)
    decide(service,wid)
    WorkflowEngine(service).drive(wid)
    final=service.get('mei',wid)
    assert final['status']=='completed' and final['outcome']['verified'] is True
    assert final['outcome']['amount']=='45.00' and final['outcome']['customer_message_sent'] is False
    assert final['outcome']['incident_resolved'] is False and counts(database)==(1,1)
    assert final['receipt']['payload_hash']==digest(final['receipt']['payload'])
    assert [e['kind'] for e in final['events']].count('approval_requested')==1
    assert service.sla.detail('mei','INC-105')['ticket']['status']=='investigating'


@pytest.mark.parametrize('fraction,expected', [('0.5','22.50'),('0','0.00'),('1','45.00'),('0.3333','15.00')])
def test_modify_recalculates_and_binds_the_exact_amount(database,fraction,expected):
    service,wid=start(database)
    decide(service,wid,'modify',credit_fraction=fraction,reason='Reviewer reduces sandbox credit')
    WorkflowEngine(service).drive(wid)
    final=service.get('lin',wid)
    assert final['outcome']['amount']==expected and final['outcome']['verified']
    assert final['outcome']['effect']==('sandbox_credit_recorded' if Decimal(expected)>0 else 'no_credit_recorded')
    assert counts(database)==(int(Decimal(expected)>0),1)
    assert final['approval']['decision']=='modify'


def test_rejection_is_terminal_and_does_not_repropose(database):
    service,wid=start(database)
    decide(service,wid,'reject',reason='Need independent monitoring evidence')
    for _ in range(3): WorkflowEngine(service).drive(wid)
    row=service.get('mei',wid)
    assert row['status']=='rejected' and row['outcome']['reason']=='Need independent monitoring evidence'
    assert row['proposal_revision']==1 and counts(database)==(0,0)
    with pytest.raises(ConflictError): service.resume('mei',wid,row['version'])
    _,new_id=start(database,key='explicit-new-review')
    assert new_id!=wid  # A new review requires an explicit new request.


def test_missing_input_checkpoint_persists_and_resumes_same_conversation(database):
    service,wid=start(database,ticket='INC-104')
    before=service.get('mei',wid)
    assert before['status']=='waiting_for_input' and before['approval'] is None
    assert 'incident_ended_at' in before['missing_inputs']
    # Another application instance can read and resume the exact same pending question.
    other=WorkflowService(database)
    ticket=other.sla.detail('mei','INC-104')['ticket']
    body=EditTicket(**edit_body(ticket,incident_started_at='2026-11-15T01:00:00Z',incident_ended_at='2026-11-15T02:00:00Z',
        exclusions=[],eligibility_confirmed=True,prior_credit_amount=None,
        billing_period_start='2026-11-01T00:00:00Z',billing_period_end='2026-12-01T00:00:00Z'))
    other.sla.edit_ticket('mei','INC-104',body)
    other.resume('mei',wid,before['version'])
    WorkflowEngine(other).drive(wid)
    after=other.get('mei',wid)
    assert after['status']=='waiting_for_approval'
    assert after['conversation_id']==before['conversation_id'] and after['approval']['payload']['amount']=='30.00'
    assert after['snapshot']['calculation']['prior_credit_source']=='sandbox_ledger'


def test_duplicate_submission_decision_and_execution_are_idempotent(database):
    service,wid=start(database)
    again=service.start('mei',StartWorkflow(ticket_id='INC-105',expected_ticket_version=1,request_key='start'))
    assert again['id']==wid
    with pytest.raises(ConflictError):
        service.start('mei',StartWorkflow(ticket_id='INC-104',expected_ticket_version=1,request_key='start'))
    decide(service,wid)
    decide(service,wid)
    WorkflowEngine(service).drive(wid)
    decide(service,wid)
    for _ in range(3): WorkflowEngine(service).drive(wid)
    assert counts(database)==(1,1)
    with pytest.raises(ConflictError): decide(service,wid,'reject',reason='Changed decision')
    with pytest.raises(ConflictError): start(database,key='new-paid-case')


def test_cross_tenant_wrong_reviewer_and_self_approval_are_denied(database):
    service,wid=start(database)
    for actor in ['outsider','alex']:
        with pytest.raises(LookupError): service.get(actor,wid)
        with pytest.raises(LookupError): service.decide(actor,wid,DecideApproval(approval_id=service.phase(wid)['approval_id'],expected_version=1,request_key='x',decision='approve'))
    with pytest.raises(PermissionError):
        service.decide('mei',wid,DecideApproval(approval_id=service.phase(wid)['approval_id'],expected_version=1,request_key='x',decision='approve'))
    with pytest.raises(PermissionError): service.resume('lin',wid,service.get('lin',wid)['version'])
    assert counts(database)==(0,0)


@pytest.mark.parametrize('change', ['ticket','policy','memo','source','reviewer_role','requester_inactive','expired'])
def test_material_change_invalidates_pending_approval(database,change):
    service,wid=start(database)
    row=service.get('mei',wid)
    with database.engine.begin() as conn:
        if change=='ticket':
            conn.execute(update(sla.tickets).where(sla.tickets.c.id=='INC-105').values(title='Changed incident facts',version=2))
        elif change=='policy':
            conn.execute(update(sla.policies).where(sla.policies.c.id=='acme-gold-v1').values(credit_rate_per_minute=Decimal('.0004'),version=2))
        elif change=='memo':
            conn.execute(update(sla.memos).where(sla.memos.c.id==row['memo_id']).values(version=2))
        elif change=='source':
            doc=database.document('acme-sla')
            doc=doc.model_copy(update={'version':doc.version+1})
            conn.execute(update(entities['documents']).where(entities['documents'].c.id=='acme-sla').values(payload=doc.model_dump(mode='json')))
        elif change in ('reviewer_role','requester_inactive'):
            eid='lin' if change=='reviewer_role' else 'mei'
            employee=database.employee(eid).model_copy(update={'team_ids':()} if eid=='lin' else {'active':False})
            conn.execute(update(entities['employees']).where(entities['employees'].c.id==eid).values(payload=employee.model_dump(mode='json')))
        else:
            conn.execute(update(t.approvals).where(t.approvals.c.id==row['approval']['id']).values(expires_at=now()-timedelta(seconds=1)))
    with pytest.raises((ConflictError,PermissionError,LookupError)):
        decide(service,wid)
    assert counts(database)==(0,0)
    assert service.phase(wid)['status'] in ('waiting_for_input','waiting_for_approval')


def test_approved_but_changed_case_requires_fresh_review(database):
    service,wid=start(database)
    decide(service,wid)
    ticket=service.sla.detail('mei','INC-105')['ticket']
    service.sla.edit_ticket('mei','INC-105',EditTicket(**edit_body(ticket,evidence_note='New confirmed monitoring data')))
    WorkflowEngine(service).drive(wid)
    row=service.get('mei',wid)
    assert row['status']=='waiting_for_input' and row['approval']['status']=='stale' and counts(database)==(0,0)
    service.resume('mei',wid,row['version'])
    WorkflowEngine(service).drive(wid)
    fresh=service.get('mei',wid)
    assert fresh['proposal_revision']==2 and fresh['approval']['status']=='pending'
    assert counts(database)==(0,0)


def test_cancellation_prevents_future_effects(database):
    service,wid=start(database)
    decide(service,wid)
    row=service.get('mei',wid)
    service.cancel('mei',wid,row['version'])
    WorkflowEngine(service).drive(wid)
    assert service.get('mei',wid)['status']=='cancelled' and counts(database)==(0,0)


def test_cancellation_after_commit_requires_receipt_review(database):
    service,wid=start(database)
    decide(service,wid)
    service.execute(wid)
    row=service.get('mei',wid)
    with pytest.raises(ConflictError): service.cancel('mei',wid,row['version'])
    service.verify(wid)
    assert service.get('mei',wid)['status']=='completed' and counts(database)==(1,1)


def test_process_death_after_write_recovers_without_duplicate_effect_or_memo(database):
    service,wid=start(database)
    decide(service,wid)
    code='''import os
from work_assistant.db.store import Database
from work_assistant.workflows.service import WorkflowService
from work_assistant.workflows.engine import WorkflowEngine
db=Database(os.environ['WORKFLOW_TEST_URL'])
def crash(step,wid):
    if step=='execute': os._exit(77)
WorkflowEngine(WorkflowService(db),after_step=crash).drive(os.environ['WORKFLOW_TEST_ID'])
'''
    process=subprocess.run([sys.executable,'-c',code],env=os.environ|{'WORKFLOW_TEST_URL':database.url,'WORKFLOW_TEST_ID':wid},timeout=20,capture_output=True)
    assert process.returncode==77, process.stderr.decode()
    assert service.phase(wid)['status']=='verifying' and counts(database)==(1,1)
    WorkflowEngine(WorkflowService(database)).drive(wid)
    row=service.get('mei',wid)
    assert row['status']=='completed' and counts(database)==(1,1)
    assert [e['kind'] for e in row['events']].count('approval_requested')==1


def test_two_workers_and_concurrent_decisions_do_not_duplicate(database):
    service,wid=start(database)
    def review(_):
        try:
            decide(service,wid)
            return True
        except ConflictError: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(review,range(2)))  # Identical decision returns the persisted result.
        list(pool.map(lambda _: WorkflowEngine(service).drive(wid),range(2)))
    assert counts(database)==(1,1) and service.get('mei',wid)['outcome']['verified']


def test_monthly_cap_comes_from_ledger_and_second_approval_becomes_stale(database):
    service,first=start(database)
    case=service.sla.detail('mei','INC-105')['ticket']
    body={k:v for k,v in case.items() if k in CreateTicket.model_fields}
    body.update(id='INC-106',reported_at='2026-11-16T01:00:00Z',first_response_at=None,
                incident_started_at='2026-11-16T01:00:00Z',incident_ended_at='2026-11-16T03:00:00Z',exclusions=[],prior_credit_amount='999.00')
    service.sla.create_ticket('mei',CreateTicket(**body))
    _,second=start(database,ticket='INC-106',key='second')
    assert service.get('mei',second)['approval']['payload']['amount']=='50.00'  # Ignores attested 999.
    decide(service,first); WorkflowEngine(service).drive(first)
    with pytest.raises(ConflictError): decide(service,second)
    row=service.get('mei',second)
    service.resume('mei',second,row['version']); WorkflowEngine(service).drive(second)
    assert service.get('mei',second)['approval']['payload']['amount']=='5.00'
    decide(service,second); WorkflowEngine(service).drive(second)
    with database.engine.connect() as conn:
        assert conn.execute(select(func.sum(t.credits.c.amount))).scalar_one()==Decimal('50.00')


def test_transient_recovery_is_bounded_and_reuses_prepared_work(database):
    service,wid=start(database)
    decide(service,wid)
    original=service.execute
    def timeout(_): raise TimeoutError('simulated transient')
    service.execute=timeout
    WorkflowEngine(service).drive(wid)
    row=service.get('mei',wid)
    assert row['status']=='retry_scheduled' and counts(database)==(0,0)
    with pytest.raises(ConflictError): service.resume('mei',wid,row['version'])
    service.clock=lambda:now()+timedelta(seconds=10)
    service.execute=original
    service.resume('mei',wid,row['version']); WorkflowEngine(service).drive(wid)
    assert service.get('mei',wid)['status']=='completed' and counts(database)==(1,1)


def test_failed_readback_reports_partial_and_never_reissues(database):
    service,wid=start(database)
    decide(service,wid); service.execute(wid)
    with database.engine.begin() as conn:
        conn.execute(update(t.credits).where(t.credits.c.workflow_id==wid).values(amount=Decimal('44.00')))
    service.verify(wid)
    row=service.get('mei',wid)
    assert row['status']=='partial' and not row['outcome']['verified']
    WorkflowEngine(service).drive(wid)
    assert counts(database)==(1,1)


def test_tampered_approved_payload_fails_before_effect(database):
    service,wid=start(database)
    decide(service,wid)
    with database.engine.begin() as conn:
        approval=service._approval(conn,service._row(conn,wid))
        payload=dict(approval['approved_payload'],amount='1000.00')
        conn.execute(update(t.approvals).where(t.approvals.c.id==approval['id']).values(approved_payload=payload))
    WorkflowEngine(service).drive(wid)
    assert service.get('mei',wid)['status']=='failed' and counts(database)==(0,0)


def test_http_review_restart_identity_and_validation(database):
    with TestClient(app(database)) as client:
        response=client.post('/v1/sla-workflows',headers=HEADERS,json={'ticket_id':'INC-105','expected_ticket_version':1,'request_key':'http'})
        assert response.status_code==202,response.text
        wid=response.json()['id']
        assert wait_workflow(client,'/v1/sla-workflows/'+wid,HEADERS)['status']=='waiting_for_approval'
    with TestClient(app(database)) as client:
        path='/v1/sla-workflows/'+wid
        assert client.get(path,headers={'Authorization':'Bearer outside'}).status_code==404
        approval_id=client.get(path,headers=HEADERS).json()['approval']['id']
        invalid={'approval_id':approval_id,'expected_version':1,'request_key':'review','decision':'modify','credit_fraction':'1.1','reason':'too much'}
        assert client.post(path+'/decision',headers={'Authorization':'Bearer lin'},json=invalid).status_code==422
        body={'approval_id':approval_id,'expected_version':1,'request_key':'review','decision':'approve'}
        assert client.post(path+'/decision',headers=HEADERS,json=body).status_code==403
        assert client.post(path+'/decision',headers={'Authorization':'Bearer lin'},json=body).status_code==202
        result=wait_workflow(client,path,HEADERS,'completed')
        assert result['status']=='completed' and result['receipt']['result']['amount']=='45.00'
        assert client.get('/v1/memos/'+result['memo_id'],headers={'Authorization':'Bearer lin'}).status_code==404


def test_conversation_tool_starts_bound_workflow_without_model_approval(database,tmp_path):
    from test_agent import ScriptedModel, turn, call, final
    class Model(ScriptedModel):
        async def turn(self,contents,language):
            if not self.requests: return await super().turn(contents,language)
            response=contents[-1].parts[0].function_response.response
            return final('The case request was accepted. Open SLA cases for its current review status.',[response['document']['id']])
    model=Model([turn(call('start_sla_workflow',{'ticket_id':'INC-105','expected_ticket_version':1}))])
    with TestClient(app(database,model,artifact_root=tmp_path)) as client:
        cid=conversation(client)
        result=message(client,cid,question='Submit INC-105 for human review')
        assert result.status_code==200,result.text
        assert result.json()['result']['status']=='answered'
        workflows=client.get('/v1/sla-workflows',headers=HEADERS).json()['workflows']
        assert len(workflows)==1 and workflows[0]['conversation_id']==cid
        assert wait_workflow(client,'/v1/sla-workflows/'+workflows[0]['id'],HEADERS)['status']=='waiting_for_approval' and counts(database)==(0,0)


def test_old_decision_cannot_approve_a_replacement_proposal_with_same_version(database):
    service,wid=start(database)
    row=service.get('mei',wid)
    old_body=DecideApproval(approval_id=row['approval']['id'],expected_version=1,request_key='old-browser',decision='approve')
    ticket=service.sla.detail('mei','INC-105')['ticket']
    service.sla.edit_ticket('mei','INC-105',EditTicket(**edit_body(ticket,evidence_note='New evidence for a replacement proposal')))
    service.resume('mei',wid,row['version']); WorkflowEngine(service).drive(wid)
    current=service.get('mei',wid)
    assert current['approval']['version']==1 and current['approval']['id']!=old_body.approval_id
    with pytest.raises(ConflictError): service.decide('lin',wid,old_body)
    assert counts(database)==(0,0) and service.get('mei',wid)['status']=='waiting_for_approval'


def test_transient_retry_limit_stops_repeated_failures(database):
    service,wid=start(database)
    decide(service,wid)
    def timeout(_): raise TimeoutError('simulated')
    service.execute=timeout
    for attempt in range(3):
        service.clock=lambda attempt=attempt:now()+timedelta(seconds=attempt*20)
        WorkflowEngine(service).drive(wid)
    assert service.phase(wid)['status']=='failed' and service.phase(wid)['attempts']==3
    assert counts(database)==(0,0)


def test_no_independent_reviewer_pauses_instead_of_self_approving(database):
    service=WorkflowService(database)
    row=service.start('lin',StartWorkflow(ticket_id='INC-105',expected_ticket_version=1,request_key='ops-self'))
    WorkflowEngine(service).drive(row['id'])
    result=service.get('lin',row['id'])
    assert result['status']=='waiting_for_input' and result['reviewer_id'] is None
    assert result['missing_inputs']==['separate_operations_reviewer'] and counts(database)==(0,0)


def test_due_retry_recovers_without_a_second_human_decision(database):
    service,wid=start(database)
    decide(service,wid)
    original=service.execute
    calls=0
    def transient_once(workflow_id):
        nonlocal calls
        calls+=1
        if calls==1: raise TimeoutError('one temporary error')
        return original(workflow_id)
    service.execute=transient_once
    WorkflowEngine(service).drive(wid)
    assert service.phase(wid)['status']=='retry_scheduled'
    service.clock=lambda:now()+timedelta(seconds=10)
    WorkflowEngine(service).drive(wid)
    assert service.get('mei',wid)['status']=='completed' and counts(database)==(1,1)
