"""Permission-aware bilingual lexical search; no model inference in Unit 1."""

import re
import unicodedata
from datetime import datetime

from .models import Document, Employee, Language, Record
from .repository import WorkspaceRepository


def can_read(employee: Employee, document: Document) -> bool:
    """Tenant isolation is mandatory even when a document is organization-visible."""
    return (
        employee.active
        and employee.organization_id == document.organization_id
        and (
            document.visibility == "organization"
            or employee.id in document.reader_employee_ids
            or bool(set(employee.team_ids) & set(document.reader_team_ids))
        )
    )


def terms(text: str) -> set[str]:
    """English words + overlapping Han bigrams; a transparent baseline, not translation."""
    text = unicodedata.normalize("NFKC", text).casefold()
    words = set(re.findall(r"[a-z0-9]+", text))
    for sequence in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", text):
        words.update(sequence[i:i + 2] for i in range(len(sequence) - 1))
        if len(sequence) == 1:
            words.add(sequence)
    return words


class DocumentView(Record):
    id: str
    project_id: str | None
    title: str
    content: str
    language: Language
    version: int
    updated_at: datetime
    source_url: str


class SearchHit(Record):
    document: DocumentView
    score: float
    chunk_id: str | None = None
    section: str | None = None
    excerpt: str | None = None


class SearchResponse(Record):
    query: str
    language: Language
    method: str = "bilingual_lexical_baseline"
    hits: tuple[SearchHit, ...]


def view(document: Document, language: Language) -> DocumentView:
    return DocumentView(
        id=document.id, project_id=document.project_id,
        title=document.title.localized(language), content=document.content.localized(language),
        language=language, version=document.version, updated_at=document.updated_at,
        source_url=f"/v1/documents/{document.id}?language={language}",
    )


class KnowledgeService:
    def __init__(self, repository: WorkspaceRepository):
        self.repository = repository
        from .sla.service import SlaService
        self.cases = SlaService(repository) if hasattr(repository, "engine") else None

    def _actor(self, employee_id: str) -> Employee:
        employee = self.repository.employee(employee_id)
        if employee is None or not employee.active:
            raise PermissionError("Employee unavailable")
        return employee

    def read(self, employee_id: str, document_id: str, language: Language) -> DocumentView:
        actor = self._actor(employee_id)
        if self.cases and document_id in ('deal-list','case-list'):
            from sqlalchemy import select,func
            from .deals.tables import requests
            from .sla.tables import tickets
            import json
            table=requests if document_id=='deal-list' else tickets
            fields=['id','title','version','updated_at']+(['owner_id','contract_id','requested_discount','renewal_end'] if document_id=='deal-list' else ['status','severity','assignee_id'])
            limit=50 if document_id=='deal-list' else 100
            with self.repository.engine.connect() as conn:
                rows=[dict(r) for r in conn.execute(select(*(table.c[f] for f in fields),func.count().over().label('matching_total'))
                    .where(table.c.organization_id==actor.organization_id).order_by(table.c.updated_at.desc(),table.c.id).limit(limit)).mappings()]
            total=rows[0]['matching_total'] if rows else 0
            for row in rows:row.pop('matching_total')
            payload={'scope':'Current organization','records':rows,'total':total,'returned':len(rows),'truncated':total>len(rows),
                     'note':'Listing only. Read individual records for full details and policy calculations.'}
            result=DocumentView(id=document_id,project_id=None,title='Deal requests' if document_id=='deal-list' else 'SLA cases',
                content=json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str,indent=2),language=language,version=1,
                updated_at=max([r['updated_at'] for r in rows] or [datetime.fromisoformat('1970-01-01T00:00:00+00:00')]),
                source_url=f'/v1/documents/{document_id}?language={language}')
            from .telemetry import event,source_reference
            event('evidence_read',source_reference(result))
            return result
        if self.cases and document_id == 'case-summary':
            from sqlalchemy import select, func
            from .sla.tables import tickets
            from .deals.tables import requests
            import json
            # One database snapshot; counts are not derived from capped list pages.
            with self.repository.engine.connect() as conn:
                sla, deals, sla_updated, deal_updated = conn.execute(select(
                    select(func.count()).select_from(tickets).where(tickets.c.organization_id==actor.organization_id).scalar_subquery(),
                    select(func.count()).select_from(requests).where(requests.c.organization_id==actor.organization_id).scalar_subquery(),
                    select(func.max(tickets.c.updated_at)).where(tickets.c.organization_id==actor.organization_id).scalar_subquery(),
                    select(func.max(requests.c.updated_at)).where(requests.c.organization_id==actor.organization_id).scalar_subquery(),
                )).one()
            data={'scope':'Current organization; records visible in SLA cases and Deal desk',
                  'sla_cases':sla,'deal_requests':deals,'combined_records':sla+deals,
                  'note':'Counts include all stored records, not only open cases. Deals are separate from SLA cases.'}
            result=DocumentView(id=document_id,project_id=None,title='Current case counts',
                content=json.dumps(data,sort_keys=True,indent=2),language=language,version=1,
                updated_at=max([v for v in (sla_updated,deal_updated) if v] or [datetime.fromisoformat('1970-01-01T00:00:00+00:00')]),
                source_url=f'/v1/documents/{document_id}?language={language}')
            from .telemetry import event,source_reference
            event('evidence_read',source_reference(result))
            return result
        if self.cases and document_id.startswith('workflow-request:'):
            from .workflows.tables import workflows
            from sqlalchemy import select
            import json
            with self.repository.engine.connect() as conn:
                row=conn.execute(select(workflows).where(workflows.c.id==document_id.split(':',1)[1],
                    workflows.c.organization_id==actor.organization_id,workflows.c.employee_id==actor.id)).mappings().first()
            if not row: raise LookupError('Workflow not found')
            data={'workflow_id':row['id'],'ticket_id':row['ticket_id'],'deal_id':row['deal_id'],
                'conversation_id':row['conversation_id'],'accepted_at':row['created_at'].isoformat(),
                'note':'Request accepted durably. Read the workflow for current state; this acceptance does not imply approval or execution.'}
            result=DocumentView(id=document_id,project_id=None,title='Workflow request accepted',content=json.dumps(data,sort_keys=True),
                language=language,version=1,updated_at=row['created_at'],source_url=f'/v1/documents/{document_id}?language={language}')
            from .telemetry import event,source_reference
            event('evidence_read',source_reference(result))
            return result
        if self.cases and document_id.startswith(('deal:', 'deal-workflow:')):
            import json
            from .deals.service import DealService
            from .deals.workflow import DealWorkflowService
            if document_id.startswith('deal-workflow:'):
                row = DealWorkflowService(self.repository).get(employee_id,document_id.split(':',1)[1])
                row.pop('events',None)
                row.pop('delivery',None)
                version, updated = row['version'], row['updated_at']
                title = 'Deal workflow ' + row['deal_id']
            else:
                row = DealService(self.repository).detail(employee_id,document_id.split(':',1)[1])
                version, updated = row['deal']['version'], row['deal']['updated_at']
                title = 'Deal request ' + row['deal']['id']
            result = DocumentView(id=document_id,project_id=None,title=title,
                content=json.dumps(row,ensure_ascii=False,sort_keys=True,indent=2),language=language,version=version,updated_at=updated,
                source_url=f'/v1/documents/{document_id}?language={language}')
            from .telemetry import event, source_reference
            event('evidence_read',source_reference(result))
            return result
        if self.cases and document_id.startswith('workflow:'):
            from .workflows.service import WorkflowService
            import json
            row = WorkflowService(self.repository).get(employee_id, document_id[9:])
            # Events can grow during independent checkpoint bookkeeping; cite business state only.
            row.pop('events', None)
            row.pop('delivery',None)
            result = DocumentView(id=document_id, project_id=None, title='SLA workflow ' + row['ticket_id'],
                content=json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2), language=language,
                version=row['version'], updated_at=row['updated_at'],
                source_url=f'/v1/documents/{document_id}?language={language}')
            from .telemetry import event, source_reference
            event('evidence_read', source_reference(result))
            return result
        if self.cases and document_id.startswith(('case:', 'memo:')):
            result = self.cases.source(employee_id, document_id, language)
            from .telemetry import event, source_reference
            event('evidence_read', source_reference(result))
            return result
        document = self.repository.document(document_id)
        if document is None or not can_read(actor, document):
            # Do not reveal whether a forbidden document exists.
            raise LookupError("Document not found")
        result = view(document, language)
        from .telemetry import event, source_reference
        event('evidence_read', source_reference(result))
        return result

    def search(self, employee_id: str, query: str, language: Language, limit: int = 5, mode: str | None = None) -> SearchResponse:
        actor = self._actor(employee_id)
        import os
        mode = mode or (os.getenv('WORK_RETRIEVAL_MODE','bm25') if self.cases else 'lexical')
        if mode not in ('lexical','bm25','hybrid'):raise ValueError('Unsupported retrieval mode')
        if mode != 'lexical':
            from .retrieval.search import rank
            return rank(self,actor,query,language,limit,mode)
        query_terms = terms(query)
        hits = []
        for document in self.repository.documents():
            # Authorize BEFORE ranking, limiting, or returning any text/metadata.
            if not can_read(actor, document):
                continue
            title_terms = terms(document.title.en + " " + document.title.zh)
            content_terms = terms(document.content.en + " " + document.content.zh)
            score = 3 * len(query_terms & title_terms) + len(query_terms & content_terms)
            if score:
                hits.append(SearchHit(document=view(document, language), score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.document.id))
        return SearchResponse(query=query, language=language, hits=tuple(hits[:limit]))
