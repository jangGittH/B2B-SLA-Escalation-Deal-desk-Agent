import io
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from work_assistant.api import create_app
from work_assistant.files import ArtifactStore, WriteArguments, render_file
from test_agent import ScriptedModel, call, turn, final


@pytest.mark.parametrize('filename', ['../x.txt', '/tmp/x.txt', 'a\\x.txt', '.env', 'x.exe', 'x.txt\n', 'a:b.txt'])
def test_invalid_filenames(filename):
    with pytest.raises(ValidationError):
        WriteArguments(filename=filename, content='hello')


def test_formats_can_be_opened():
    from docx import Document
    from openpyxl import load_workbook
    from pptx import Presentation
    assert render_file('note.txt', '你好').decode() == '你好'
    assert json.loads(render_file('note.json', '{"你好": 1}')) == {'你好': 1}
    with pytest.raises(ValueError):
        render_file('note.json', 'invalid')
    document = Document(io.BytesIO(render_file('note.docx', '# 标题\n你好')))
    assert [p.text for p in document.paragraphs] == ['标题', '你好']
    sheet = load_workbook(io.BytesIO(render_file('data.xlsx', 'Name,Value\n你好,=1+1\nThird,3'))).active
    assert list(sheet.values) == [('Name', 'Value'), ('你好', '=1+1'), ('Third', '3')]
    assert sheet['B2'].data_type == 's'
    deck = Presentation(io.BytesIO(render_file('slides.pptx', '# 标题\n你好\n---\nNext\nBody')))
    assert len(deck.slides) == 2 and deck.slides[0].shapes.title.text == '标题'
    pdf = render_file('note.pdf', '你好 <hello> & welcome')
    assert pdf.startswith(b'%PDF-') and b'%%EOF' in pdf


def test_write_download_auth_and_persistence(tmp_path):
    model = ScriptedModel([turn(call('write_file', {'filename': '你好.md', 'content': '# Hello\n你好'})),
                           final('Created your file.', [])])
    headers = {'Authorization': 'Bearer mei-token'}
    with TestClient(create_app({'mei-token': 'mei', 'alex-token': 'alex'},
                               model_factory=lambda: model, artifact_root=tmp_path)) as client:
        response = client.post('/v1/agent/ask', headers=headers,
                               json={'question': 'Write a Markdown greeting file.'})
        body = response.json()
        assert body['status'] == 'answered'
        file = body['files'][0]
        assert body['tool_trace'][0]['tool'] == 'write_file'
        assert client.get(file['download_url']).status_code == 401
        assert client.get(file['download_url'], headers={'Authorization': 'Bearer alex-token'}).status_code == 404
        download = client.get(file['download_url'], headers=headers)
        assert download.status_code == 200 and download.text == '# Hello\n你好'
        assert download.headers['content-disposition'].startswith('attachment;')
        assert download.headers['cache-control'] == 'no-store'
        assert client.get('/v1/files/unknown', headers=headers).status_code == 404
        artifact, path = ArtifactStore(tmp_path).read('mei', file['id'])
        assert path.read_text() == '# Hello\n你好'
        assert artifact.filename == '你好.md'


def test_write_error_is_recoverable(tmp_path):
    model = ScriptedModel([
        turn(call('write_file', {'filename': 'bad.json', 'content': 'not json'})),
        turn(call('write_file', {'filename': 'good.json', 'content': '{"ok": true}'})),
        final('Created.', [])])
    with TestClient(create_app({'token': 'mei'}, model_factory=lambda: model, artifact_root=tmp_path)) as client:
        result = client.post('/v1/agent/ask', headers={'Authorization': 'Bearer token'},
                             json={'question': 'Create JSON.'}).json()
    assert [event['outcome'] for event in result['tool_trace']] == ['WRITE_FAILED', 'ok']
    assert len(result['files']) == 1


def test_files_returned_even_when_model_budget_ends(tmp_path):
    import asyncio
    from work_assistant.agent import KnowledgeAgent, AgentRequest
    from work_assistant.knowledge import KnowledgeService
    from work_assistant.repository import FixtureRepository
    model = ScriptedModel([turn(call('write_file', {'filename': 'note.txt', 'content': 'saved'}))])
    agent = KnowledgeAgent(KnowledgeService(FixtureRepository.load()), model,
                           max_model_calls=1, files=ArtifactStore(tmp_path))
    response = asyncio.run(agent.ask('mei', AgentRequest(question='Create note.txt')))
    assert response.status == 'budget_exceeded' and len(response.files) == 1


def test_repeated_names_do_not_overwrite(tmp_path):
    store = ArtifactStore(tmp_path)
    args = WriteArguments(filename='note.txt', content='hello')
    first, second = store.create('mei', args), store.create('mei', args)
    assert first.id != second.id
    assert store.read('mei', first.id)[1].read_text() == 'hello'
