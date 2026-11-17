"""Workspace, conversations, runs, events and artifact metadata."""
from alembic import op
from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, UniqueConstraint

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('organizations', Column('id', String(100), primary_key=True), Column('payload', JSON, nullable=False))
    for name in ('teams', 'employees', 'projects', 'documents'):
        op.create_table(name, Column('id', String(100), primary_key=True),
                        Column('organization_id', String(100), ForeignKey('organizations.id'), nullable=False),
                        Column('payload', JSON, nullable=False))
        op.create_index(f'ix_{name}_organization_id', name, ['organization_id'])
    op.create_table('conversations', Column('id', String(32), primary_key=True),
        Column('organization_id', String(100), ForeignKey('organizations.id'), nullable=False),
        Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False),
        Column('title', String(120), nullable=False), Column('language', String(2), nullable=False),
        Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False))
    op.create_index('ix_conversations_employee_id', 'conversations', ['employee_id'])
    op.create_table('runs', Column('id', String(32), primary_key=True),
        Column('organization_id', String(100), ForeignKey('organizations.id'), nullable=False),
        Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False),
        Column('conversation_id', String(32), ForeignKey('conversations.id')),
        Column('kind', String(20), nullable=False), Column('request_key', String(100), nullable=False),
        Column('payload_hash', String(64), nullable=False), Column('request', JSON, nullable=False),
        Column('status', String(30), nullable=False), Column('result', JSON), Column('error', String(200)),
        Column('lease_token', String(32)), Column('lease_until', Float),
        Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False),
        UniqueConstraint('employee_id', 'request_key', name='uq_run_employee_request'))
    op.create_index('ix_runs_employee_id', 'runs', ['employee_id'])
    op.create_index('ix_runs_conversation_id', 'runs', ['conversation_id'])
    op.create_table('messages', Column('id', String(32), primary_key=True),
        Column('conversation_id', String(32), ForeignKey('conversations.id'), nullable=False),
        Column('run_id', String(32), ForeignKey('runs.id'), nullable=False, unique=True),
        Column('question', String(4000), nullable=False), Column('created_at', Float, nullable=False))
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])
    op.create_table('run_events', Column('id', Integer, primary_key=True, autoincrement=True),
        Column('run_id', String(32), ForeignKey('runs.id'), nullable=False),
        Column('kind', String(50), nullable=False), Column('payload', JSON, nullable=False),
        Column('created_at', Float, nullable=False))
    op.create_index('ix_run_events_run_id', 'run_events', ['run_id'])
    op.create_table('artifacts', Column('id', String(32), primary_key=True),
        Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False),
        Column('run_id', String(32), ForeignKey('runs.id')), Column('payload', JSON, nullable=False),
        Column('created_at', Float, nullable=False))
    op.create_index('ix_artifacts_employee_id', 'artifacts', ['employee_id'])
    op.create_index('ix_artifacts_run_id', 'artifacts', ['run_id'])


def downgrade():
    for name in ('artifacts', 'run_events', 'messages', 'runs', 'conversations', 'documents', 'projects', 'employees', 'teams', 'organizations'):
        op.drop_table(name)
