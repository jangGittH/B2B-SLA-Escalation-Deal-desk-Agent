"""Distributed request/concurrency admission; SDK retries remain explicitly disabled."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, insert, update, func, text, delete
from .queue import limits, now, CapacityError
from .tables import provider_calls as p


class ProviderGate:
    def __init__(self,db): self.db=db

    def acquire(self,org):
        stamp=now()
        with self.db.engine.begin() as c:
            c.execute(text('SELECT pg_advisory_xact_lock(81008,3)'))
            cfg=limits(c)
            live=(p.c.finished_at.is_(None)) & (p.c.expires_at>stamp)
            recent=p.c.started_at>stamp-timedelta(seconds=60)
            def count(where): return c.execute(select(func.count()).select_from(p).where(where)).scalar_one()
            if (count(live)>=cfg.provider_concurrency or count(live & (p.c.organization_id==org))>=cfg.provider_per_tenant
                or count(recent)>=cfg.provider_rpm or count(recent & (p.c.organization_id==org))>=cfg.provider_tenant_rpm):
                raise CapacityError('Model request limit reached; retry after the current rate window')
            token=uuid4().hex
            c.execute(insert(p).values(id=token,organization_id=org,started_at=stamp,expires_at=stamp+timedelta(seconds=120)))
            c.execute(delete(p).where(p.c.started_at<stamp-timedelta(days=1),p.c.expires_at<stamp))
            return token

    def release(self,token):
        with self.db.engine.begin() as c:
            c.execute(update(p).where(p.c.id==token).values(finished_at=now()))
