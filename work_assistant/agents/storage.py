"""Append-only plan revisions; node results are private, run-scoped durable handoffs."""
import time
from sqlalchemy import Table, Column, String, Integer, Float, JSON, ForeignKey, select
from sqlalchemy.dialects.postgresql import insert
from ..db.models import metadata
from ..telemetry import current_run

plans = Table('agent_plans', metadata,
    Column('run_id', String(32), ForeignKey('runs.id'), primary_key=True),
    Column('revision', Integer, primary_key=True), Column('payload', JSON, nullable=False),
    Column('created_at', Float, nullable=False))
steps = Table('agent_steps', metadata,
    Column('run_id', String(32), ForeignKey('runs.id'), primary_key=True),
    Column('revision', Integer, primary_key=True), Column('node_id', String(40), primary_key=True),
    Column('role', String(20), nullable=False), Column('status', String(30), nullable=False),
    Column('payload', JSON, nullable=False), Column('updated_at', Float, nullable=False))

def save_plan(revision, plan):
    context=current_run.get()
    if context:
        db,run=context
        with db.engine.begin() as c:
            c.execute(insert(plans).values(run_id=run['id'],revision=revision,
                payload=plan.model_dump(mode='json'),created_at=time.time()).on_conflict_do_nothing())

def save_step(revision,node,status,payload):
    context=current_run.get()
    if context:
        db,run=context
        row=dict(run_id=run['id'],revision=revision,node_id=node.id,role=node.role,
                 status=status,payload=payload,updated_at=time.time())
        with db.engine.begin() as c:
            c.execute(insert(steps).values(**row).on_conflict_do_update(
                index_elements=['run_id','revision','node_id'],
                set_={k:row[k] for k in ('status','payload','updated_at')}))

def inspect(db,actor,run_id):
    db.get_run(actor,run_id)
    from ..durable_api import evidence_available
    from ..knowledge import KnowledgeService
    if not evidence_available(db,KnowledgeService(db),actor,run_id): raise LookupError('Plan evidence unavailable')
    with db.engine.connect() as c:
        revisions=[dict(r) for r in c.execute(select(plans).where(plans.c.run_id==run_id).order_by(plans.c.revision)).mappings()]
        # Historical raw handoff bodies are intentionally not returned. Current source checks
        # protect the saved response; spans expose only role/state/version and source pointers.
        records=[{k:r[k] for k in ('revision','node_id','role','status','updated_at')} for r in
            c.execute(select(steps).where(steps.c.run_id==run_id).order_by(steps.c.revision,steps.c.node_id)).mappings()]
    return {'revisions':revisions,'steps':records}
