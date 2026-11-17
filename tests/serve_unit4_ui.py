"""Local browser-test server with isolated PostgreSQL state and deterministic models.

TEST_DATABASE_URL=... uv run python -m tests.serve_unit4_ui is not used because
`tests` isn't a package: run `uv run python tests/serve_unit4_ui.py` with PYTHONPATH=.
"""
import json
import os
from tempfile import TemporaryDirectory
from uuid import uuid4

import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from google.genai import types

from work_assistant.api import create_app
from work_assistant.db.store import Database, migrate
from work_assistant.repository import FixtureRepository


class BrowserModel:
    async def turn(self, contents, language):
        if not any(p.function_response for p in contents[-1].parts):
            return types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
                name='read_document', args={'document_id': 'acme-sla'}, id='read'))])
        return types.Content(role='model', parts=[types.Part(text=json.dumps({
            'status': 'answered', 'answer': 'The initial response target is 60 minutes.', 'citations': ['acme-sla']}))])


    async def aclose(self): pass


if __name__ == '__main__':
    base = os.environ['TEST_DATABASE_URL']
    name = 'northstar_ui_' + uuid4().hex
    admin = create_engine(base, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    db = Database(url)
    try:
        migrate(url)
        workspace = FixtureRepository.load().workspace
        db.seed(workspace)
        with TemporaryDirectory(prefix='northstar-ui-files-') as files:
            from pathlib import Path
            from structured_model import CaseModel
            app = create_app({e.id: e.id for e in workspace.employees if e.active},
                model_factory=CaseModel if os.getenv('UI_STRUCTURED_MODEL')=='1' else BrowserModel,
                demo_mode=True, database_url=url, artifact_root=Path(files))
            uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('UI_TEST_PORT', '8012')))
    finally:
        db.close()
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()
