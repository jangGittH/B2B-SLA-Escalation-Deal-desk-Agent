from sqlalchemy import Table, Column, String, Integer, Numeric, DateTime, JSON, ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint
from ..db.models import metadata
from ..sla.tables import identity, reference

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
