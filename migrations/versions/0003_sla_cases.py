"""Typed SLA records, private memo revisions and case audit events.

Frozen schema: later model changes must not alter this migration.
"""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

from sqlalchemy import (Table, Column, String, Integer, Numeric, Boolean, DateTime, JSON,
                        ForeignKey, ForeignKeyConstraint, CheckConstraint)
from sqlalchemy import MetaData
metadata = MetaData()
# Existing 0001 keys, declared solely to resolve this migration's foreign keys.
for existing in ('organizations', 'employees', 'runs'):
    Table(existing, metadata, Column('id', String(100), primary_key=True))


def identity():
    return [Column('organization_id', String(100), ForeignKey('organizations.id'), primary_key=True),
            Column('id', String(100), primary_key=True), Column('version', Integer, nullable=False),
            CheckConstraint('version >= 1')]


def reference(table, column):
    return ForeignKeyConstraint(['organization_id', column], [f'{table}.organization_id', f'{table}.id'])


accounts = Table('sla_accounts', metadata, *identity(),
    Column('name', String(200), nullable=False), Column('contact_name', String(200), nullable=False),
    Column('contact_email', String(200), nullable=False))
policies = Table('sla_policies', metadata, *identity(),
    Column('name', String(200), nullable=False), Column('response_minutes', Integer, nullable=False),
    Column('credit_rate_per_minute', Numeric(10, 8), nullable=False),
    Column('monthly_cap_fraction', Numeric(6, 5), nullable=False),
    Column('effective_from', DateTime(timezone=True), nullable=False),
    Column('effective_until', DateTime(timezone=True), nullable=False),
    CheckConstraint('response_minutes > 0 AND credit_rate_per_minute > 0 AND credit_rate_per_minute <= 1'),
    CheckConstraint('monthly_cap_fraction > 0 AND monthly_cap_fraction <= 1'),
    CheckConstraint('effective_until > effective_from'))
contracts = Table('sla_contracts', metadata, *identity(),
    Column('account_id', String(100), nullable=False), Column('policy_id', String(100), nullable=False),
    Column('signed', Boolean, nullable=False), Column('currency', String(3), nullable=False),
    Column('monthly_fee', Numeric(11, 2), nullable=False),
    Column('starts_at', DateTime(timezone=True), nullable=False), Column('ends_at', DateTime(timezone=True), nullable=False),
    reference('sla_accounts', 'account_id'), reference('sla_policies', 'policy_id'),
    CheckConstraint('monthly_fee >= 0 AND ends_at > starts_at'))
tickets = Table('sla_tickets', metadata, *identity(),
    Column('contract_id', String(100), nullable=False), Column('title', String(200), nullable=False),
    Column('severity', String(2), nullable=False), Column('status', String(20), nullable=False),
    Column('assignee_id', String(100), ForeignKey('employees.id'), nullable=False),
    Column('reported_at', DateTime(timezone=True), nullable=False), Column('first_response_at', DateTime(timezone=True)),
    Column('incident_started_at', DateTime(timezone=True)), Column('incident_ended_at', DateTime(timezone=True)),
    Column('exclusions', JSON), Column('eligibility_confirmed', Boolean), Column('prior_credit_amount', Numeric(11, 2)),
    Column('billing_period_start', DateTime(timezone=True)), Column('billing_period_end', DateTime(timezone=True)),
    Column('evidence_note', String(3000), nullable=False), Column('updated_at', DateTime(timezone=True), nullable=False),
    reference('sla_contracts', 'contract_id'),
    CheckConstraint("status IN ('open', 'investigating')"), CheckConstraint("severity IN ('P1', 'P2', 'P3')"),
    CheckConstraint('incident_ended_at >= incident_started_at'), CheckConstraint('first_response_at >= reported_at'),
    CheckConstraint('billing_period_end > billing_period_start'), CheckConstraint('prior_credit_amount >= 0'))
memos = Table('sla_memos', metadata, *identity(),
    Column('ticket_id', String(100), nullable=False),
    Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False),
    Column('run_id', String(32), ForeignKey('runs.id')), Column('language', String(2), nullable=False),
    Column('snapshot', JSON, nullable=False), Column('notes', JSON, nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False), reference('sla_tickets', 'ticket_id'))
revisions = Table('sla_memo_revisions', metadata,
    Column('organization_id', String(100), primary_key=True), Column('memo_id', String(100), primary_key=True),
    Column('version', Integer, primary_key=True), Column('payload', JSON, nullable=False),
    ForeignKeyConstraint(['organization_id', 'memo_id'], ['sla_memos.organization_id', 'sla_memos.id']))
audit = Table('sla_case_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('organization_id', String(100), nullable=False), Column('ticket_id', String(100), nullable=False),
    Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False),
    Column('run_id', String(32), ForeignKey('runs.id')), Column('kind', String(50), nullable=False),
    Column('payload', JSON, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    reference('sla_tickets', 'ticket_id'))
exports = Table('sla_memo_exports', metadata,
    Column('artifact_id', String(32), primary_key=True), Column('organization_id', String(100), nullable=False),
    Column('memo_id', String(100), nullable=False), Column('memo_version', Integer, nullable=False),
    ForeignKeyConstraint(['organization_id', 'memo_id'], ['sla_memos.organization_id', 'sla_memos.id']))
TABLES = [accounts, policies, contracts, tickets, memos, revisions, audit, exports]


def upgrade():
    for table in TABLES:
        table.create(op.get_bind())


def downgrade():
    for table in reversed(TABLES):
        table.drop(op.get_bind())
