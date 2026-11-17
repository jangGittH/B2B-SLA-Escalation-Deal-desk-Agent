from datetime import datetime, timezone, timedelta
from uuid import uuid4
from sqlalchemy import select, insert, update, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import BaseModel, ConfigDict, Field
from . import tables as t
from ..workflows.tables import workflows as w

RUNNABLE={'queued','running','approved','verifying','retry_scheduled'}
TERMINAL={'completed','rejected','cancelled','failed','partial'}


def now(): return datetime.now(timezone.utc)


class Limits(BaseModel):
    model_config=ConfigDict(extra='forbid')
    active_total:int=Field(default=500,ge=1,le=10000)
    active_per_tenant:int=Field(default=100,ge=1,le=10000)
    workers_total:int=Field(default=4,ge=1,le=64)
    workers_per_tenant:int=Field(default=1,ge=1,le=16)
    lease_seconds:int=Field(default=15,ge=2,le=300)
    max_deliveries:int=Field(default=5,ge=1,le=20)
    provider_concurrency:int=Field(default=2,ge=1,le=64)
    provider_per_tenant:int=Field(default=1,ge=1,le=32)
    provider_rpm:int=Field(default=60,ge=1,le=10000)
    provider_tenant_rpm:int=Field(default=30,ge=1,le=10000)


class CapacityError(Exception): pass


def limits(conn): return Limits.model_validate(conn.execute(select(t.settings.c.payload).where(t.settings.c.id==1)).scalar_one())


def admit(conn,org):
    # One short admission mutex makes aggregate capacity exact across API processes.
    conn.execute(text("SELECT pg_advisory_xact_lock(81008,1)"))
    cfg=limits(conn)
    active=w.c.status.not_in(TERMINAL)
    total=conn.execute(select(func.count()).select_from(w).where(active)).scalar_one()
    own=conn.execute(select(func.count()).select_from(w).where(active,w.c.organization_id==org)).scalar_one()
    if total>=cfg.active_total or own>=cfg.active_per_tenant:
        raise CapacityError('Active workflow capacity reached; finish or cancel existing reviews before retrying')


def enqueue(conn,row,kind):
    """Called in the same transaction as accepted start/decision/resume. Never drop a wakeup."""
    stamp=now()
    conn.execute(pg_insert(t.tenants).values(organization_id=row['organization_id'],last_served=datetime(1970,1,1,tzinfo=timezone.utc)).on_conflict_do_nothing())
    old=conn.execute(select(t.jobs).where(t.jobs.c.workflow_id==row['id']).with_for_update()).mappings().first()
    if old:
        # A worker may still be finishing the preceding generation. Preserve its lease;
        # acknowledge will notice the new generation and put it back in the ready queue.
        conn.execute(update(t.jobs).where(t.jobs.c.workflow_id==row['id']).values(generation=old['generation']+1,
            status='leased' if old['status']=='leased' else 'pending',deliveries=0,available_at=stamp,enqueued_at=stamp,
            last_error=None,updated_at=stamp))
    else:
        conn.execute(insert(t.jobs).values(workflow_id=row['id'],organization_id=row['organization_id'],kind=kind,status='pending',
            generation=1,deliveries=0,available_at=stamp,enqueued_at=stamp,updated_at=stamp))


class Queue:
    def __init__(self,db,*,clock=now): self.db,self.clock=db,clock

    def reconcile(self):
        stamp=self.clock()
        with self.db.engine.begin() as c:
            # Bounded scan resumes after the last repaired batch; completed entries are excluded.
            rows=c.execute(select(w).outerjoin(t.jobs,t.jobs.c.workflow_id==w.c.id).where(
                w.c.status.in_(RUNNABLE),(t.jobs.c.workflow_id.is_(None)) | (t.jobs.c.status=='idle')).limit(100)).mappings().all()
            for row in rows:
                # Lock business first, then queue, matching enqueue's lock order.
                current=c.execute(select(w).where(w.c.id==row['id']).with_for_update(skip_locked=True)).mappings().first()
                if current and current['status'] in RUNNABLE:
                    enqueue(c,current,'deal' if current['deal_id'] else 'sla')
            expired=c.execute(select(t.jobs).where(t.jobs.c.status=='leased',t.jobs.c.lease_until<=stamp).with_for_update(skip_locked=True).limit(100)).mappings().all()
            cfg=limits(c)
            for job in expired:
                state='dead' if job['deliveries']>=cfg.max_deliveries else 'pending'
                c.execute(update(t.jobs).where(t.jobs.c.workflow_id==job['workflow_id']).values(status=state,
                    lease_token=None,lease_until=None,worker_id=None,available_at=stamp,updated_at=stamp,last_error='Worker lease expired'))
                c.execute(update(t.attempts).where(t.attempts.c.id==job['lease_token'],t.attempts.c.finished_at.is_(None)).values(
                    finished_at=stamp,outcome='lease_expired'))
            # Drop pending deliveries for cancelled/terminal/waiting workflows without invoking the graph.
            ids=select(w.c.id).where(w.c.status.not_in(RUNNABLE))
            c.execute(update(t.jobs).where(t.jobs.c.status.in_(['pending','dead']),t.jobs.c.workflow_id.in_(ids)).values(status='idle',updated_at=stamp))
        return len(rows)+len(expired)

    def claim(self,worker_id):
        stamp=self.clock()
        with self.db.engine.begin() as c:
            c.execute(text('SELECT pg_advisory_xact_lock(81008,2)'))
            cfg=limits(c)
            active=c.execute(select(t.jobs.c.organization_id,func.count()).where(t.jobs.c.status=='leased',t.jobs.c.lease_until>stamp).group_by(t.jobs.c.organization_id)).all()
            counts=dict(active)
            if sum(counts.values())>=cfg.workers_total: return None
            ready=select(t.jobs.c.organization_id).where(t.jobs.c.status=='pending',t.jobs.c.available_at<=stamp)
            orgs=c.execute(select(t.tenants.c.organization_id).where(t.tenants.c.organization_id.in_(ready)).order_by(t.tenants.c.last_served,t.tenants.c.organization_id)).scalars()
            for org in orgs:
                if counts.get(org,0)>=cfg.workers_per_tenant: continue
                job=c.execute(select(t.jobs).where(t.jobs.c.organization_id==org,t.jobs.c.status=='pending',t.jobs.c.available_at<=stamp)
                    .order_by(t.jobs.c.enqueued_at,t.jobs.c.workflow_id).with_for_update(skip_locked=True).limit(1)).mappings().first()
                if not job: continue
                token=uuid4().hex
                c.execute(update(t.jobs).where(t.jobs.c.workflow_id==job['workflow_id']).values(status='leased',lease_token=token,
                    lease_until=stamp+timedelta(seconds=cfg.lease_seconds),worker_id=worker_id,deliveries=job['deliveries']+1,updated_at=stamp))
                c.execute(update(t.tenants).where(t.tenants.c.organization_id==org).values(last_served=stamp))
                c.execute(insert(t.attempts).values(id=token,workflow_id=job['workflow_id'],organization_id=org,worker_id=worker_id,
                    generation=job['generation'],started_at=stamp,queue_wait_ms=max(0,round((stamp-job['enqueued_at']).total_seconds()*1000))))
                return dict(job)|{'lease_token':token,'lease_seconds':cfg.lease_seconds,'worker_id':worker_id}
        return None

    def heartbeat(self,job):
        with self.db.engine.begin() as c:
            return c.execute(update(t.jobs).where(t.jobs.c.workflow_id==job['workflow_id'],t.jobs.c.lease_token==job['lease_token'],
                t.jobs.c.status=='leased',t.jobs.c.lease_until>self.clock()).values(lease_until=self.clock()+timedelta(seconds=job['lease_seconds']))).rowcount==1

    def finish(self,job,error=None):
        stamp=self.clock()
        with self.db.engine.begin() as c:
            current=c.execute(select(t.jobs).where(t.jobs.c.workflow_id==job['workflow_id']).with_for_update()).mappings().one()
            if current['lease_token']!=job['lease_token'] or current['status']!='leased': return False
            row=c.execute(select(w).where(w.c.id==job['workflow_id'])).mappings().one()
            cfg=limits(c)
            if row['status'] not in RUNNABLE: state='idle'
            elif current['generation']!=job['generation']: state='pending'
            elif error and current['deliveries']>=cfg.max_deliveries: state='dead'
            else: state='pending'
            delay=min(2**current['deliveries'],30) if error else 0.25
            due=max(stamp+timedelta(seconds=delay),row['retry_at'] or stamp)
            c.execute(update(t.jobs).where(t.jobs.c.workflow_id==job['workflow_id']).values(status=state,available_at=due,
                lease_token=None,lease_until=None,worker_id=None,last_error=error,updated_at=stamp))
            attempt=c.execute(select(t.attempts).where(t.attempts.c.id==job['lease_token'])).mappings().one()
            c.execute(update(t.attempts).where(t.attempts.c.id==job['lease_token']).values(finished_at=stamp,
                duration_ms=max(0,round((stamp-attempt['started_at']).total_seconds()*1000)),outcome=state if error else row['status']))
            return True

    def worker_heartbeat(self,worker_id,draining=False):
        stamp=self.clock()
        with self.db.engine.begin() as c:
            c.execute(pg_insert(t.workers).values(id=worker_id,started_at=stamp,heartbeat_at=stamp,draining=int(draining)).on_conflict_do_update(
                index_elements=['id'],set_={'heartbeat_at':stamp,'draining':int(draining)}))
