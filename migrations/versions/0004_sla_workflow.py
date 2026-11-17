"""Durable SLA approvals and sandbox execution ledger (frozen schema)."""
from alembic import op
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

from sqlalchemy import (Table, Column, String, Integer, Numeric, DateTime, JSON, ForeignKey,
                        ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, text)
from sqlalchemy import MetaData
metadata = MetaData()
for name in ('organizations','employees','conversations','runs'):
    Table(name, metadata, Column('id', String(100), primary_key=True))
for name in ('sla_tickets','sla_contracts'):
    Table(name, metadata, Column('organization_id',String(100),primary_key=True), Column('id',String(100),primary_key=True))

workflows = Table('sla_workflows', metadata,
    Column('id', String(32), primary_key=True),
    Column('organization_id', String(100), ForeignKey('organizations.id'), nullable=False),
    Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False),
    Column('reviewer_id', String(100), ForeignKey('employees.id')),
    Column('ticket_id', String(100), nullable=False),
    Column('conversation_id', String(32), ForeignKey('conversations.id'), nullable=False),
    Column('origin_run_id', String(32), ForeignKey('runs.id')),
    Column('request_key', String(100), nullable=False), Column('request_hash', String(64), nullable=False),
    Column('language', String(2), nullable=False), Column('version', Integer, nullable=False),
    Column('status', String(32), nullable=False), Column('phase', String(30), nullable=False),
    Column('proposal_revision', Integer, nullable=False), Column('snapshot', JSON),
    Column('memo_id', String(100)), Column('approval_id', String(32)),
    Column('missing_inputs', JSON, nullable=False), Column('error', JSON), Column('outcome', JSON),
    Column('attempts', Integer, nullable=False), Column('retry_at', DateTime(timezone=True)),
    Column('created_at', DateTime(timezone=True), nullable=False), Column('updated_at', DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(['organization_id','ticket_id'], ['sla_tickets.organization_id','sla_tickets.id']),
    UniqueConstraint('organization_id','id', name='uq_sla_workflow_org_id'),
    UniqueConstraint('organization_id','employee_id','request_key', name='uq_sla_workflow_request'))
Index('uq_sla_active_case', workflows.c.organization_id, workflows.c.ticket_id, unique=True,
      postgresql_where=text("status NOT IN ('completed','rejected','cancelled','failed','partial')"))

approvals = Table('sla_approvals', metadata,
    Column('id', String(32), primary_key=True), Column('organization_id', String(100), nullable=False),
    Column('workflow_id', String(32), nullable=False), Column('proposal_revision', Integer, nullable=False),
    Column('reviewer_id', String(100), ForeignKey('employees.id'), nullable=False),
    Column('version', Integer, nullable=False), Column('status', String(20), nullable=False),
    Column('payload', JSON, nullable=False), Column('payload_hash', String(64), nullable=False),
    Column('approved_payload', JSON), Column('approved_hash', String(64)),
    Column('decision', String(20)), Column('decision_key', String(100)), Column('decision_hash', String(64)),
    Column('reason', String(2000)), Column('expires_at', DateTime(timezone=True), nullable=False),
    Column('decided_at', DateTime(timezone=True)),
    ForeignKeyConstraint(['organization_id','workflow_id'], ['sla_workflows.organization_id','sla_workflows.id']),
    UniqueConstraint('workflow_id','proposal_revision', name='uq_sla_workflow_proposal'))

credits = Table('sla_sandbox_credits', metadata,
    Column('operation_id', String(100), primary_key=True), Column('organization_id', String(100), nullable=False),
    Column('ticket_id', String(100), nullable=False), Column('contract_id', String(100), nullable=False),
    Column('workflow_id', String(32), nullable=False), Column('approval_id', String(32), ForeignKey('sla_approvals.id'), nullable=False),
    Column('amount', Numeric(11,2), nullable=False), Column('currency', String(3), nullable=False),
    Column('period_start', DateTime(timezone=True), nullable=False), Column('period_end', DateTime(timezone=True), nullable=False),
    Column('payload_hash', String(64), nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(['organization_id','ticket_id'], ['sla_tickets.organization_id','sla_tickets.id']),
    ForeignKeyConstraint(['organization_id','contract_id'], ['sla_contracts.organization_id','sla_contracts.id']),
    ForeignKeyConstraint(['organization_id','workflow_id'], ['sla_workflows.organization_id','sla_workflows.id']),
    UniqueConstraint('organization_id','ticket_id', name='uq_sla_credit_case'),
    CheckConstraint('amount > 0'))

receipts = Table('sla_action_receipts', metadata,
    Column('operation_id', String(100), primary_key=True), Column('organization_id', String(100), nullable=False),
    Column('workflow_id', String(32), nullable=False), Column('approval_id', String(32), ForeignKey('sla_approvals.id'), nullable=False),
    Column('payload_hash', String(64), nullable=False), Column('payload', JSON, nullable=False),
    Column('result', JSON, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(['organization_id','workflow_id'], ['sla_workflows.organization_id','sla_workflows.id']),
    UniqueConstraint('workflow_id', name='uq_sla_receipt_workflow'))

events = Table('sla_workflow_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True), Column('organization_id', String(100), nullable=False),
    Column('workflow_id', String(32), nullable=False), Column('kind', String(50), nullable=False),
    Column('payload', JSON, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(['organization_id','workflow_id'], ['sla_workflows.organization_id','sla_workflows.id']))
TABLES = [workflows, approvals, credits, receipts, events]


def upgrade():
    for table in TABLES:
        table.create(op.get_bind())


def downgrade():
    for table in reversed(TABLES):
        table.drop(op.get_bind())
