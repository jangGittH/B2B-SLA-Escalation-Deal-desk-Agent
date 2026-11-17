"""Deal records and shared review workflow extension (frozen schema)."""
from alembic import op
revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

from sqlalchemy import Table, Column, String, Integer, Numeric, DateTime, JSON, ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint
from sqlalchemy import MetaData
metadata = MetaData()
for name in ('organizations','employees','sla_workflows','sla_approvals'):
    Table(name,metadata,Column('id',String(100),primary_key=True))
Table('sla_contracts',metadata,Column('organization_id',String(100),primary_key=True),Column('id',String(100),primary_key=True))

def identity():
    return [Column('organization_id',String(100),ForeignKey('organizations.id'),primary_key=True),
            Column('id',String(100),primary_key=True),Column('version',Integer,nullable=False),CheckConstraint('version >= 1')]

def reference(table,column):
    return ForeignKeyConstraint(['organization_id',column],[table+'.organization_id',table+'.id'])

policies = Table('deal_pricing_policies',metadata,*identity(),
    Column('name',String(200),nullable=False),Column('currency',String(3),nullable=False),
    Column('monthly_list_price',Numeric(11,2),nullable=False),Column('monthly_cost',Numeric(11,2)),
    Column('cost_evidence',String(2000),nullable=False),Column('maximum_discount',Numeric(5,4),nullable=False),
    Column('minimum_margin',Numeric(5,4),nullable=False),Column('max_extension_months',Integer,nullable=False),
    Column('effective_from',DateTime(timezone=True),nullable=False),Column('effective_until',DateTime(timezone=True),nullable=False),
    CheckConstraint('monthly_list_price > 0 AND monthly_cost >= 0'),
    CheckConstraint('maximum_discount BETWEEN 0 AND 1 AND minimum_margin BETWEEN 0 AND 1'),
    CheckConstraint('effective_until > effective_from AND max_extension_months BETWEEN 1 AND 36'))
requests = Table('deal_requests',metadata,*identity(),
    Column('contract_id',String(100),nullable=False),Column('pricing_policy_id',String(100),nullable=False),
    Column('owner_id',String(100),ForeignKey('employees.id'),nullable=False),Column('title',String(200),nullable=False),
    Column('requested_discount',Numeric(5,4)),Column('renewal_end',DateTime(timezone=True)),
    Column('evidence_note',String(3000),nullable=False),Column('updated_at',DateTime(timezone=True),nullable=False),
    reference('sla_contracts','contract_id'),reference('deal_pricing_policies','pricing_policy_id'),
    CheckConstraint('requested_discount BETWEEN 0 AND 1'))
sandbox_contracts = Table('deal_sandbox_contracts',metadata,*identity(),
    Column('source_contract_version',Integer,nullable=False),Column('monthly_fee',Numeric(11,2),nullable=False),
    Column('currency',String(3),nullable=False),Column('ends_at',DateTime(timezone=True),nullable=False),
    ForeignKeyConstraint(['organization_id','id'],['sla_contracts.organization_id','sla_contracts.id']),
    CheckConstraint('monthly_fee >= 0 AND source_contract_version >= 1'))
memos = Table('deal_memos',metadata,*identity(),
    Column('deal_id',String(100),nullable=False),Column('employee_id',String(100),ForeignKey('employees.id'),nullable=False),
    Column('language',String(2),nullable=False),Column('snapshot',JSON,nullable=False),Column('notes',JSON,nullable=False),
    Column('updated_at',DateTime(timezone=True),nullable=False),reference('deal_requests','deal_id'))
effects = Table('deal_contract_effects',metadata,
    Column('operation_id',String(100),primary_key=True),Column('organization_id',String(100),nullable=False),
    Column('deal_id',String(100),nullable=False),Column('contract_id',String(100),nullable=False),
    Column('workflow_id',String(32),ForeignKey('sla_workflows.id'),nullable=False),
    Column('approval_id',String(32),ForeignKey('sla_approvals.id'),nullable=False),
    Column('payload_hash',String(64),nullable=False),Column('before',JSON,nullable=False),Column('after',JSON,nullable=False),
    Column('created_at',DateTime(timezone=True),nullable=False),reference('deal_requests','deal_id'),
    reference('sla_contracts','contract_id'),UniqueConstraint('organization_id','deal_id',name='uq_deal_effect_request'))
audit = Table('deal_record_events',metadata,
    Column('id',Integer,primary_key=True,autoincrement=True),Column('organization_id',String(100),ForeignKey('organizations.id'),nullable=False),
    Column('employee_id',String(100),ForeignKey('employees.id'),nullable=False),Column('record_id',String(100),nullable=False),
    Column('kind',String(50),nullable=False),Column('payload',JSON,nullable=False),Column('created_at',DateTime(timezone=True),nullable=False))
TABLES = [policies,requests,sandbox_contracts,memos,effects,audit]


def upgrade():
    for table in TABLES:
        table.create(op.get_bind())
    op.alter_column('sla_workflows','ticket_id',nullable=True)
    op.add_column('sla_workflows',Column('deal_id',String(100)))
    op.create_foreign_key('fk_workflow_deal','sla_workflows','deal_requests',['organization_id','deal_id'],['organization_id','id'])
    op.create_check_constraint('ck_workflow_one_subject','sla_workflows','(ticket_id IS NULL) <> (deal_id IS NULL)')
    from sqlalchemy import text
    op.create_index('uq_deal_active_request','sla_workflows',['organization_id','deal_id'],unique=True,
        postgresql_where=text("status NOT IN ('completed','rejected','cancelled','failed','partial')"))


def downgrade():
    op.drop_index('uq_deal_active_request',table_name='sla_workflows')
    op.drop_constraint('ck_workflow_one_subject','sla_workflows')
    op.drop_constraint('fk_workflow_deal','sla_workflows',type_='foreignkey')
    # Refuse to discard deal workflow history during a downgrade.
    if op.get_bind().exec_driver_sql('SELECT 1 FROM sla_workflows WHERE deal_id IS NOT NULL LIMIT 1').first():
        raise RuntimeError('Deal workflow history exists; use a forward migration')
    op.drop_column('sla_workflows','deal_id')
    op.alter_column('sla_workflows','ticket_id',nullable=False)
    for table in reversed(TABLES):
        table.drop(op.get_bind())
