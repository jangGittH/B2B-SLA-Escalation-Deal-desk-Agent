"""Unit 9 versioned evidence, private offloads, bounded contexts and governed memory."""
from alembic import op
revision='0007'
down_revision='0006'
branch_labels=None
depends_on=None
from sqlalchemy import Table,Column,String,Integer,Float,JSON,LargeBinary,ForeignKey,UniqueConstraint
from sqlalchemy import MetaData
metadata=MetaData()
for name in ('organizations','employees','runs'):
    Table(name,metadata,Column('id',String(100),primary_key=True))

revisions=Table('knowledge_revisions',metadata,
    Column('id',String(32),primary_key=True),Column('document_id',String(100),nullable=False,index=True),
    Column('organization_id',String(100),ForeignKey('organizations.id'),nullable=False),
    Column('owner_id',String(100),ForeignKey('employees.id'),nullable=False),Column('version',Integer,nullable=False),
    Column('filename',String(150),nullable=False),Column('original',LargeBinary,nullable=False),
    Column('checksum',String(64),nullable=False),Column('payload',JSON,nullable=False),Column('created_at',Float,nullable=False),
    UniqueConstraint('document_id','version',name='uq_knowledge_revision'))
chunks=Table('knowledge_chunks',metadata,Column('id',String(64),primary_key=True),
    Column('document_id',String(100),nullable=False,index=True),Column('version',Integer,nullable=False),
    Column('fingerprint',String(64),nullable=False),Column('language',String(2),nullable=False),
    Column('ordinal',Integer,nullable=False),Column('section',String(500),nullable=False),Column('content',String,nullable=False),
    Column('embedding_model',String(200)),Column('embedding',JSON))
offloads=Table('context_offloads',metadata,Column('id',String(32),primary_key=True),
    Column('organization_id',String(100),ForeignKey('organizations.id'),nullable=False),
    Column('employee_id',String(100),ForeignKey('employees.id'),nullable=False),Column('run_id',String(32),ForeignKey('runs.id')),
    Column('payload',JSON,nullable=False),Column('guards',JSON,nullable=False),Column('created_at',Float,nullable=False),Column('expires_at',Float,nullable=False))
contexts=Table('task_contexts',metadata,Column('run_id',String(32),ForeignKey('runs.id'),primary_key=True),
    Column('payload',JSON,nullable=False),Column('updated_at',Float,nullable=False))
memories=Table('governed_memories',metadata,Column('id',String(32),primary_key=True),
    Column('organization_id',String(100),ForeignKey('organizations.id'),nullable=False),Column('owner_id',String(100),ForeignKey('employees.id'),nullable=False),
    Column('scope',String(10),nullable=False),Column('team_id',String(100)),Column('kind',String(20),nullable=False),
    Column('key',String(120),nullable=False),Column('value',String(2000),nullable=False),Column('status',String(20),nullable=False),
    Column('version',Integer,nullable=False),Column('sources',JSON,nullable=False),Column('confidence',Float,nullable=False),
    Column('supersedes',String(32)),Column('confirmed_by',String(100)),Column('origin_run_id',String(32)),
    Column('created_at',Float,nullable=False),Column('updated_at',Float,nullable=False),Column('expires_at',Float,nullable=False),Column('last_used_at',Float))
memory_events=Table('memory_events',metadata,Column('id',String(32),primary_key=True),
    Column('memory_id',String(32),ForeignKey('governed_memories.id'),nullable=False),Column('actor_id',String(100),nullable=False),
    Column('action',String(20),nullable=False),Column('version',Integer,nullable=False),Column('created_at',Float,nullable=False))
TABLES=[revisions,chunks,offloads,contexts,memories,memory_events]


def upgrade():
    for table in TABLES: table.create(op.get_bind())


def downgrade():
    for table in reversed(TABLES): table.drop(op.get_bind())
