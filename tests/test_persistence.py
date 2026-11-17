"""Real PostgreSQL integration tests. Opt in with TEST_DATABASE_URL."""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.engine import make_url

from work_assistant.api import create_app
from work_assistant.db.models import entities, runs
from work_assistant.db.store import Database, migrate
from work_assistant.repository import FixtureRepository
from test_agent import ScriptedModel, turn, call, final

HEADERS = {'Authorization': 'Bearer mei'}


@pytest.fixture
def database():
    base = os.getenv('TEST_DATABASE_URL')
    if not base:
        pytest.skip('Set TEST_DATABASE_URL to run PostgreSQL integration tests')
    name = 'northstar_test_' + uuid4().hex
    admin = create_engine(base, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    db = Database(url)
    try:
        migrate(url)
        db.seed(FixtureRepository.load().workspace)
        yield db
    finally:
        db.close()
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def app(db, model=None, **kwargs):
    return create_app({'mei': 'mei', 'alex': 'alex', 'outside': 'outsider'},
                      model_factory=lambda: model, database_url=db.url, **kwargs)


def conversation(client):
    response = client.post('/v1/conversations', json={'title': 'SLA questions'}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()['id']


def message(client, cid, key='first', question='Acme SLA?'):
    return client.post(f'/v1/conversations/{cid}/messages', headers=HEADERS,
                       json={'question': question, 'request_key': key})


def test_saved_chat_restart_history_duplicate_and_access(database):
    model = ScriptedModel([final('Which customer case?', [], 'needs_clarification')])
    with TestClient(app(database, model)) as client:
        cid = conversation(client)
        result = message(client, cid)
        assert result.status_code == 200, result.text
        row = result.json()
        assert row['status'] == 'waiting_for_input'
        assert message(client, cid).json()['id'] == row['id']
        assert message(client, cid, question='different').status_code == 409
        assert len(model.requests) == 1
        for path in [f'/v1/conversations/{cid}', f'/v1/runs/{row["id"]}']:
            assert client.get(path).status_code == 401
            for token in ['alex', 'outside']:
                assert client.get(path, headers={'Authorization': f'Bearer {token}'}).status_code == 404
    followup = ScriptedModel([turn(call('read_document', {'document_id': 'acme-sla'})), final()])
    with TestClient(app(database, followup)) as client:
        history = client.get(f'/v1/conversations/{cid}', headers=HEADERS).json()
        assert len(history['runs']) == 1
        assert history['runs'][0]['result']['answer'] == 'Which customer case?'
        result = message(client, cid, key='second', question='Acme')
        assert result.status_code == 200, result.text
        texts = [p.text for c in followup.requests[0][0] for p in c.parts if p.text]
        assert [t for t in texts if not t.startswith('BACKGROUND CONTEXT')] == ['Acme SLA?', 'Which customer case?', 'Acme']
        assert any('fixed_tool_loop' in t for t in texts)
        assert result.json()['status'] == 'completed'
        assert len(client.get('/v1/conversations', headers={'Authorization': 'Bearer alex'}).json()['conversations']) == 0


def test_seed_is_idempotent_and_does_not_reset_permissions(database):
    employee = database.employee('mei').model_copy(update={'active': False})
    with database.engine.begin() as conn:
        conn.execute(update(entities['employees']).where(entities['employees'].c.id == 'mei').values(payload=employee.model_dump(mode='json')))
    database.seed(FixtureRepository.load().workspace)
    assert database.employee('mei').active is False


def test_concurrent_turn_and_duplicate_do_not_call_model_twice(database):
    started, release = Event(), Event()
    class WaitingModel:
        calls = 0
        async def turn(self, contents, language):
            self.calls += 1
            started.set()
            while not release.is_set():
                await anyio.sleep(.005)
            return final('Which customer case?', [], 'needs_clarification')
        async def aclose(self): pass
    model = WaitingModel()
    with TestClient(app(database, model, max_concurrent=3)) as client:
        cid = conversation(client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(message, client, cid)
            try:
                assert started.wait(5)
                assert message(client, cid, key='another').status_code == 409
                duplicate = message(client, cid)
                assert duplicate.status_code == 200
                assert duplicate.json()['status'] == 'running'
            finally:
                release.set()
            assert first.result(5).status_code == 200
        assert model.calls == 1


def test_chat_files_survive_response_failure(database, tmp_path):
    model = ScriptedModel([turn(call('write_file', {'filename': 'saved.md', 'content': 'saved'})),
                           turn() , turn()])
    with TestClient(app(database, model, artifact_root=tmp_path)) as client:
        cid = conversation(client)
        response = message(client, cid)
        assert response.status_code == 200, response.text
        row = response.json()
        assert row['status'] == 'failed' and len(row['files']) == 1
        assert client.get(row['files'][0]['download_url'], headers=HEADERS).text == 'saved'
        assert client.post(f'/v1/runs/{row["id"]}/resume', headers=HEADERS, json={}).status_code == 404


def test_revoked_source_blocks_saved_file_and_model_history(database, tmp_path):
    model = ScriptedModel([
        turn(call('read_document', {'document_id': 'inc-104-runbook'})),
        turn(call('write_file', {'filename': 'private.txt', 'content': 'private summary'})),
        final('File prepared.', []),
    ])
    with TestClient(app(database, model, artifact_root=tmp_path)) as client:
        cid = conversation(client)
        row = message(client, cid).json()
        file = row['files'][0]
        assert client.get(file['download_url'], headers=HEADERS).status_code == 200
        doc = database.document('inc-104-runbook').model_copy(update={
            'reader_team_ids': (), 'reader_employee_ids': ('alex',)})
        with database.engine.begin() as conn:
            conn.execute(update(entities['documents']).where(entities['documents'].c.id == doc.id).values(payload=doc.model_dump(mode='json')))
        assert client.get(file['download_url'], headers=HEADERS).status_code == 404
        saved = client.get(f'/v1/conversations/{cid}', headers=HEADERS).json()['runs'][0]
        assert saved['result'] is None
        assert saved['files'] == []
    next_model = ScriptedModel([final('Which customer case?', [], 'needs_clarification')])
    with TestClient(app(database, next_model, artifact_root=tmp_path)) as client:
        assert message(client, cid, key='next').status_code == 200
        assert len(next_model.requests[0][0]) == 1


def test_provider_usage_is_saved_without_prompt_or_secret(database):
    from unittest.mock import AsyncMock
    from google.genai import types
    from work_assistant.agent import GeminiModel
    model = object.__new__(GeminiModel)
    model.model = 'unit4-test'
    model.client = AsyncMock()
    model.client.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[types.Candidate(content=final('Which customer case?', [], 'needs_clarification'))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=20,
            candidates_token_count=10, total_token_count=30))
    with TestClient(app(database, model)) as client:
        row = message(client, conversation(client)).json()
        event = next(e for e in row['events'] if e['kind'] == 'model_call')
        assert event['payload']['model'] == 'unit4-test'
        assert event['payload']['usage']['total_token_count'] == 30
        assert event['payload']['cost_usd'] is None
        assert event['payload']['duration_ms'] >= 0
        assert 'prompt' not in event['payload']


def test_deactivated_employee_cannot_load_history(database):
    with TestClient(app(database)) as client:
        cid = conversation(client)
        employee = database.employee('mei').model_copy(update={'active': False})
        with database.engine.begin() as conn:
            conn.execute(update(entities['employees']).where(entities['employees'].c.id == employee.id).values(payload=employee.model_dump(mode='json')))
        assert client.get(f'/v1/conversations/{cid}', headers=HEADERS).status_code == 401
        assert client.get('/v1/runs', headers=HEADERS).status_code == 401


def test_retired_runs_are_not_exposed(database):
    actor = database.employee('mei')
    row, _ = database.submit(actor, 'meeting', {}, 'retired-history', None, 120)
    with TestClient(app(database)) as client:
        assert client.get(f'/v1/runs/{row["id"]}', headers=HEADERS).status_code == 404
        assert client.get('/v1/runs', headers=HEADERS).json()['runs'] == []
        assert client.get('/v1/runs?kind=meeting', headers=HEADERS).status_code == 422


def test_cleanup_migration_retires_seed_and_preserves_custom_records(database):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import insert, select
    from work_assistant.models import Document

    # A canonical legacy seed row, as written by the previous repository seed.
    legacy = Document.model_validate({
        'id': 'handbook', 'organization_id': 'northstar', 'project_id': None,
        'title': {'en': 'Employee handbook', 'zh': '员工手册'},
        'content': {
            'en': 'Northstar Labs is a fictional company. Employees collaborate through Engineering, Sales, and Operations. Meeting notes must distinguish decisions from suggestions. Ask before assigning an owner who made no commitment.',
            'zh': '北辰实验室是一家虚构公司。研发、销售和运营团队共同协作。会议纪要必须区分已确定的决策和建议。没有明确承诺时，分配负责人前应先确认。'},
        'version': 1, 'updated_at': '2026-11-10T08:00:00Z', 'visibility': 'organization',
        'reader_team_ids': [], 'reader_employee_ids': [],
    }).model_dump(mode='json')
    custom = {**legacy, 'id': 'atlas-overview', 'version': 7, 'project_id': 'atlas'}
    project = {'id': 'atlas', 'organization_id': 'northstar',
               'name': {'en': 'Atlas employee portal', 'zh': 'Atlas 员工门户'}}
    with database.engine.begin() as conn:
        conn.execute(insert(entities['projects']).values(id='atlas', organization_id='northstar', payload=project))
        for payload in [legacy, custom]:
            conn.execute(insert(entities['documents']).values(id=payload['id'], organization_id='northstar', payload=payload))
    actor = database.employee('mei')
    cid = database.create_conversation(actor, 'Custom conversation', 'en')['id']
    # Simulate an existing 0001 database with data, then run the actual upgrade.
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    config.attributes['database_url'] = database.url
    command.downgrade(config, '0001')
    migrate(database.url)
    database.seed(FixtureRepository.load().workspace)
    assert database.document('handbook') is None
    assert database.document('atlas-overview').version == 7
    assert any(p.id == 'atlas' for p in database.projects())
    assert database.document('acme-sla') is not None
    assert database.conversation(actor, cid)['title'] == 'Custom conversation'


def test_chat_timeout_records_interruption_without_replaying_writes(database):
    class SlowModel:
        async def turn(self, contents, language):
            await anyio.sleep(10)
        async def aclose(self): pass
    with TestClient(app(database, SlowModel(), request_timeout=.05)) as client:
        cid = conversation(client)
        assert message(client, cid).status_code == 504
        saved = client.get(f'/v1/conversations/{cid}', headers=HEADERS).json()['runs'][0]
        assert saved['status'] == 'interrupted'
        assert 'saved files' in saved['error']
        assert message(client, cid).json()['id'] == saved['id']
    model = ScriptedModel([final('Which customer case?', [], 'needs_clarification')])
    with TestClient(app(database, model)) as client:
        assert message(client, cid, key='new-explicit-turn').status_code == 200
        assert len(model.requests) == 1


def test_history_validation_does_not_copy_reads_into_followup(database):
    from sqlalchemy import select, func
    from work_assistant.db.models import events
    model = ScriptedModel([
        turn(call('read_document', {'document_id': 'acme-sla'})), final(),
        final('Which case should I investigate?', [], 'needs_clarification'),
    ])
    with TestClient(app(database, model)) as client:
        cid = conversation(client)
        first = message(client, cid).json()
        # Simulate an older conversation with many duplicate audit records.
        reference = database.evidence_references(first['id'])[0]
        for _ in range(25): database.event(first['id'], 'evidence_read', reference)
        assert database.evidence_references(first['id']) == [reference]
        second = message(client, cid, key='followup', question='Help with another case').json()
        assert second['status'] == 'waiting_for_input'
        with database.engine.connect() as c:
            assert c.execute(select(func.count()).select_from(events).where(
                events.c.run_id == second['id'], events.c.kind == 'evidence_read')).scalar_one() == 0
        history = client.get(f'/v1/conversations/{cid}', headers=HEADERS).json()
        assert len(history['runs']) == 2 and history['runs'][0]['result']
        assert all(row['events'] == [] for row in history['runs'])
        # Full audit remains available through the owned run endpoint.
        assert client.get(f'/v1/runs/{first["id"]}', headers=HEADERS).json()['events']
        changed = dict(reference, version=reference['version'] + 1)
        database.event(first['id'], 'evidence_read', changed)
        assert len(database.evidence_references(first['id'])) == 2
        history = client.get(f'/v1/conversations/{cid}', headers=HEADERS).json()
        assert history['runs'][0]['result'] is None
