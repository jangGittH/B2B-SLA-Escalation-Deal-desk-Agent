"""Unit 8: durable delivery, fair admission, recovery, metrics and real restore."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
import time
import json
from uuid import uuid4
import pytest
from sqlalchemy import select, update, delete, func, create_engine, text
from fastapi.testclient import TestClient
from test_persistence import database
from runtime_helpers import wait_workflow
from work_assistant.api import create_app
from work_assistant.runtime import tables as t
from work_assistant.runtime.queue import Queue, Limits, CapacityError, now
from work_assistant.runtime.worker import Worker
from work_assistant.runtime.provider import ProviderGate
from work_assistant.runtime.api import summary, authorized_events
from work_assistant.workflows import tables as w
from work_assistant.workflows.service import WorkflowService
from work_assistant.workflows.domain import StartWorkflow, DecideApproval
from work_assistant.deals.workflow import DealWorkflowService
from work_assistant.deals.domain import StartDealWorkflow


def config(db,**changes):
    with db.engine.begin() as c:
        old=c.execute(select(t.settings.c.payload)).scalar_one()
        c.execute(update(t.settings).values(payload=Limits.model_validate(old|changes).model_dump()))


def start(db,kind='sla',key='start',actor=None,subject=None):
    s=(WorkflowService if kind=='sla' else DealWorkflowService)(db)
    row=s.start(actor or ('mei' if kind=='sla' else 'alex'),(StartWorkflow(ticket_id=subject or 'INC-105',expected_ticket_version=1,request_key=key)
        if kind=='sla' else StartDealWorkflow(deal_id=subject or 'DEAL-101',expected_deal_version=1,request_key=key)))
    return s,row['id']


def deliver(db):
    q=Queue(db);q.reconcile();return Worker(db).once()


def test_acceptance_is_atomic_and_runs_without_request_process(database):
    app=create_app({'mei':'mei'},database_url=database.url,model_factory=lambda:None,embedded_worker=False)
    with TestClient(app) as c:
        response=c.post('/v1/sla-workflows',headers={'Authorization':'Bearer mei'},json={'ticket_id':'INC-105','expected_ticket_version':1,'request_key':'start'})
        assert response.status_code==202 and response.json()['status']=='queued'
        wid=response.json()['id']
    with database.engine.connect() as c: assert c.execute(select(t.jobs.c.workflow_id)).scalar_one()==wid
    assert deliver(database)
    assert WorkflowService(database).phase(wid)['status']=='waiting_for_approval'


def test_capacity_rejects_without_partial_conversation_or_job_and_duplicates_remain_valid(database):
    config(database,active_total=1,active_per_tenant=1)
    s,wid=start(database)
    with pytest.raises(CapacityError): start(database,kind='deal',key='second')
    assert start(database)[1]==wid
    with database.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(t.jobs)).scalar_one()==1
        assert c.execute(select(func.count()).select_from(w.workflows)).scalar_one()==1
    s.cancel('mei',wid,s.phase(wid)['version']);assert start(database,kind='deal',key='new')[1]!=wid


def test_per_tenant_capacity_does_not_block_other_tenant(database):
    config(database,active_per_tenant=1)
    start(database)
    with pytest.raises(CapacityError): start(database,kind='deal')
    start(database,kind='deal',actor='outsider',subject='OTHER-DEAL',key='other')
    q=Queue(database);a=q.claim('one');b=q.claim('two')
    assert a['organization_id']!=b['organization_id']
    assert q.claim('three') is None


def test_round_robin_and_global_concurrency(database):
    start(database);start(database,kind='deal',key='two')
    start(database,kind='deal',actor='outsider',subject='OTHER-DEAL',key='other')
    config(database,workers_total=1)
    q=Queue(database);a=q.claim('first')
    assert q.claim('other-worker') is None
    with database.engine.begin() as c: c.execute(update(t.jobs).where(t.jobs.c.workflow_id==a['workflow_id']).values(status='idle'))
    b=q.claim('second');assert a['organization_id']!=b['organization_id']


def test_lease_expiry_reclaims_and_old_token_cannot_ack(database):
    start(database);q=Queue(database);a=q.claim('dead-worker')
    with database.engine.begin() as c: c.execute(update(t.jobs).values(lease_until=now()-timedelta(seconds=1)))
    q.reconcile();b=q.claim('replacement')
    assert b['lease_token']!=a['lease_token']
    assert not q.heartbeat(a) and not q.finish(a)
    assert q.heartbeat(b)


def test_bounded_dead_delivery_requires_explicit_owner_recovery(database):
    config(database,max_deliveries=1)
    s,wid=start(database);q=Queue(database);job=q.claim('failed-worker');q.finish(job,'Storage unavailable')
    q.reconcile()
    with database.engine.connect() as c: assert c.execute(select(t.jobs.c.status)).scalar_one()=='dead'
    assert q.claim('another') is None
    s.resume('mei',wid,s.phase(wid)['version'])
    assert deliver(database) and s.phase(wid)['status']=='waiting_for_approval'


def test_ack_does_not_drop_concurrent_decision_generation(database):
    s,wid=start(database);q=Queue(database);job=q.claim('worker')
    from work_assistant.workflows.engine import WorkflowEngine
    WorkflowEngine(s).drive(wid)
    row=s.get('mei',wid)
    s.decide('lin',wid,DecideApproval(approval_id=row['approval']['id'],expected_version=1,request_key='approve',decision='approve'))
    assert q.finish(job)
    with database.engine.begin() as c:
        assert c.execute(select(t.jobs.c.status)).scalar_one()=='pending'
        c.execute(update(t.jobs).values(available_at=now()-timedelta(seconds=1)))
    assert deliver(database) and s.phase(wid)['status']=='completed'


def test_reconciliation_finds_missing_delivery_and_cancellation_stops_pending_job(database):
    s,wid=start(database)
    with database.engine.begin() as c:c.execute(delete(t.jobs))
    Queue(database).reconcile()
    with database.engine.connect() as c:assert c.execute(select(t.jobs.c.workflow_id)).scalar_one()==wid
    s.cancel('mei',wid,s.phase(wid)['version']);Queue(database).reconcile()
    assert not Worker(database).once()


def test_worker_process_death_after_claim_recovers_both_workflow_kinds(database):
    ids=[start(database)[1],start(database,kind='deal',key='deal')[1]]
    script="""import os
from work_assistant.db.store import Database
from work_assistant.runtime.queue import Queue
q=Queue(Database(os.environ['RUNTIME_TEST_URL']))
assert q.claim('crashed-process')
os._exit(77)
"""
    result=subprocess.run([sys.executable,'-c',script],env=os.environ|{'RUNTIME_TEST_URL':database.url},capture_output=True,timeout=15)
    assert result.returncode==77
    with database.engine.begin() as c:c.execute(update(t.jobs).where(t.jobs.c.status=='leased').values(lease_until=now()-timedelta(seconds=1)))
    worker=Worker(database).start()
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            with database.engine.connect() as c: states=c.execute(select(w.workflows.c.status)).scalars().all()
            if states==['waiting_for_approval']*2:break
            time.sleep(.05)
        assert states==['waiting_for_approval']*2
    finally:worker.close()


def test_metrics_separate_human_wait_and_scope_and_report_unknown_cost(database):
    s,wid=start(database);deliver(database)
    with database.engine.begin() as c:c.execute(update(w.workflows).where(w.workflows.c.id==wid).values(updated_at=now()-timedelta(seconds=12)))
    view=summary(database,database.employee('lin'))
    assert view['scope']=='tenant' and float(view['timing']['human_wait_total_ms'])>=12000
    assert view['model']['cost_usd'] is None and view['outcomes'][0]['status']=='waiting_for_approval'
    assert summary(database,database.employee('outsider'))['recent']==[]
    assert summary(database,database.employee('alex'))['recent']==[]
    with database.engine.begin() as c:c.execute(update(w.workflows).where(w.workflows.c.id==wid).values(timing_started_at=None))
    historical=summary(database,database.employee('lin'))
    assert historical['timing']['covered_workflows']==0 and historical['timing']['system_elapsed_p95_ms'] is None
    assert historical['outcomes'][0]['count']==1


def test_provider_limits_shared_across_instances_and_request_window(database):
    config(database,provider_concurrency=2,provider_per_tenant=1,provider_tenant_rpm=2)
    a=ProviderGate(database);b=ProviderGate(database)
    token=a.acquire('northstar')
    with pytest.raises(CapacityError):b.acquire('northstar')
    other=b.acquire('otherco');a.release(token);b.release(other)
    second=b.acquire('northstar');b.release(second)
    with pytest.raises(CapacityError):a.acquire('northstar')
    with database.engine.begin() as c:c.execute(update(t.provider_calls).values(started_at=now()-timedelta(seconds=61)))
    a.release(a.acquire('northstar'))


def test_event_replay_cursor_tenant_and_revocation(database):
    s,wid=start(database);deliver(database);s.cancel('mei',wid,s.phase(wid)['version'])
    row,events=authorized_events(database,'mei','sla',wid,0)
    assert events and authorized_events(database,'mei','sla',wid,events[0]['id'])[1][0]['id']>events[0]['id']
    app=create_app({'mei':'mei','outside':'outsider'},database_url=database.url,model_factory=lambda:None,embedded_worker=False)
    with TestClient(app) as c:
        path='/v1/sla-workflows/'+wid+'/events'
        assert c.get(path).status_code==401
        assert c.get(path,headers={'Authorization':'Bearer outside'}).status_code==404
        r=c.get(path,headers={'Authorization':'Bearer mei','Last-Event-ID':str(events[-1]['id'])})
        assert 'event: closed' in r.text and 'event: progress' not in r.text
    from work_assistant.db.models import entities
    actor=database.employee('mei').model_copy(update={'active':False})
    with database.engine.begin() as c:c.execute(update(entities['employees']).where(entities['employees'].c.id=='mei').values(payload=actor.model_dump(mode='json')))
    with pytest.raises(PermissionError):authorized_events(database,'mei','sla',wid,0)


def test_backup_restore_files_pending_review_and_next_action(database,tmp_path):
    from work_assistant.runtime.backup import backup,restore,verify_bundle
    from work_assistant.files import ArtifactStore,WriteArguments
    from work_assistant.db.store import Database
    s,wid=start(database);deliver(database)
    files=tmp_path/'original-files';store=ArtifactStore(files)
    artifact=store.create('mei',WriteArguments(filename='restore-proof.txt',content='Private restore proof'),requires_database=True)
    database.register_artifact('mei',artifact)
    with pytest.raises(ValueError,match='artifact files are missing'):
        backup(database.url,tmp_path/'wrong-files',tmp_path/'invalid-backup',quiesced=True,docker=True)
    destination=tmp_path/'backup'
    backup(database.url,files,destination,quiesced=True,docker=True)
    name='northstar_restore_'+uuid4().hex
    url,report=restore(database.url,destination,name,tmp_path/'restored-files',docker=True)
    restored=Database(url)
    try:
        assert report['counts_verified']
        restored_store=ArtifactStore(tmp_path/'restored-files')
        _,path=restored_store.read('mei',artifact.id,database=restored)
        assert path.read_text()=='Private restore proof'
        with pytest.raises(LookupError):restored_store.read('alex',artifact.id,database=restored)
        service=WorkflowService(restored);approval=service.get('mei',wid)['approval']
        service.decide('lin',wid,DecideApproval(approval_id=approval['id'],expected_version=1,request_key='restore-review',decision='approve'))
        assert deliver(restored) and service.phase(wid)['status']=='completed'
        assert s.phase(wid)['status']=='waiting_for_approval'
        with pytest.raises(Exception):restore(database.url,destination,name,tmp_path/'second-files',docker=True)
        (destination/'database.dump').write_bytes(b'corrupted')
        with pytest.raises(ValueError):verify_bundle(destination)
    finally:
        restored.close();admin=create_engine(database.url,isolation_level='AUTOCOMMIT')
        with admin.connect() as c:c.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.mark.parametrize('kind',['sla','deal'])
def test_worker_crash_after_effect_commit_redelivers_without_duplicate(database,kind):
    s,wid=start(database,kind=kind);deliver(database)
    approval=s.get('mei' if kind=='sla' else 'alex',wid)['approval']
    from work_assistant.deals.domain import DecideDeal
    body=(DecideApproval if kind=='sla' else DecideDeal)(approval_id=approval['id'],expected_version=1,request_key='approve',decision='approve')
    s.decide('lin',wid,body)
    script="""import os
from work_assistant.db.store import Database
from work_assistant.runtime.worker import Worker
from work_assistant.workflows.engine import WorkflowEngine
def crash(name,wid):
    if name=='execute':os._exit(77)
Worker(Database(os.environ['RUNTIME_TEST_URL']),engine_factory=lambda service:WorkflowEngine(service,after_step=crash)).once()
"""
    result=subprocess.run([sys.executable,'-c',script],env=os.environ|{'RUNTIME_TEST_URL':database.url},capture_output=True,timeout=20)
    assert result.returncode==77,result.stderr.decode()
    with database.engine.begin() as c:
        assert c.execute(select(func.count()).select_from(w.receipts)).scalar_one()==1
        c.execute(update(t.jobs).values(lease_until=now()-timedelta(seconds=1)))
    assert deliver(database)
    assert s.phase(wid)['status']=='completed'
    with database.engine.connect() as c:assert c.execute(select(func.count()).select_from(w.receipts)).scalar_one()==1


def test_http_capacity_response_is_explicit_and_does_not_accept_work(database):
    config(database,active_total=1);start(database)
    app=create_app({'alex':'alex'},database_url=database.url,model_factory=lambda:None,embedded_worker=False)
    with TestClient(app) as c:
        response=c.post('/v1/deal-workflows',headers={'Authorization':'Bearer alex'},json={'deal_id':'DEAL-101','expected_deal_version':1,'request_key':'full'})
        assert response.status_code==429 and response.headers['retry-after']=='5'
    with database.engine.connect() as c:assert c.execute(select(func.count()).select_from(t.jobs)).scalar_one()==1
