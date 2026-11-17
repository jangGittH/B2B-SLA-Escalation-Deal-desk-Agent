from typing import Literal
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..db.store import ConflictError
from ..files import WriteArguments
from ..models import Employee
from .domain import CreateTicket, EditTicket, CreateMemo, EditMemo
from .service import SlaService
from . import tables as t


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)
    format: Literal['md', 'docx', 'pdf', 'json'] = 'docx'


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)
    expected_ticket_version: int = Field(ge=1)
    revision: int = Field(ge=1)


def install_routes(app, service: SlaService, files, authorize):
    def execute(operation):
        try:
            return operation()
        except LookupError:
            raise HTTPException(404, 'Record not found') from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get('/v1/cases/options')
    def options(actor: Employee = Depends(authorize)):
        return execute(lambda: service.options(actor.id))

    @app.get('/v1/cases')
    def cases(status: Literal['open', 'investigating'] | None = None,
              account_id: str | None = Query(default=None, max_length=100),
              query: str = Query(default='', max_length=200), actor: Employee = Depends(authorize)):
        return execute(lambda: {'tickets': service.list_tickets(actor.id, status, account_id, query)})

    @app.post('/v1/cases', status_code=201)
    def create(body: CreateTicket, actor: Employee = Depends(authorize)):
        return execute(lambda: service.create_ticket(actor.id, body))

    @app.get('/v1/cases/{ticket_id}')
    def detail(ticket_id: str, actor: Employee = Depends(authorize)):
        def result():
            data = service.detail(actor.id, ticket_id)
            return data | {'can_edit': data['ticket']['assignee_id'] == actor.id,
                           'memos': service.list_memos(actor.id, ticket_id)}
        return execute(result)

    @app.patch('/v1/cases/{ticket_id}')
    def edit(ticket_id: str, body: EditTicket, actor: Employee = Depends(authorize)):
        return execute(lambda: service.edit_ticket(actor.id, ticket_id, body))

    @app.get('/v1/cases/{ticket_id}/events')
    def events(ticket_id: str, actor: Employee = Depends(authorize)):
        def result():
            service.detail(actor.id, ticket_id)
            with service.db.engine.connect() as conn:
                rows = conn.execute(select(t.audit).where(t.audit.c.organization_id == actor.organization_id,
                    t.audit.c.ticket_id == ticket_id,
                    t.audit.c.kind.in_(['case_created', 'case_edited']) | (t.audit.c.employee_id == actor.id)
                    ).order_by(t.audit.c.id.desc()).limit(100)).mappings()
                return {'events': [dict(row) for row in rows]}
        return execute(result)

    @app.post('/v1/cases/{ticket_id}/memos', status_code=201)
    def draft(ticket_id: str, body: CreateMemo, actor: Employee = Depends(authorize)):
        return execute(lambda: service.save_memo(actor.id, ticket_id, body))

    @app.get('/v1/memos/{memo_id}')
    def memo(memo_id: str, actor: Employee = Depends(authorize)):
        return execute(lambda: service.memo(actor.id, memo_id))

    @app.patch('/v1/memos/{memo_id}')
    def edit_memo(memo_id: str, body: EditMemo, actor: Employee = Depends(authorize)):
        def result():
            row = service.memo(actor.id, memo_id)
            return service.save_memo(actor.id, row['ticket_id'], body, memo_id)
        return execute(result)

    @app.post('/v1/memos/{memo_id}/restore')
    def restore(memo_id: str, body: RestoreRequest, actor: Employee = Depends(authorize)):
        def result():
            row = service.memo(actor.id, memo_id)
            with service.db.engine.connect() as conn:
                previous = conn.execute(select(t.revisions.c.payload).where(
                    t.revisions.c.organization_id == actor.organization_id, t.revisions.c.memo_id == memo_id,
                    t.revisions.c.version == body.revision)).scalar_one_or_none()
            if previous is None:
                raise LookupError('Revision not found')
            edit = EditMemo(**previous['notes'], expected_version=body.expected_version,
                            expected_ticket_version=body.expected_ticket_version)
            return service.save_memo(actor.id, row['ticket_id'], edit, memo_id)
        return execute(result)

    @app.post('/v1/memos/{memo_id}/exports', status_code=201)
    def export(memo_id: str, body: ExportRequest, actor: Employee = Depends(authorize)):
        return execute(lambda: export_memo(service, files, actor.id, memo_id, body.expected_version, body.format))


def export_memo(service, files, employee_id, memo_id, expected_version, format):
    import json
    row = service.memo(employee_id, memo_id)
    if row['stale'] or row['version'] != expected_version:
        raise ConflictError('Memo or its evidence changed; refresh and save before exporting')
    content = json.dumps(row, ensure_ascii=False, indent=2, default=str) if format == 'json' else row['content']
    if format!='json':
        from ..memory.service import MemoryService
        if MemoryService(service.db).layout(employee_id)=='evidence_first':
            import re
            sections=re.split(r'(?m)(?=^## )',content)
            sources=[x for x in sections if x.startswith(('## Input provenance','## Evidence','## Source','## 输入来源','## 证据','## 来源'))]
            content=''.join([sections[0]]+sources+[x for x in sections[1:] if x not in sources])
    artifact = files.create(employee_id, WriteArguments(filename=f"{row['ticket_id']}-memo-v{row['version']}.{format}", content=content), requires_database=True)
    service.link_export(employee_id, memo_id, row['version'], artifact)
    return artifact.model_dump()
