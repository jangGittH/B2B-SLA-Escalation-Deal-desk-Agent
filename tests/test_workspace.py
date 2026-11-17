"""Behavioral checks for Unit 1's isolation boundary and bilingual knowledge function."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from work_assistant.api import create_app
from work_assistant.knowledge import KnowledgeService
from work_assistant.models import Workspace
from work_assistant.repository import FixtureRepository


@pytest.fixture
def repository():
    return FixtureRepository.load()


@pytest.fixture
def client(repository):
    with TestClient(create_app({'mei-key': 'mei', 'alex-key': 'alex',
                                'lin-key': 'lin', 'other-key': 'outsider'}, repository)) as client:
        yield client


def auth(token='mei-key'):
    return {'Authorization': f'Bearer {token}'}


def search(client, token='mei-key', query='Acme', language='en', limit=20):
    return client.post('/v1/knowledge/search', headers=auth(token),
                       json={'query': query, 'language': language, 'limit': limit})


def ids(response):
    assert response.status_code == 200
    return {hit['document']['id'] for hit in response.json()['hits']}


def test_employees_see_different_results(client):
    mei = ids(search(client))
    alex = ids(search(client, 'alex-key'))
    assert mei == {'acme-sla', 'inc-104-runbook', 'inc-104-history', 'engineer-notes'}
    assert alex == {'acme-sla', 'acme-deal', 'inc-104-history'}
    assert 'credit-policy' in ids(search(client, 'lin-key'))


def test_tenant_isolation_includes_organization_visible_documents(client):
    assert ids(search(client, 'other-key', query='response')) == {'other-account-policy'}
    assert not search(client, query='OTHER-ORG-SECRET-915').json()['hits']
    assert 'OTHER-ORG-SECRET-915' not in search(client).text


@pytest.mark.parametrize('document_id', ['inc-104-runbook', 'engineer-notes', 'credit-policy',
                                         'other-account-policy', 'does-not-exist'])
def test_forbidden_and_missing_documents_have_same_response(client, document_id):
    response = client.get(f'/v1/documents/{document_id}', headers=auth('alex-key'))
    assert response.status_code == 404
    assert response.json() == {'detail': 'Document not found'}


def test_secret_query_never_returns_forbidden_evidence(client):
    other_hits = search(client, 'alex-key', 'AURORA-PRIVATE-742').json()['hits']
    assert 'AURORA-PRIVATE-742' not in str(other_hits)
    assert all(hit['document']['id'] != 'inc-104-runbook' for hit in other_hits)
    response = search(client, query='AURORA-PRIVATE-742')
    assert 'inc-104-runbook' in ids(response)
    source = next(hit['document'] for hit in response.json()['hits'] if hit['document']['id'] == 'inc-104-runbook')
    assert 'AURORA-PRIVATE-742' in source['content']


def test_bilingual_queries_outputs_and_citation(client):
    chinese = search(client, query='响应时限', language='zh')
    assert 'acme-sla' in ids(chinese)
    hit = next(h for h in chinese.json()['hits'] if h['document']['id'] == 'acme-sla')
    assert hit['document']['title'] == 'Acme SLA 条款'
    assert '60 分钟' in hit['document']['content']
    cited = client.get(hit['document']['source_url'], headers=auth())
    assert cited.status_code == 200
    assert cited.json() == hit['document']
    assert 'acme-sla' in ids(search(client, query='response target'))
    # Both language fields are searchable regardless of requested display language.
    assert 'acme-sla' in ids(search(client, query='响应时限', language='en'))


def test_no_match_has_no_fabricated_answer(client):
    result = search(client, query='unicornspaceflight').json()
    assert result['hits'] == []
    assert result['method'] == 'bilingual_lexical_baseline'
    assert 'answer' not in result


@pytest.mark.parametrize('path,method', [('/v1/me','get'), ('/v1/documents/operating-guide','get'),
                                         ('/v1/knowledge/search','post')])
def test_all_employee_endpoints_require_authentication(client, path, method):
    kwargs = {'json': {'query': 'Acme'}} if method == 'post' else {}
    for headers in ({}, auth('invalid')):
        response = getattr(client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 401


def test_identity_cannot_be_overridden_by_request(client):
    response = client.post('/v1/knowledge/search', headers=auth('alex-key'),
                           json={'query': 'Acme', 'employee_id': 'mei'})
    assert response.status_code == 422
    identity = client.get('/v1/me?language=zh', headers=auth('alex-key')).json()
    assert identity['employee_id'] == 'alex'
    assert identity['name'] == '王安'


@pytest.mark.parametrize('body', [{'query':'   '}, {'query':'x','limit':0},
                                 {'query':'x','limit':21}, {'query':'x','language':'fr'},
                                 {'query':'x'*2001}])
def test_search_input_validation(client, body):
    assert client.post('/v1/knowledge/search', headers=auth(), json=body).status_code == 422


def test_inactive_employee_rejected_at_service_and_api_configuration(repository):
    with pytest.raises(PermissionError):
        KnowledgeService(repository).search('former', 'Acme', 'en')
    with pytest.raises(ValueError):
        create_app({'token':'former'}, repository)
    with pytest.raises(ValueError):
        create_app({}, repository)


@pytest.mark.parametrize('mutation', ['duplicate', 'wrong_org', 'grant', 'project', 'timezone'])
def test_fixture_rejects_inconsistent_relationships(repository, mutation):
    data = repository.workspace.model_dump(mode='json')
    if mutation == 'duplicate':
        data['employees'].append(data['employees'][0])
    elif mutation == 'wrong_org':
        data['employees'][0]['team_ids'] = ['external-team']
    elif mutation == 'grant':
        data['documents'][2]['reader_team_ids'] = ['external-team']
    elif mutation == 'project':
        data['documents'][2]['project_id'] = 'other-account'
    else:
        data['documents'][0]['updated_at'] = '2026-11-10T08:00:00'
    with pytest.raises(ValidationError):
        Workspace.model_validate(data)


def test_permissions_applied_before_limit(client):
    result = search(client, 'alex-key', query='Acme incident investigation runbook', limit=1)
    assert len(ids(result)) == 1
    assert 'inc-104-runbook' not in ids(result)


def test_multiple_clients_do_not_share_identity(client):
    for _ in range(3):
        assert 'inc-104-runbook' in ids(search(client))
        assert 'inc-104-runbook' not in ids(search(client, 'alex-key'))
