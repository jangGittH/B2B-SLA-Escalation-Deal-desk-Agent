from fastapi.testclient import TestClient
from work_assistant.api import create_app


def test_ui_config_does_not_offer_impersonation_by_default():
    with TestClient(create_app({'mei-token':'mei'}, model_factory=lambda:None), base_url='http://127.0.0.1:8001') as client:
        assert client.get('/v1/ui/config').json() == {'demo_mode':False, 'employees':[]}
        assert client.post('/v1/demo/session', json={'employee_id':'mei'}).status_code == 404
        assert client.get('/v1/workspace').status_code == 401


def test_demo_selection_requires_explicit_mode_and_local_origin():
    with TestClient(create_app({'mei-token':'mei'}, model_factory=lambda:None, demo_mode=True), base_url='http://127.0.0.1:8001') as client:
        config = client.get('/v1/ui/config').json()
        assert config['demo_mode'] and all(e['id']!='former' for e in config['employees'])
        assert 'mei-token' not in str(config)
        response = client.post('/v1/demo/session', json={'employee_id':'mei'}, headers={'Origin':'http://127.0.0.1:8001'})
        assert response.json()['token'] == 'mei-token'
        for origin in ['https://attacker.example', 'null', 'http://evil.localhost:8001']:
            assert client.post('/v1/demo/session', json={'employee_id':'mei'}, headers={'Origin':origin}).status_code == 403
        assert client.post('/v1/demo/session', json={'employee_id':'former'}).status_code == 404
        assert client.post('/v1/demo/session', json={'employee_id':'mei'}, headers={'Host':'attacker.example'}).status_code == 403


def test_workspace_lists_only_authorized_documents_and_projects():
    with TestClient(create_app({'mei-token':'mei','alex-token':'alex','other-token':'outsider'}, model_factory=lambda:None)) as client:
        def workspace(token):
            return client.get('/v1/workspace?language=zh', headers={'Authorization':f'Bearer {token}'}).json()
        mei, alex, other = workspace('mei-token'), workspace('alex-token'), workspace('other-token')
        assert mei['identity']['name'] == '陈梅'
        assert mei['agent_available'] is False
        assert 'inc-104-runbook' in {d['id'] for d in mei['documents']}
        assert 'inc-104-runbook' not in {d['id'] for d in alex['documents']}
        assert 'AURORA-PRIVATE-742' not in str(alex)
        assert {d['id'] for d in other['documents']} == {'other-account-policy'}
        assert {p['id'] for p in other['projects']} == {'other-account'}
        assert client.get('/v1/workspace?language=fr', headers={'Authorization':'Bearer mei-token'}).status_code == 422


def test_retired_workflow_routes_are_absent():
    with TestClient(create_app({'token': 'mei'}, model_factory=lambda: None)) as client:
        for path in ['/v1/workflows/meeting-brief', '/v1/runs/meeting-brief', '/v1/runs/old/resume']:
            assert client.post(path, headers={'Authorization': 'Bearer token'}, json={}).status_code == 404
        assert not any('meeting' in path for path in client.get('/openapi.json').json()['paths'])
