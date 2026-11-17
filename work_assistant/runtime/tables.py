from sqlalchemy import Table, Column, String, Integer, BigInteger, DateTime, JSON, ForeignKey, Index, CheckConstraint
from ..db.models import metadata

settings = Table('runtime_settings',metadata,Column('id',Integer,primary_key=True),Column('payload',JSON,nullable=False))
tenants = Table('runtime_tenants',metadata,Column('organization_id',String(100),ForeignKey('organizations.id'),primary_key=True),
    Column('last_served',DateTime(timezone=True),nullable=False))
jobs = Table('runtime_jobs',metadata,
    Column('workflow_id',String(32),ForeignKey('sla_workflows.id'),primary_key=True),
    Column('organization_id',String(100),ForeignKey('organizations.id'),nullable=False),Column('kind',String(10),nullable=False),
    Column('status',String(12),nullable=False),Column('generation',Integer,nullable=False),Column('deliveries',Integer,nullable=False),
    Column('available_at',DateTime(timezone=True),nullable=False),Column('enqueued_at',DateTime(timezone=True),nullable=False),
    Column('lease_token',String(32)),Column('lease_until',DateTime(timezone=True)),Column('worker_id',String(100)),
    Column('last_error',String(200)),Column('updated_at',DateTime(timezone=True),nullable=False),
    CheckConstraint("status IN ('pending','leased','idle','dead')"),CheckConstraint("kind IN ('sla','deal')"))
Index('ix_runtime_ready',jobs.c.status,jobs.c.available_at,jobs.c.organization_id)
attempts = Table('runtime_attempts',metadata,Column('id',String(32),primary_key=True),
    Column('workflow_id',String(32),ForeignKey('sla_workflows.id'),nullable=False),Column('organization_id',String(100),nullable=False),
    Column('worker_id',String(100),nullable=False),Column('generation',Integer,nullable=False),Column('started_at',DateTime(timezone=True),nullable=False),
    Column('finished_at',DateTime(timezone=True)),Column('queue_wait_ms',BigInteger,nullable=False),Column('duration_ms',BigInteger),Column('outcome',String(30)))
workers = Table('runtime_workers',metadata,Column('id',String(100),primary_key=True),Column('started_at',DateTime(timezone=True),nullable=False),
    Column('heartbeat_at',DateTime(timezone=True),nullable=False),Column('draining',Integer,nullable=False))
provider_calls = Table('runtime_provider_calls',metadata,Column('id',String(32),primary_key=True),
    Column('organization_id',String(100),ForeignKey('organizations.id'),nullable=False),Column('started_at',DateTime(timezone=True),nullable=False),
    Column('expires_at',DateTime(timezone=True),nullable=False),Column('finished_at',DateTime(timezone=True)))
Index('ix_provider_window',provider_calls.c.started_at)
TABLES=[settings,tenants,jobs,attempts,workers,provider_calls]
