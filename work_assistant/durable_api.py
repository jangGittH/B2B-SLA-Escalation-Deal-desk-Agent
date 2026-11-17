"""Unit 4: request-driven durable runs. Background dispatch is a later unit."""
import logging
import time
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .agent import AgentRequest
from .db.store import ConflictError
from .models import Employee, Language
from .telemetry import current_run, source_reference

log = logging.getLogger('work_assistant.runs')


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(default='New conversation', min_length=1, max_length=120)
    language: Language = 'en'


class TurnRequest(AgentRequest):
    request_key: str = Field(min_length=1, max_length=100)


def evidence_available(database, service, actor, run_id):
    token = current_run.set(None)
    try:
        for reference in database.evidence_references(run_id):
            current = service.read(actor.id, reference['id'], reference['language'])
            if source_reference(current) != reference:
                return False
    except LookupError:
        return False
    finally:
        current_run.reset(token)
    return True


def install_routes(app, database, service, authorize, execute_agent, request_timeout):
    def safe_result(actor, result):
        if not result:
            return result
        token = current_run.set(None)
        try:
            for source in result.get('sources', []):
                current = service.read(actor.id, source['id'], source['language'])
                if current.model_dump(mode='json') != source:
                    return None
        except LookupError:
            return None
        finally:
            current_run.reset(token)
        return result

    def public_run(actor, row):
        available = evidence_available(database, service, actor, row['id'])
        result = safe_result(actor, row.get('result')) if available else None
        status = row['status']
        if status == 'running' and row['lease_until'] <= time.time():
            status = 'interrupted'
        return {key: row[key] for key in ('id', 'conversation_id', 'kind', 'request', 'created_at', 'updated_at')} | {
            'status': status, 'result': result,
            'error': 'Saved evidence changed or is no longer available. Start a fresh request.'
                     if not available or (row.get('result') and result is None) else row.get('error'),
            'events': row.get('events', []) if available else [],
            'files': [] if not available or (row.get('result') and result is None) else row.get('files', []),
        }

    def find_run(actor, run_id):
        try:
            return database.get_run(actor, run_id)
        except LookupError:
            raise HTTPException(404, 'Run not found') from None

    async def perform(actor, row):
        token = current_run.set((database, row))
        from .context.state import prior_task
        prior_token=prior_task.set(None)
        started = time.monotonic()
        try:
            history = []
            size = 0
            previous = database.conversation(actor, row['conversation_id'])['runs']
            for item in reversed(previous):
                result = public_run(actor, item)['result']
                if item['id'] == row['id'] or not result:
                    continue
                question, answer = item['request']['question'], result['answer']
                size += len(question) + len(answer)
                if size > 24000 or len(history) >= 6:
                    break
                history.append({'question': question, 'answer': answer})
            # Carry bounded pointers/outcomes from the latest still-authorized prior context.
            from .context.tables import contexts
            from sqlalchemy import select
            for item in reversed(previous):
                if item['id']==row['id'] or not public_run(actor,item)['result']:continue
                with database.engine.connect() as c:
                    saved=c.execute(select(contexts.c.payload).where(contexts.c.run_id==item['id'])).scalar_one_or_none()
                if saved:
                    prior_task.set({key:saved.get(key,[]) for key in ('entities','steps','evidence','artifacts','unresolved')})
                    break
            response = await app.state.agent.ask(actor.id, AgentRequest(**row['request']), list(reversed(history)))
            service._actor(actor.id)
            data = response.model_dump(mode='json')
            if safe_result(actor, data) is None or not evidence_available(database, service, actor, row['id']):
                raise LookupError('Source changed')
            status = ('waiting_for_input' if response.status == 'needs_clarification' else
                      'completed' if response.status in ('answered', 'not_found') else 'failed')
            database.finish(row, status, result=data)
        except BaseException as exc:
            # Cancellation/timeout also releases the DB lease. Never expose exception bodies.
            error = ('Source access or version changed; start a fresh request.' if isinstance(exc, LookupError)
                     else 'Run stopped before completion. Review saved files before submitting another turn.')
            database.finish(row, 'interrupted', error=error)
            if isinstance(exc, LookupError):
                raise HTTPException(409, error) from None
            raise
        finally:
            try:
                database.event(row['id'], 'attempt_finished', {'duration_ms': round((time.monotonic()-started)*1000)})
            finally:
                prior_task.reset(prior_token)
                current_run.reset(token)
        return public_run(actor, database.get_run(actor, row['id']))

    async def submit(actor, kind, payload, key, conversation_id=None):
        async def operation():
            try:
                row, created = database.submit(actor, kind, payload, key, conversation_id, request_timeout + 30)
            except LookupError:
                raise HTTPException(404, 'Conversation not found') from None
            except ConflictError as exc:
                raise HTTPException(409, str(exc)) from None
            if not created:
                return public_run(actor, database.get_run(actor, row['id']))
            return await perform(actor, row)
        return await execute_agent(operation)

    @app.post('/v1/conversations', status_code=201)
    def create_conversation(body: ConversationRequest, actor: Employee = Depends(authorize)):
        return database.create_conversation(actor, body.title, body.language)

    @app.get('/v1/conversations')
    def list_conversations(actor: Employee = Depends(authorize)):
        return {'conversations': database.list_conversations(actor)}

    @app.get('/v1/conversations/{conversation_id}')
    def get_conversation(conversation_id: str, actor: Employee = Depends(authorize)):
        try:
            row = database.conversation(actor, conversation_id)
        except LookupError:
            raise HTTPException(404, 'Conversation not found') from None
        row['runs'] = [public_run(actor, database.get_run(actor, run['id'], include_events=False)) for run in row['runs']]
        return row

    @app.post('/v1/conversations/{conversation_id}/messages')
    async def message(conversation_id: str, body: TurnRequest, actor: Employee = Depends(authorize)):
        return await submit(actor, 'chat', body.model_dump(exclude={'request_key'}), body.request_key, conversation_id)


    @app.get('/v1/runs')
    def list_runs(kind: Literal['chat'] = 'chat', actor: Employee = Depends(authorize)):
        return {'runs': [public_run(actor, row) for row in database.list_runs(actor, kind)]}

    @app.get('/v1/runs/{run_id}')
    def get_run(run_id: str, actor: Employee = Depends(authorize)):
        return public_run(actor, find_run(actor, run_id))
