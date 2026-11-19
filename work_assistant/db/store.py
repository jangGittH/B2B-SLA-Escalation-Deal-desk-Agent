"""Short database transactions; no transaction is held during model calls."""
import hashlib
import json
import os
import time
from uuid import uuid4
from pathlib import Path

from sqlalchemy import create_engine, select, insert, update
from alembic import command
from alembic.config import Config

from .models import metadata, organizations, entities, conversations, runs, messages, events, artifacts
from ..models import Employee, Document, Project, Organization, Workspace

DEFAULT_DATABASE_URL = 'postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar'


def database_url():
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv('WORK_DATABASE_URL', DEFAULT_DATABASE_URL)


def migrate(url):
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    config.attributes['database_url'] = url
    command.upgrade(config, 'head')
    from ..workflows.engine import setup_checkpoints
    setup_checkpoints(url)


class ConflictError(Exception):
    pass


class Database:
    def __init__(self, url):
        self.url = url
        self.engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5,
                                    connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})

    def close(self):
        self.engine.dispose()

    def seed(self, workspace: Workspace):
        """Insert missing fixture records only; never overwrite changed permissions."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        with self.engine.begin() as connection:
            for table, records in [(organizations, workspace.organizations), *[
                    (entities[name], getattr(workspace, name)) for name in entities]]:
                for record in records:
                    values = {'id': record.id, 'payload': record.model_dump(mode='json')}
                    if table is not organizations:
                        values['organization_id'] = record.organization_id
                    connection.execute(pg_insert(table).values(**values).on_conflict_do_nothing())

            from ..sla.seed import seed
            seed(connection, workspace)
            from ..deals.seed import seed as seed_deals
            seed_deals(connection, workspace)
            from .practice_seed import seed as seed_practice
            seed_practice(connection, workspace)

    def _record(self, table, record_id, model):
        with self.engine.connect() as connection:
            payload = connection.execute(select(table.c.payload).where(table.c.id == record_id)).scalar_one_or_none()
        return model.model_validate(payload) if payload else None

    def _records(self, table, model):
        with self.engine.connect() as connection:
            payloads = connection.execute(select(table.c.payload).order_by(table.c.id)).scalars().all()
        return tuple(model.model_validate(payload) for payload in payloads)

    def employee(self, employee_id):
        return self._record(entities['employees'], employee_id, Employee)

    def document(self, document_id):
        return self._record(entities['documents'], document_id, Document)

    def documents(self):
        return self._records(entities['documents'], Document)

    def employees(self):
        return self._records(entities['employees'], Employee)

    def projects(self):
        return self._records(entities['projects'], Project)

    def organization(self, organization_id):
        return self._record(organizations, organization_id, Organization)

    def _owned(self, connection, table, resource_id, actor):
        row = connection.execute(select(table).where(table.c.id == resource_id,
            table.c.employee_id == actor.id, table.c.organization_id == actor.organization_id)).mappings().first()
        if row is None:
            raise LookupError('Resource not found')
        return dict(row)

    def _lock_actor(self, connection, actor):
        # Serializes submissions across tabs/processes, and revalidates identity.
        row = connection.execute(select(entities['employees'].c.payload).where(
            entities['employees'].c.id == actor.id).with_for_update()).scalar_one_or_none()
        current = Employee.model_validate(row) if row else None
        if current is None or not current.active or current.organization_id != actor.organization_id:
            raise PermissionError('Employee unavailable')

    def create_conversation(self, actor, title, language):
        now = time.time()
        row = dict(id=uuid4().hex, employee_id=actor.id, organization_id=actor.organization_id,
                   title=title, language=language, created_at=now, updated_at=now)
        with self.engine.begin() as connection:
            self._lock_actor(connection, actor)
            connection.execute(insert(conversations).values(**row))
        return row

    def list_conversations(self, actor):
        with self.engine.connect() as connection:
            return [dict(r) for r in connection.execute(select(conversations).where(
                conversations.c.employee_id == actor.id, conversations.c.organization_id == actor.organization_id
            ).order_by(conversations.c.updated_at.desc()).limit(100)).mappings()]

    def conversation(self, actor, conversation_id):
        with self.engine.connect() as connection:
            row = self._owned(connection, conversations, conversation_id, actor)
            row['runs'] = [dict(r) for r in connection.execute(select(runs).where(
                runs.c.conversation_id == conversation_id).order_by(runs.c.created_at, runs.c.id)).mappings()]
        return row

    def _event(self, connection, run_id, kind, payload):
        connection.execute(insert(events).values(run_id=run_id, kind=kind, payload=payload, created_at=time.time()))

    def event(self, run_id, kind, payload):
        with self.engine.begin() as connection:
            self._event(connection, run_id, kind, payload)

    def submit(self, actor, kind, payload, request_key, conversation_id, lease_seconds):
        digest = hashlib.sha256(json.dumps([kind, conversation_id, payload], sort_keys=True,
                                          ensure_ascii=False).encode()).hexdigest()
        now = time.time()
        with self.engine.begin() as connection:
            self._lock_actor(connection, actor)
            if conversation_id:
                self._owned(connection, conversations, conversation_id, actor)
            existing = connection.execute(select(runs).where(runs.c.employee_id == actor.id,
                                          runs.c.request_key == request_key)).mappings().first()
            if existing:
                if existing['payload_hash'] != digest:
                    raise ConflictError('Request key was already used for different content')
                return dict(existing), False
            if conversation_id:
                active = connection.execute(select(runs.c.id).where(runs.c.conversation_id == conversation_id,
                    runs.c.status == 'running', runs.c.lease_until > now)).first()
                if active:
                    raise ConflictError('Another turn is running in this conversation')
                # Interrupted chats are not silently retried (they may have written files).
                connection.execute(update(runs).where(runs.c.conversation_id == conversation_id,
                    runs.c.status == 'running', runs.c.lease_until <= now).values(status='interrupted',
                    error='Execution interrupted. Review saved files before submitting a new turn.'))
            row = dict(id=uuid4().hex, organization_id=actor.organization_id, employee_id=actor.id,
                       conversation_id=conversation_id, kind=kind, request_key=request_key,
                       payload_hash=digest, request=payload, status='running', result=None, error=None,
                       lease_token=uuid4().hex, lease_until=now + lease_seconds, created_at=now, updated_at=now)
            connection.execute(insert(runs).values(**row))
            if conversation_id:
                connection.execute(insert(messages).values(id=uuid4().hex, conversation_id=conversation_id,
                    run_id=row['id'], question=payload['question'], created_at=now))
                connection.execute(update(conversations).where(conversations.c.id == conversation_id).values(updated_at=now))
            self._event(connection, row['id'], 'started', {'kind': kind})
        return row, True

    def get_run(self, actor, run_id, include_events=True):
        with self.engine.begin() as connection:
            row = self._owned(connection, runs, run_id, actor)
            if row['kind'] != 'chat':
                raise LookupError('Run not found')
            if row['status'] == 'running' and row['lease_until'] <= time.time():
                connection.execute(update(runs).where(runs.c.id == run_id, runs.c.lease_token == row['lease_token'],
                    runs.c.status == 'running', runs.c.lease_until <= time.time()).values(status='interrupted',
                    error='Execution interrupted; review saved files before submitting a new turn.'))
                row = self._owned(connection, runs, run_id, actor)
            row['events'] = [dict(e) for e in connection.execute(select(events).where(
                events.c.run_id == run_id).order_by(events.c.id)).mappings()] if include_events else []
            row['files'] = [a for a in connection.execute(select(artifacts.c.payload).where(
                artifacts.c.run_id == run_id, artifacts.c.employee_id == actor.id)).scalars()]
        return row

    def list_runs(self, actor, kind):
        with self.engine.connect() as connection:
            return [dict(r) for r in connection.execute(select(runs).where(runs.c.employee_id == actor.id,
                runs.c.organization_id == actor.organization_id, runs.c.kind == kind
            ).order_by(runs.c.created_at.desc()).limit(100)).mappings()]


    def finish(self, run, status, result=None, error=None):
        with self.engine.begin() as connection:
            changed = connection.execute(update(runs).where(runs.c.id == run['id'],
                runs.c.status == 'running', runs.c.lease_token == run['lease_token']).values(
                status=status, result=result, error=error, lease_until=None, updated_at=time.time())).rowcount
            if changed:
                self._event(connection, run['id'], status, {'error': error} if error else {})
            return bool(changed)

    def register_artifact(self, employee_id, artifact, run_id=None):
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        with self.engine.begin() as connection:
            connection.execute(pg_insert(artifacts).values(id=artifact.id, employee_id=employee_id,
                run_id=run_id, payload=artifact.model_dump(), created_at=time.time()).on_conflict_do_nothing())

    def evidence_references(self, run_id):
        with self.engine.connect() as connection:
            records = connection.execute(select(events.c.kind, events.c.payload).where(
                events.c.run_id == run_id, events.c.kind.in_(['evidence_read', 'evidence_reset'])).order_by(events.c.id)).all()
        references = {}
        for kind, payload in records:
            # Only the server's read-only replan path emits a reset, before any artifact
            # or workflow mutation. Original events remain append-only for audit.
            if kind == 'evidence_reset':
                references = {json.dumps(ref, sort_keys=True): ref for ref in payload['references']}
            else:
                references[json.dumps(payload, sort_keys=True)] = payload
        # Keep every distinct fingerprint, including conflicting old versions, but
        # authorize repeated reads only once. Historical audit rows stay intact.
        return list(references.values())

    def artifact_run(self, employee_id, artifact_id):
        with self.engine.connect() as connection:
            return connection.execute(select(artifacts.c.run_id).where(
                artifacts.c.employee_id == employee_id, artifacts.c.id == artifact_id)).scalar_one_or_none()

    def has_artifact(self, employee_id, artifact_id):
        with self.engine.connect() as connection:
            return connection.execute(select(artifacts.c.id).where(
                artifacts.c.employee_id == employee_id, artifacts.c.id == artifact_id)).scalar_one_or_none() is not None
