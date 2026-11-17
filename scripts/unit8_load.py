"""Reproducible isolated 200-request HTTP burst and two-process worker drain."""
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import httpx
from sqlalchemy import create_engine, select, insert, update, text
from sqlalchemy.engine import make_url
from work_assistant.db.store import Database,migrate
from work_assistant.repository import FixtureRepository
from work_assistant.sla import tables as sla
from work_assistant.deals import tables as deal
from work_assistant.workflows import tables as w
from work_assistant.runtime import tables as rt
from work_assistant.runtime.queue import Limits


def main():
    base=os.environ['TEST_DATABASE_URL'];name='northstar_test_load_'+uuid4().hex
    admin=create_engine(base,isolation_level='AUTOCOMMIT')
    with admin.connect() as c:c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url=make_url(base).set(database=name).render_as_string(hide_password=False)
    db=Database(url);processes=[]
    try:
        migrate(url);db.seed(FixtureRepository.load().workspace)
        inputs=[]
        with db.engine.begin() as c:
            c.execute(update(rt.settings).values(payload=Limits(active_total=60,active_per_tenant=40).model_dump()))
            for i in range(200):
                org='northstar' if (i//2)%2==0 else 'otherco';kind='sla' if i%2==0 else 'deal'
                table=sla.tickets if kind=='sla' else deal.requests
                original=c.execute(select(table).where(table.c.organization_id==org).order_by(table.c.id).limit(1)).mappings().one()
                row=dict(original);row['id']='LOAD-'+str(i);c.execute(insert(table).values(**row))
                actor=('mei' if kind=='sla' else 'alex') if org=='northstar' else 'outsider'
                subject='ticket' if kind=='sla' else 'deal'
                inputs.append((kind,actor,{subject+'_id':row['id'],'expected_'+subject+'_version':1,'request_key':'load-'+str(i)}))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        code="""import os,uvicorn
from work_assistant.api import create_app
app=create_app({'mei':'mei','alex':'alex','outsider':'outsider'},database_url=os.environ['LOAD_URL'],model_factory=lambda:None,embedded_worker=False)
uvicorn.run(app,host='127.0.0.1',port=int(os.environ['LOAD_PORT']),log_level='warning')
"""
        Path('.work_assistant').mkdir(exist_ok=True)
        api_log=open('.work_assistant/unit8-load-server.log','w')
        api=subprocess.Popen([sys.executable,'-c',code],env=os.environ|{'LOAD_URL':url,'LOAD_PORT':str(port)},stdout=api_log,stderr=api_log);processes.append(api)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=30,limits=httpx.Limits(max_connections=20)) as client:
            for _ in range(100):
                try:
                    if client.get('/health/ready').status_code==200:break
                except httpx.ConnectError:pass
                time.sleep(.1)
            else:raise RuntimeError('Benchmark API did not become ready')
            def submit(item):
                kind,actor,body=item;began=time.perf_counter()
                response=client.post('/v1/'+kind+'-workflows',headers={'Authorization':'Bearer '+actor},json=body)
                try:data=response.json()
                except ValueError:data={'error':'Non-JSON server response'}
                return response.status_code,(time.perf_counter()-began)*1000,data
            began=time.perf_counter()
            with ThreadPoolExecutor(max_workers=16) as pool:results=list(pool.map(submit,inputs))
            seconds=time.perf_counter()-began
        assert all(code in (202,429) for code,_,_ in results),[(code,data) for code,_,data in results if code not in (202,429)]
        accepted=sum(code==202 for code,_,_ in results);assert accepted==60
        # Prove accepted work survives the request-serving process stopping.
        api.terminate();api.wait(timeout=15)
        began=time.perf_counter()
        for _ in range(2):
            p=subprocess.Popen([sys.executable,'-m','work_assistant','worker','--slots','1'],env=os.environ|{'WORK_DATABASE_URL':url},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);processes.append(p)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            with db.engine.connect() as c:
                states=c.execute(select(rt.jobs.c.status)).scalars().all()
            if len(states)==accepted and all(x=='idle' for x in states):break
            time.sleep(.1)
        else:raise RuntimeError('Accepted jobs did not drain')
        drain_seconds=time.perf_counter()-began
        with db.engine.connect() as c:
            attempts=c.execute(select(rt.attempts)).mappings().all()
            outcomes=c.execute(select(w.workflows.c.status)).scalars().all()
            assert set(outcomes)<= {'waiting_for_input','waiting_for_approval'}
            assert len({r['worker_id'] for r in attempts})==2
            assert c.execute(select(w.receipts)).first() is None
            db_version=c.exec_driver_sql('SHOW server_version').scalar_one()
        def percentile(values,p):return round(sorted(values)[int((len(values)-1)*p)],2)
        report={'scenario':'200 real loopback HTTP submissions; 16 submitter threads; API stopped before worker drain; two independent worker processes',
            'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'hardware':{'os':platform.system(),'architecture':platform.machine(),'logical_cpus':os.cpu_count(),'python':platform.python_version(),'postgresql':db_version},
            'limits':{'active_total':60,'active_per_tenant':40,'workers_per_tenant':1,'worker_processes':2,'slots_per_process':1},
            'submitted':200,'accepted':accepted,'explicit_429':200-accepted,'unexpected_statuses':0,'intake_seconds':round(seconds,3),
            'intake_p50_ms':percentile([x[1] for x in results],.5),'intake_p95_ms':percentile([x[1] for x in results],.95),
            'drain_seconds_including_process_start':round(drain_seconds,3),'delivery_attempts':len(attempts),'lost_accepted':0,
            'duplicate_effects':0,'effects_expected':0,'outcomes':{k:outcomes.count(k) for k in sorted(set(outcomes))},
            'queue_wait_p95_ms':percentile([r['queue_wait_ms'] for r in attempts],.95),
            'workers_used':len({r['worker_id'] for r in attempts}),
            'caveats':['Synthetic records and local PostgreSQL; no live model or external provider calls.','Measures intake and dispatch to human waits, not financial execution throughput or production capacity.']}
        output=Path('.work_assistant/unit8-load.json');output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=20)
                except subprocess.TimeoutExpired:p.kill();p.wait()
        db.close()
        with admin.connect() as c:c.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()

if __name__=='__main__':main()
