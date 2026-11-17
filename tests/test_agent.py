"""Deterministic model transcripts exercise the real loop, gateway and HTTP boundary."""

import asyncio
import json
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi.testclient import TestClient
from google.genai import types

from work_assistant.agent import AgentRequest, GeminiModel, KnowledgeAgent
from work_assistant.api import create_app
from work_assistant.knowledge import KnowledgeService
from work_assistant.repository import FixtureRepository
from work_assistant.tools import ToolGateway


def call(name, args, id='call-1'):
    return types.Part(function_call=types.FunctionCall(name=name, args=args, id=id),
                      thought_signature=b'preserve-me')


def turn(*parts):
    return types.Content(role='model', parts=list(parts))


def final(answer='The initial response target is 60 minutes.', citations=None, status='answered'):
    return turn(types.Part(text=json.dumps(dict(status=status, answer=answer,
                                                citations=['acme-sla'] if citations is None else citations))))


class ScriptedModel:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []
        self.closed = False

    async def turn(self, contents, language):
        self.requests.append((list(contents), language))
        return next(self.turns)

    async def aclose(self):
        self.closed = True


def run(turns, employee='mei', language='en', **budgets):
    model = ScriptedModel(turns)
    agent = KnowledgeAgent(KnowledgeService(FixtureRepository.load()), model, **budgets)
    response = asyncio.run(agent.ask(employee, AgentRequest(question='Acme SLA?', language=language)))
    return response, model


def test_search_read_answer_and_original_signature_preserved():
    search = turn(call('search_knowledge', {'query':'Acme SLA'}))
    read = turn(call('read_document', {'document_id':'acme-sla'}, 'read-2'))
    response, model = run([search, read, final()], language='zh')
    assert response.status == 'answered'
    assert response.model_calls == 3 and response.tool_calls == 2
    assert response.sources[0].title == 'Acme SLA 条款'
    assert response.sources[0].source_url == '/v1/documents/acme-sla?language=zh'
    assert model.requests[1][0][1] is search
    assert model.requests[1][0][1].parts[0].thought_signature == b'preserve-me'
    assert model.requests[2][0][-1].parts[0].function_response.id == 'read-2'
    assert all(language == 'zh' for _, language in model.requests)


def test_tool_batch_keeps_all_ids_and_returns_each_result():
    response, model = run([turn(call('read_document', {'document_id':'acme-sla'}, 'a'),
                                call('read_document', {'document_id':'inc-104-history'}, 'b')), final()])
    results = model.requests[1][0][-1].parts
    assert [part.function_response.id for part in results] == ['a', 'b']
    assert response.tool_calls == 2


@pytest.mark.parametrize('name,args,code', [
    ('read_document', {'document_id':'inc-104-runbook'}, 'NOT_FOUND'),
    ('read_document', {'document_id':'other-account-policy'}, 'NOT_FOUND'),
    ('read_document', {'document_id':'inc-104-runbook','employee_id':'mei'}, 'INVALID_ARGUMENTS'),
    ('search_knowledge', {'query':'Acme','limit':500}, 'INVALID_ARGUMENTS'),
    ('search_knowledge', {'query':'Acme','organization_id':'otherco'}, 'INVALID_ARGUMENTS'),
    ('send_message', {'text':'hello'}, 'UNKNOWN_TOOL'),
])
def test_tool_failures_return_safe_errors(name, args, code):
    response, model = run([turn(call(name,args)), final('Please specify your project.', [], 'needs_clarification')], employee='alex')
    result = model.requests[1][0][-1].parts[0].function_response.response
    assert result['error']['code'] == code
    assert response.sources == []
    assert 'AURORA-PRIVATE-742' not in json.dumps(result)
    assert 'OTHER-ORG-SECRET-915' not in json.dumps(result)


def test_model_can_repair_bad_tool_arguments():
    response, _ = run([turn(call('read_document', {'id':'acme-sla'})),
                       turn(call('read_document', {'document_id':'acme-sla'})), final()])
    assert [event.outcome for event in response.tool_trace] == ['INVALID_ARGUMENTS','ok']
    assert response.status == 'answered'


@pytest.mark.parametrize('citations', [[], ['acme-sla'], ['invented-document']])
def test_final_cannot_cite_unread_or_missing_evidence(citations):
    response, _ = run([final(citations=citations), final(citations=citations)])
    assert response.status == 'invalid_response'
    assert response.sources == []
    assert response.model_calls == 2


def test_search_preview_is_not_a_read_citation():
    response, _ = run([turn(call('search_knowledge', {'query':'Acme'})), final(), final()])
    assert response.status == 'invalid_response'


def test_unknown_question_can_report_no_evidence_after_search():
    response, _ = run([turn(call('search_knowledge', {'query':'unicornspaceship'})),
                       final('No available evidence.', [], 'not_found')])
    assert response.status == 'not_found' and response.sources == []


def test_empty_or_malformed_model_answers_stop_after_one_repair():
    response, _ = run([turn(types.Part(text='not JSON')), turn()])
    assert response.status == 'invalid_response'


def test_thought_text_is_not_returned():
    response, _ = run([turn(call('read_document', {'document_id':'acme-sla'})),
                       turn(types.Part(text='private reasoning', thought=True), *final().parts)])
    assert response.status == 'answered'
    assert 'private reasoning' not in response.model_dump_json()


def test_tool_loop_and_model_loop_have_independent_bounds():
    tool = turn(call('search_knowledge', {'query':'Acme'}))
    response, model = run([tool, tool, tool], max_tool_calls=2)
    assert response.status == 'budget_exceeded'
    assert response.tool_calls == 2 and len(response.tool_trace) == 2
    response, _ = run([tool, tool], max_model_calls=2)
    assert response.status == 'budget_exceeded' and response.model_calls == 2


def test_oversized_batch_executes_nothing():
    response, _ = run([turn(call('read_document', {'document_id':'acme-sla'}),
                            call('read_document', {'document_id':'inc-104-history'}))], max_tool_calls=1)
    assert response.status == 'budget_exceeded' and response.tool_calls == 0


def test_gateway_and_evidence_are_isolated_per_employee():
    knowledge = KnowledgeService(FixtureRepository.load())
    mei = ToolGateway(knowledge, 'mei', 'en')
    alex = ToolGateway(knowledge, 'alex', 'en')
    assert mei.execute('read_document', {'document_id':'inc-104-runbook'})['ok']
    assert not alex.execute('read_document', {'document_id':'inc-104-runbook'})['ok']
    assert alex.evidence == {}
    hits = alex.execute('search_knowledge', {'query':'AURORA-PRIVATE-742'})['hits']
    assert all(hit['document_id'] != 'inc-104-runbook' for hit in hits)
    assert 'AURORA-PRIVATE-742' not in json.dumps(hits)


def test_http_auth_and_lifecycle():
    model = ScriptedModel([turn(call('read_document', {'document_id':'acme-sla'})), final()])
    with TestClient(create_app({'test':'mei'}, model_factory=lambda:model)) as client:
        assert client.post('/v1/agent/ask', json={'question':'Acme?'}).status_code == 401
        assert client.post('/v1/agent/ask', headers={'Authorization':'Bearer test'},
                           json={'question':'Acme?', 'employee_id':'alex'}).status_code == 422
        response = client.post('/v1/agent/ask', headers={'Authorization':'Bearer test'}, json={'question':'Acme?'})
        assert response.status_code == 200 and response.json()['status'] == 'answered'
    assert model.closed


def test_no_key_keeps_unit_one_usable():
    with TestClient(create_app({'test':'mei'}, model_factory=lambda:None)) as client:
        assert client.get('/health/live').status_code == 200
        response = client.post('/v1/agent/ask', headers={'Authorization':'Bearer test'}, json={'question':'Acme?'})
        assert response.status_code == 503


def test_timeout_cancels_model_and_releases_capacity():
    class SlowModel:
        def __init__(self): self.cancelled = 0
        async def turn(self, contents, language):
            try:
                await anyio.sleep(10)
            finally:
                self.cancelled += 1
        async def aclose(self): pass
    model = SlowModel()
    with TestClient(create_app({'test':'mei'}, model_factory=lambda:model, request_timeout=.02, max_concurrent=1)) as client:
        for _ in range(2):
            response = client.post('/v1/agent/ask', headers={'Authorization':'Bearer test'}, json={'question':'Acme?'})
            assert response.status_code == 504
    assert model.cancelled == 2


def test_gemini_adapter_disables_automatic_execution_and_preserves_content():
    model = object.__new__(GeminiModel)
    model.model = 'test-model'
    content = turn(call('read_document', {'document_id':'acme-sla'}))
    model.client = AsyncMock()
    model.client.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[types.Candidate(content=content)])
    result = asyncio.run(model.turn([], 'en'))
    kwargs = model.client.models.generate_content.call_args.kwargs
    assert kwargs['config'].automatic_function_calling.disable is True
    assert {d.name for d in kwargs['config'].tools[0].function_declarations} == {'count_workspace_cases','inspect_artifact','propose_memory','search_knowledge','read_document','write_file','list_sla_cases','get_sla_case','calculate_sla_impact','draft_incident_memo','export_incident_memo','start_sla_workflow','get_sla_workflow','resume_sla_workflow','list_deals','get_deal','calculate_deal_terms','start_deal_workflow','get_deal_workflow','resume_deal_workflow'}
    assert result.parts[0].thought_signature == b'preserve-me'


def test_network_failure_is_sanitized_and_releases_capacity():
    import httpx
    class BrokenModel:
        async def turn(self, contents, language):
            raise httpx.ConnectError('sensitive upstream details')
        async def aclose(self): pass
    with TestClient(create_app({'test':'mei'}, model_factory=BrokenModel, max_concurrent=1)) as client:
        for _ in range(2):
            response = client.post('/v1/agent/ask', headers={'Authorization':'Bearer test'}, json={'question':'Acme?'})
            assert response.status_code == 503
            assert 'sensitive' not in response.text


def test_parallel_requests_get_overload_without_waiting():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    started = Event()
    release = Event()
    class WaitingModel:
        async def turn(self, contents, language):
            started.set()
            while not release.is_set():
                await anyio.sleep(.005)
            return final('Which project?', [], 'needs_clarification')
        async def aclose(self): pass
    with TestClient(create_app({'test':'mei'}, model_factory=WaitingModel, max_concurrent=1)) as client:
        def request():
            return client.post('/v1/agent/ask', headers={'Authorization':'Bearer test'}, json={'question':'Acme?'})
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(request)
            try:
                assert started.wait(timeout=3)
                second = request()
                assert second.status_code == 429
                assert second.headers['Retry-After'] == '5'
            finally:
                release.set()
            assert first.result(timeout=3).status_code == 200


def test_provider_error_is_sanitized():
    from google import genai
    class BrokenModel:
        async def turn(self, contents, language):
            raise genai.errors.ClientError(429, {'error': {'message':'provider-private-details'}})
        async def aclose(self): pass
    with TestClient(create_app({'test':'mei'}, model_factory=BrokenModel)) as client:
        response = client.post('/v1/agent/ask', headers={'Authorization':'Bearer test'}, json={'question':'Acme?'})
        assert response.status_code == 503
        assert 'provider-private-details' not in response.text
