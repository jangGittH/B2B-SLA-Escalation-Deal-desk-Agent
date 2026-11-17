"""Durable runtime queue and cumulative workflow timing (frozen schema)."""
from alembic import op
revision='0006'
down_revision='0005'
branch_labels=None
depends_on=None
from sqlalchemy import Table, Column, String, Integer, BigInteger, DateTime, JSON, ForeignKey, Index, CheckConstraint
from sqlalchemy import MetaData
metadata=MetaData()
for name in ('organizations','sla_workflows'):
    Table(name,metadata,Column('id',String(100),primary_key=True))

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


def upgrade():
    for table in TABLES: table.create(op.get_bind())
    op.add_column('sla_workflows',Column('human_wait_ms',BigInteger,nullable=False,server_default='0'))
    op.add_column('sla_workflows',Column('system_elapsed_ms',BigInteger,nullable=False,server_default='0'))
    op.add_column('sla_workflows',Column('timing_started_at',DateTime(timezone=True)))
    op.get_bind().execute(settings.insert().values(id=1,payload={
        'active_total':500,'active_per_tenant':100,'workers_total':4,'workers_per_tenant':1,
        'lease_seconds':15,'max_deliveries':5,'provider_concurrency':2,'provider_per_tenant':1,
        'provider_rpm':60,'provider_tenant_rpm':30}))


def downgrade():
    for name in ('timing_started_at','system_elapsed_ms','human_wait_ms'): op.drop_column('sla_workflows',name)
    for table in reversed(TABLES): table.drop(op.get_bind())
