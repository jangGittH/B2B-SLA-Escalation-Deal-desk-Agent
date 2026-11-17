"""Fixed LangGraph with PostgreSQL checkpoints; database decisions alone authorize resume."""
from contextlib import contextmanager
from typing import TypedDict
import time

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ..db.store import ConflictError
from .service import WorkflowService, TERMINAL


@contextmanager
def saver(url):
    uri=make_url(url).set(drivername='postgresql').render_as_string(hide_password=False)
    with psycopg.connect(uri, autocommit=True, prepare_threshold=0, row_factory=dict_row,
                         connect_timeout=5, options='-c statement_timeout=10000 -c lock_timeout=5000') as conn:
        yield PostgresSaver(conn, serde=JsonPlusSerializer(pickle_fallback=False))


def setup_checkpoints(url):
    with saver(url) as checkpointer:
        checkpointer.setup()


class State(TypedDict):
    workflow_id: str
    phase: str


class WorkflowEngine:
    def __init__(self, service: WorkflowService, *, after_step=None):
        self.service=service
        self.after_step=after_step  # fault-injection seam, never populated by HTTP/model input

    def graph(self, checkpointer):
        graph=StateGraph(State)
        def step(name):
            def execute(state):
                phase=getattr(self.service,name)(state['workflow_id'])
                if self.after_step:
                    self.after_step(name,state['workflow_id'])
                return {'phase':phase}
            return execute
        for name in ('investigate','prepare','execute','verify'):
            graph.add_node(name,step(name))
        def wait(state):
            row=self.service.phase(state['workflow_id'])
            # Interrupts replay this node. Approved decisions and edited facts are persisted outside it.
            if row['phase']=='wait':
                interrupt({'workflow_id':row['id'],'status':row['status'],
                           'missing_inputs':row['missing_inputs'],'approval_id':row['approval_id']})
            return {'phase':self.service.phase(state['workflow_id'])['phase']}
        graph.add_node('wait',wait)
        def route(state):
            phase=self.service.phase(state['workflow_id'])['phase']
            return END if phase=='end' else phase
        graph.add_conditional_edges(START,route)
        for name in ('investigate','prepare','execute','verify','wait'):
            graph.add_conditional_edges(name,route)
        return graph.compile(checkpointer=checkpointer)

    def drive(self, workflow_id):
        # Session advisory lock prevents two graph writers, including across API processes.
        # It is released by PostgreSQL on connection/process death; transactions remain short.
        with self.service.db.engine.connect() as lock:
            locked=lock.execute(text('SELECT pg_try_advisory_lock(hashtextextended(:key, 0))'),
                {'key':self.service.kind+'-workflow:'+workflow_id}).scalar_one()
            lock.commit()
            if not locked:
                return False
            started=time.monotonic()
            try:
                row=self.service.phase(workflow_id)
                if row['status'] in TERMINAL:
                    return True
                if row['retry_at'] and self.service.clock()<row['retry_at']:
                    return False
                if row['status']=='retry_scheduled':
                    with self.service.db.engine.begin() as conn:
                        current=self.service._row(conn,workflow_id)
                        if current['status']=='retry_scheduled':
                            self.service._change(conn,current,status=('approved' if current['phase']=='execute' else
                                'verifying' if current['phase']=='verify' else 'running'),retry_at=None)
                            self.service._event(conn,current,'retry_started',{'after_failures':current['attempts']})
                with saver(self.service.db.url) as checkpointer:
                    graph=self.graph(checkpointer)
                    config={'configurable':{'thread_id':self.service.namespace+workflow_id}, 'recursion_limit':25}
                    saved=graph.get_state(config)
                    waiting=any(task.interrupts for task in saved.tasks)
                    if waiting and row['phase']=='wait':
                        return True
                    if not saved.values:
                        graph_input={'workflow_id':workflow_id,'phase':row['phase']}
                    elif waiting:
                        graph_input=Command(resume={'persisted_decision':True})
                    else:
                        graph_input=None
                    graph.invoke(graph_input,config=config)
                return True
            except (OperationalError,psycopg.OperationalError,TimeoutError):
                self.service.record_failure(workflow_id,'transient','Temporary storage failure; retry after the recorded delay')
                return False
            except (LookupError,PermissionError):
                self.service.record_failure(workflow_id,'denied','Required access or business authority is unavailable')
                return False
            except ConflictError:
                self.service.record_failure(workflow_id,'conflict','State changed; review the saved workflow before starting another request')
                return False
            except Exception:
                self.service.record_failure(workflow_id,'internal','Workflow stopped safely; inspect its recorded progress')
                return False
            finally:
                lock.execute(text('SELECT pg_advisory_unlock(hashtextextended(:key, 0))'),
                             {'key':self.service.kind+'-workflow:'+workflow_id})
                lock.commit()
                # No secrets or reasoning are logged, only observed duration.
                from . import tables
                from sqlalchemy import insert
                from .service import now
                with self.service.db.engine.begin() as conn:
                    row=self.service._row(conn,workflow_id,lock=False)
                    conn.execute(insert(tables.events).values(workflow_id=workflow_id,organization_id=row['organization_id'],
                        kind='attempt_finished',payload={'duration_ms':round((time.monotonic()-started)*1000)},created_at=now()))
