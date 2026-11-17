from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint

metadata = MetaData()

# Validated domain payloads preserve the bilingual fixture contract. Relational
# identifiers and tenant keys support lookups; domain writes go through validation.
organizations = Table('organizations', metadata,
    Column('id', String(100), primary_key=True), Column('payload', JSON, nullable=False))
entities = {}
for name in ('teams', 'employees', 'projects', 'documents'):
    entities[name] = Table(name, metadata,
        Column('id', String(100), primary_key=True),
        Column('organization_id', String(100), ForeignKey('organizations.id'), nullable=False, index=True),
        Column('payload', JSON, nullable=False))

conversations = Table('conversations', metadata,
    Column('id', String(32), primary_key=True),
    Column('organization_id', String(100), ForeignKey('organizations.id'), nullable=False),
    Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False, index=True),
    Column('title', String(120), nullable=False), Column('language', String(2), nullable=False),
    Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False))
runs = Table('runs', metadata,
    Column('id', String(32), primary_key=True),
    Column('organization_id', String(100), ForeignKey('organizations.id'), nullable=False),
    Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False, index=True),
    Column('conversation_id', String(32), ForeignKey('conversations.id'), index=True),
    Column('kind', String(20), nullable=False), Column('request_key', String(100), nullable=False),
    Column('payload_hash', String(64), nullable=False), Column('request', JSON, nullable=False),
    Column('status', String(30), nullable=False), Column('result', JSON),
    Column('error', String(200)), Column('lease_token', String(32)), Column('lease_until', Float),
    Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False),
    UniqueConstraint('employee_id', 'request_key', name='uq_run_employee_request'))
messages = Table('messages', metadata,
    Column('id', String(32), primary_key=True),
    Column('conversation_id', String(32), ForeignKey('conversations.id'), nullable=False, index=True),
    Column('run_id', String(32), ForeignKey('runs.id'), nullable=False, unique=True),
    Column('question', String(4000), nullable=False), Column('created_at', Float, nullable=False))
events = Table('run_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('run_id', String(32), ForeignKey('runs.id'), nullable=False, index=True),
    Column('kind', String(50), nullable=False), Column('payload', JSON, nullable=False),
    Column('created_at', Float, nullable=False))
artifacts = Table('artifacts', metadata,
    Column('id', String(32), primary_key=True),
    Column('employee_id', String(100), ForeignKey('employees.id'), nullable=False, index=True),
    Column('run_id', String(32), ForeignKey('runs.id'), nullable=True, index=True),
    Column('payload', JSON, nullable=False), Column('created_at', Float, nullable=False))
