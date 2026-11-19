"""Unit 5 business-state scenarios against real PostgreSQL and the HTTP/tool boundary."""
from concurrent.futures import ThreadPoolExecutor
import json
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from test_persistence import database, app, HEADERS, conversation, message
from test_agent import ScriptedModel, turn, call, final
from work_assistant.db.store import Database
from work_assistant.db.models import entities
from work_assistant.repository import FixtureRepository
from work_assistant.sla.service import SlaService
from work_assistant.sla import tables
from work_assistant.sla.domain import EditTicket
from work_assistant.tools import ToolGateway
from work_assistant.knowledge import KnowledgeService


def edit_body(ticket, **changes):
    return {k: v for k, v in ticket.items() if k in EditTicket.model_fields} | {
        'expected_version': ticket['version'], 'change_reason': 'Confirmed by sandbox support', **changes}


def test_case_queries_access_edit_restart_and_conflict(database):
    with TestClient(app(database)) as client:
        assert client.get('/v1/cases').status_code == 401
        assert {t['id'] for t in client.get('/v1/cases', headers=HEADERS).json()['tickets']} == {
            'INC-104', 'INC-105', 'INC-201', 'INC-202', 'INC-203'}
        assert client.get('/v1/cases?query=%25', headers=HEADERS).json()['tickets'] == []
        assert client.get('/v1/cases?account_id=other-account', headers=HEADERS).json()['tickets'] == []
        assert client.get('/v1/cases/INC-105', headers={'Authorization':'Bearer outside'}).status_code == 404
        case = client.get('/v1/cases/INC-105', headers=HEADERS).json()
        assert case['calculation']['proposal_amount'] == '45.00'
        body = edit_body(case['ticket'], title='Human edited title')
        assert client.patch('/v1/cases/INC-105', headers={'Authorization':'Bearer alex'}, json=body).status_code == 403
        assert client.patch('/v1/cases/INC-105', headers=HEADERS, json=body | {'assignee_id':'outsider'}).status_code == 404
        assert client.patch('/v1/cases/INC-105', headers=HEADERS, json=body | {'organization_id':'otherco'}).status_code == 422
        assert client.patch('/v1/cases/INC-105', headers=HEADERS, json=body | {'status':'closed'}).status_code == 422
        assert client.patch('/v1/cases/INC-105', headers=HEADERS, json=body).json()['version'] == 2
        assert client.patch('/v1/cases/INC-105', headers=HEADERS, json=body).status_code == 409
    database.seed(FixtureRepository.load().workspace)
    with TestClient(app(database)) as client:
        case = client.get('/v1/cases/INC-105', headers=HEADERS).json()
        assert case['ticket']['title'] == 'Human edited title'
        assert case['ticket']['version'] == 2
        assert client.get('/v1/cases/INC-105/events', headers=HEADERS).json()['events'][0]['payload']['reason']


def test_create_and_overlapping_incident_block_credit(database):
    with TestClient(app(database)) as client:
        old = client.get('/v1/cases/INC-105', headers=HEADERS).json()['ticket']
        body = {k:v for k,v in old.items() if k not in {'organization_id','updated_at','version'}} | {'id':'INC-106'}
        assert client.post('/v1/cases', headers=HEADERS, json=body | {'contract_id':'other-account-2026'}).status_code == 404
        assert client.post('/v1/cases', headers=HEADERS, json=body).status_code == 201
        assert client.post('/v1/cases', headers=HEADERS, json=body).status_code == 409
        result = client.get('/v1/cases/INC-105', headers=HEADERS).json()
        assert result['overlapping_ticket_ids'] == ['INC-106']
        assert result['calculation']['proposal_amount'] is None
        assert 'overlapping_incidents_require_consolidation' in result['calculation']['missing_inputs']


def test_concurrent_case_edits_have_one_winner(database):
    service = SlaService(database)
    body = EditTicket(**edit_body(service.detail('mei', 'INC-105')['ticket'], title='Concurrent edit'))
    def edit():
        from work_assistant.db.store import ConflictError
        try:
            return service.edit_ticket('mei','INC-105',body)['version']
        except ConflictError: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: edit(), range(2)))
    assert sorted(map(str, outcomes)) == ['2', 'conflict']


def test_private_memo_revisions_restore_export_and_stale_provenance(database, tmp_path):
    with TestClient(app(database, artifact_root=tmp_path)) as client:
        body = {'expected_ticket_version':1, 'language':'zh', 'hypotheses':'尚未确认根因'}
        response = client.post('/v1/cases/INC-105/memos', headers=HEADERS, json=body)
        assert response.status_code == 201, response.text
        memo = response.json(); mid = memo['id']
        assert '未发送' in memo['content'] and '45.00' in memo['content']
        assert client.get(f'/v1/memos/{mid}', headers={'Authorization':'Bearer alex'}).status_code == 404
        assert client.get('/v1/cases/INC-105', headers={'Authorization':'Bearer alex'}).json()['memos'] == []
        assert client.patch(f'/v1/memos/{mid}', headers=HEADERS, json={
            'expected_version':1, 'expected_ticket_version':1, 'hypotheses':'Changed hypothesis'}).json()['version'] == 2
        assert client.post(f'/v1/memos/{mid}/restore', headers=HEADERS, json={
            'expected_version':1, 'expected_ticket_version':1, 'revision':1}).status_code == 409
        restored = client.post(f'/v1/memos/{mid}/restore', headers=HEADERS, json={
            'expected_version':2, 'expected_ticket_version':1, 'revision':1}).json()
        assert restored['version'] == 3 and restored['notes']['hypotheses'] == '尚未确认根因'
        artifacts = []
        for format in ['md','docx','pdf','json']:
            response = client.post(f'/v1/memos/{mid}/exports', headers=HEADERS,
                json={'expected_version':3, 'format':format})
            assert response.status_code == 201, response.text
            artifact = response.json(); artifacts.append(artifact)
            assert client.get(artifact['download_url'], headers=HEADERS).status_code == 200
            assert client.get(artifact['download_url'], headers={'Authorization':'Bearer alex'}).status_code == 404
        ticket = client.get('/v1/cases/INC-105', headers=HEADERS).json()['ticket']
        assert client.patch('/v1/cases/INC-105', headers=HEADERS, json=edit_body(ticket, prior_credit_amount='40.00')).status_code == 200
        assert client.get(f'/v1/memos/{mid}', headers=HEADERS).json()['stale'] is True
        assert client.get(f'/v1/documents/memo:{mid}', headers=HEADERS).status_code == 404
        assert client.post(f'/v1/memos/{mid}/exports', headers=HEADERS, json={'expected_version':3}).status_code == 409
        for artifact in artifacts:
            assert client.get(artifact['download_url'], headers=HEADERS).status_code == 404
    from work_assistant.api import create_app
    with TestClient(create_app({'mei':'mei'}, model_factory=lambda:None, artifact_root=tmp_path)) as ephemeral:
        assert ephemeral.get(artifacts[0]['download_url'], headers=HEADERS).status_code == 404
    with TestClient(app(database, artifact_root=tmp_path)) as client:
        assert client.get(f'/v1/memos/{mid}', headers=HEADERS).json()['revisions'] == [1,2,3]


def test_tool_identity_risk_gate_and_missing_inputs(database):
    gateway = ToolGateway(KnowledgeService(database), 'mei', 'en')
    assert gateway.execute('calculate_sla_impact', {'ticket_id':'INC-104','employee_id':'alex'})['error']['code'] == 'INVALID_ARGUMENTS'
    assert gateway.execute('issue_credit', {'ticket_id':'INC-105','amount':'45'})['error']['code'] == 'APPROVAL_REQUIRED'
    assert gateway.execute('get_sla_case', {'ticket_id':'OTHER-1'})['error']['code'] == 'NOT_FOUND'
    result = gateway.execute('calculate_sla_impact', {'ticket_id':'INC-104'})
    calculation = json.loads(result['document']['content'])['calculation']
    assert calculation['proposal_amount'] is None and 'eligibility_confirmed' in calculation['missing_inputs']
    assert gateway.execute('write_file', {'filename':'case.md','content':'untracked'})['error']['code'] == 'USE_MEMO_EXPORT'
    assert gateway.execute('draft_incident_memo', {'ticket_id':'INC-104','expected_ticket_version':7})['error']['code'] == 'CONFLICT'
    with database.engine.begin() as conn:
        employee = database.employee('mei').model_copy(update={'active': False})
        conn.execute(update(entities['employees']).where(entities['employees'].c.id=='mei').values(payload=employee.model_dump(mode='json')))
    import pytest
    with pytest.raises(PermissionError): gateway.execute('get_sla_case', {'ticket_id':'INC-105'})


def test_conversation_case_tools_memo_download_trace_and_evidence_change(database, tmp_path):
    class CaseModel:
        async def turn(self, contents, language):
            results = [p.function_response for p in contents[-1].parts if p.function_response]
            if not results:
                return turn(call('calculate_sla_impact', {'ticket_id':'INC-105'}))
            result = results[0]
            if result.name == 'calculate_sla_impact':
                return turn(call('draft_incident_memo', {'ticket_id':'INC-105', 'expected_ticket_version':1}))
            if result.name == 'draft_incident_memo':
                return turn(call('export_incident_memo', {'memo_id':result.response['memo_id'], 'expected_version':1,'format':'docx'}))
            return final('Proposed credit USD 45.00; not issued.', [result.response['document']['id']])
        async def aclose(self): pass
    with TestClient(app(database, CaseModel(), artifact_root=tmp_path)) as client:
        cid = conversation(client)
        response = message(client, cid, question='Calculate INC-105 and create a Word memo')
        assert response.status_code == 200, response.text
        run = response.json()
        assert run['status'] == 'completed' and run['result']['tool_calls'] == 3
        assert [e['risk_tier'] for e in run['result']['tool_trace']] == [0,1,1]
        artifact = run['result']['files'][0]
        assert client.get(artifact['download_url'], headers=HEADERS).status_code == 200
        saved = client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()
        assert any(e['kind']=='sla_calculated' for e in saved['events'])
        assert any(e['kind']=='sla_memo_saved' for e in saved['events'])
        ticket = client.get('/v1/cases/INC-105', headers=HEADERS).json()['ticket']
        client.patch('/v1/cases/INC-105', headers=HEADERS, json=edit_body(ticket, eligibility_confirmed=False))
        assert client.get(f"/v1/runs/{run['id']}", headers=HEADERS).json()['result'] is None
        assert client.get(artifact['download_url'], headers=HEADERS).status_code == 404
        with database.engine.connect() as conn:
            # Unit 5 has no financial mutation; the underlying contract is unchanged.
            assert conn.execute(select(tables.contracts.c.version).where(tables.contracts.c.id=='acme-2026')).scalar_one() == 1


def test_artifact_creation_cannot_launder_uncited_business_claims(tmp_path):
    from work_assistant.api import create_app
    model = ScriptedModel([turn(call('write_file', {'filename':'notes.md','content':'User note'})),
                           final('We issued $100000 in credit.', [])])
    with TestClient(create_app({'mei':'mei'}, model_factory=lambda:model, artifact_root=tmp_path)) as client:
        result = client.post('/v1/agent/ask', headers=HEADERS, json={'question':'Make a note'}).json()
        assert result['answer'] == 'Created files: notes.md'


def test_account_exercises_distinguish_response_and_credit(database):
    cases = SlaService(database)
    larkspur = cases.detail('mei', 'INC-201')['calculation']
    assert (larkspur['response_status'], larkspur['eligible_minutes'], larkspur['proposal_amount']) == (
        'met', '120', '600.00')
    merefield = cases.detail('mei', 'INC-203')['calculation']
    assert (merefield['reason'], merefield['proposal_amount']) == ('confirmed_ineligible', '0.00')

    bracken = cases.detail('mei', 'INC-202')
    assert bracken['calculation']['response_status'] == 'breached'
    assert set(bracken['calculation']['missing_inputs']) == {'incident_ended_at', 'eligibility_confirmed'}
    cases.edit_ticket('mei', 'INC-202', EditTicket(**edit_body(
        bracken['ticket'], incident_ended_at='2026-11-14T18:48:00Z', eligibility_confirmed=True,
        change_reason='BM-72 reconciles the EU store receipts')))
    database.seed(FixtureRepository.load().workspace)
    assert cases.detail('mei', 'INC-202')['calculation']['proposal_amount'] == '192.00'
