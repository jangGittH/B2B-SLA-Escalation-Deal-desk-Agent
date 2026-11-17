"""Unit 2: tool schemas and dispatch. Identity is bound by the backend."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from google.genai import types

from .knowledge import DocumentView, KnowledgeService
from .models import Language
from .files import ArtifactStore, WriteArguments, Artifact
from .policy import TOOL_RISK, allowed_tool
from .db.store import ConflictError
from .runtime.queue import CapacityError
from .sla.domain import CreateMemo


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class SearchArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=5)


class ReadArguments(ToolArguments):
    document_id: str = Field(min_length=1, max_length=100)


class CaseListArguments(ToolArguments):
    query: str = Field(default='', max_length=200)
    status: Literal['open', 'investigating'] | None = None
    account_id: str | None = Field(default=None, max_length=100)

class CaseCountArguments(ToolArguments):
    pass


class CaseArguments(ToolArguments):
    ticket_id: str = Field(min_length=1, max_length=100)


class DraftArguments(CaseArguments):
    expected_ticket_version: int = Field(ge=1)
    hypotheses: str = Field(default='', max_length=3000)
    next_steps: str = Field(default='', max_length=3000)
    customer_response_draft: str = Field(default='', max_length=4000)


class StartWorkflowArguments(CaseArguments):
    expected_ticket_version: int = Field(ge=1)


class GetWorkflowArguments(ToolArguments):
    workflow_id: str = Field(min_length=1, max_length=32)


class ResumeWorkflowArguments(GetWorkflowArguments):
    expected_version: int = Field(ge=1)


class ExportArguments(ToolArguments):
    memo_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1)
    format: Literal['md', 'docx', 'pdf', 'json'] = 'docx'


class DealArguments(ToolArguments):
    deal_id: str = Field(min_length=1,max_length=100)


class DealListArguments(ToolArguments):
    query: str = Field(default='',max_length=200)


class StartDealArguments(DealArguments):
    expected_deal_version: int = Field(ge=1)


class InspectArguments(ToolArguments):
    ref:str=Field(min_length=32,max_length=32)
    cursor:int=Field(default=0,ge=0)
    limit:int=Field(default=4,ge=1,le=8)
    contains:str=Field(default='',max_length=100)

class RememberArguments(ToolArguments):
    value:Literal['overview_first','evidence_first']


SCHEMAS = {"count_workspace_cases":CaseCountArguments,"inspect_artifact":InspectArguments,"propose_memory":RememberArguments,"search_knowledge": SearchArguments, "read_document": ReadArguments, "write_file": WriteArguments,
           "list_sla_cases": CaseListArguments, "get_sla_case": CaseArguments,
           "calculate_sla_impact": CaseArguments, "draft_incident_memo": DraftArguments,
           "export_incident_memo": ExportArguments, "start_sla_workflow": StartWorkflowArguments,
           "get_sla_workflow": GetWorkflowArguments, "resume_sla_workflow": ResumeWorkflowArguments,
           "list_deals": DealListArguments, "get_deal": DealArguments, "calculate_deal_terms": DealArguments,
           "start_deal_workflow": StartDealArguments, "get_deal_workflow": GetWorkflowArguments,
           "resume_deal_workflow": ResumeWorkflowArguments}
DESCRIPTIONS = {
    'count_workspace_cases':'Read exact current organization-wide counts of all stored SLA cases and deal requests, separately and combined. No arguments. Returns a citable source, not a capped list. Use for how many cases/deals questions; do not search policy documents or infer totals from list pages.',
    "inspect_artifact":"Inspect a private large tool result by ref, fragment cursor and optional literal text filter. Revalidates source access/version. Partial pages never substitute for exact money calculations.",
    "propose_memory":"Propose an export layout preference only when requested. Saved pending human confirmation in Preferences and memory; never activates itself or changes policies. overview_first or evidence_first only.",
    "list_deals": "List tenant-visible commercial requests to resolve the correct deal ID. Requires PostgreSQL.",
    "get_deal": "Read a typed deal, pricing/cost policy, source and sandbox contracts, exact discount/margin checks and missing inputs. Cite the returned source. Costs are human-attested demo policy, not inferred from narrative.",
    "calculate_deal_terms": "Calculate exact monthly fee and margin from stored deal/policy terms. Returns named missing facts or policy exceptions. A quote is not an executed contract update.",
    "start_deal_workflow": "When the owner requests review, submit a deal/version for independent Operations approval. Durably queues work bound to this conversation; a worker later prepares the memo and pauses for review. Acceptance is not a prepared or approved proposal. No model approval; open Deal desk to review or edit. Never invent missing cost or override policy.",
    "get_deal_workflow": "Read authorized saved deal review, memo, decision and verified sandbox contract receipt. Cite the source; never infer a real CRM update or signed amendment.",
    "resume_deal_workflow": "Resume an owned deal workflow after human facts/cost-policy edits, using its current version. Cannot approve, alter policy or bypass fresh review.",
    "start_sla_workflow": "Start the durable SLA sandbox review when the user asks to start/escalate/submit a case for approval. Read the case first for its version. Durably queues work bound to this conversation; a worker later gathers shared evidence, calculates from the sandbox ledger, prepares a memo and pauses for input or independent human approval. Acceptance is not completion; use get_sla_workflow for current status. No credit executes until a designated human reviewer uses the review UI. Cite the returned workflow source; tell the user to open SLA cases for review.",
    "resume_sla_workflow": "Continue an owned workflow after the human has updated its missing case facts, or recover interrupted work. Read the workflow first for expected_version. Does not approve a proposal or change incident facts. Current valid approvals still control any execution.",
    "get_sla_workflow": "Read an authorized workflow's saved clarification, approval, progress and verified sandbox receipt. Cite its returned source. Never infer execution from a proposal or approval alone.",
    "list_sla_cases": "List up to 100 tenant-authorized SLA tickets with optional customer, status and text filters. Use to resolve a case ID. Requires PostgreSQL mode.",
    "get_sla_case": "Read current linked ticket, account, signed demo contract and policy, exact calculation and source provenance. Cite returned document ID. Incident facts are human attestations. Requires PostgreSQL mode.",
    "calculate_sla_impact": "Calculate from stored versioned inputs only: exact Decimal credit, exclusions, monthly cap and prior credit, or explicit missing inputs. A proposal only, never an issued credit. Returns a citable source. Do not invent missing inputs or calculate money yourself.",
    "draft_incident_memo": "Save a private, editable incident memo from current records and calculation. Read case first for expected_ticket_version. Confirmed fields are rendered by the server; free text is labeled unverified hypotheses, next steps and unsent customer draft. Returns memo ID and citable source. Never claims a confirmed root cause.",
    "export_incident_memo": "Export a saved current private memo as md/docx/pdf/json using its ID and expected version. Fails if inputs changed. Use this for incident memo downloads to preserve provenance.",
    "write_file": "Create a downloadable file when the user requests one. Supported extensions: txt, md, json, csv, tsv, html, docx, xlsx, pptx, pdf. Supply filename and full content. JSON must be valid JSON; xlsx content must be CSV (cells are literal text); docx supports plain text and # headings; pdf uses plain text; pptx uses first line as slide title and remaining lines as body, with --- on its own line between slides. Never supply a filesystem path. A new file is created on every call; existing files are not edited.",
    "search_knowledge": "Search documents available to the current employee. Returns previews and IDs. "
                        "Use short Chinese or English keywords; read relevant documents before answering.",
    "read_document": "Read one available document by ID. Required before citing it in your answer. "
                     "Unavailable IDs return NOT_FOUND; never infer private document contents.",
}


def declarations() -> list[types.FunctionDeclaration]:
    return [types.FunctionDeclaration(name=name, description=DESCRIPTIONS[name],
                                      parameters_json_schema=schema.model_json_schema())
            for name, schema in SCHEMAS.items()]


class ToolGateway:
    """One gateway per request: its evidence and actor never cross conversations."""

    def __init__(self, knowledge: KnowledgeService, employee_id: str, language: Language, files: ArtifactStore | None = None):
        self.knowledge = knowledge
        self.employee_id = employee_id
        self.language = language
        self.files = files
        self.artifacts = []
        self.evidence: dict[str, DocumentView] = {}
        self.workflow_keys = {}

    def execute(self, name: str, arguments: dict) -> dict:
        result=self._execute(name,arguments)
        if result.get('ok') and self.knowledge.cases and name!='inspect_artifact':
            from .context.offload import OffloadStore
            try:return OffloadStore(self.knowledge,self.employee_id).compact(result,name,arguments)
            except (ValueError,LookupError):return {'ok':False,'error':{'code':'RESULT_UNAVAILABLE','message':'Result changed or exceeds the context budget; narrow the request.'}}
        return result

    def _execute(self, name: str, arguments: dict) -> dict:
        if name in TOOL_RISK and not allowed_tool(name):
            return {"ok": False, "error": {"code": "APPROVAL_REQUIRED", "message": "Financial/external actions cannot be called by the model. Sandbox financial actions require a persisted independent human decision in the workflow UI."}}
        if name not in SCHEMAS:
            return {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": "Tool is not available"}}
        try:
            args = SCHEMAS[name].model_validate(arguments)
        except ValidationError:
            return {"ok": False, "error": {"code": "INVALID_ARGUMENTS", "message":
                    "Use the declared schema exactly. Identity and language are supplied by the server."}}
        try:
            self.knowledge._actor(self.employee_id)
            if name=='count_workspace_cases':
                if not self.knowledge.cases:
                    return {'ok':False,'error':{'code':'CASES_UNAVAILABLE','message':'PostgreSQL mode is required'}}
                document=self.knowledge.read(self.employee_id,'case-summary',self.language)
                self.evidence[document.id]=document
                return {'ok':True,'risk_tier':0,'document':document.model_dump(mode='json')}
            if name in ('inspect_artifact','propose_memory'):
                if not self.knowledge.cases:return {'ok':False,'error':{'code':'UNAVAILABLE','message':'PostgreSQL mode required'}}
                if name=='inspect_artifact':
                    from .context.offload import OffloadStore
                    result=OffloadStore(self.knowledge,self.employee_id).read(
                        args.ref, args.cursor, args.limit, args.contains)
                    if result.get('document_id'):
                        document=self.knowledge.read(self.employee_id,result['document_id'],self.language)
                        self.evidence[document.id]=document
                    return result
                from .memory.service import MemoryService,Proposal
                row=MemoryService(self.knowledge.repository).propose(self.employee_id,Proposal(value=args.value))
                return {'ok':True,'memory_id':row['id'],'status':row['status'],'note':'Human confirmation is required in Preferences and memory.'}
            if name in {'start_sla_workflow', 'get_sla_workflow', 'resume_sla_workflow', 'start_deal_workflow', 'get_deal_workflow', 'resume_deal_workflow'}:
                if self.knowledge.cases is None:
                    return {"ok":False,"error":{"code":"CASES_UNAVAILABLE","message":"PostgreSQL mode is required"}}
                from .workflows.service import WorkflowService
                from .workflows.domain import StartWorkflow
                from .telemetry import current_run
                from uuid import uuid4
                deal_mode = 'deal' in name
                if deal_mode:
                    from .deals.workflow import DealWorkflowService as WorkflowService
                    from .deals.domain import StartDealWorkflow as StartWorkflow
                subject = 'deal' if deal_mode else 'ticket'
                service = WorkflowService(self.knowledge.repository)
                if name.startswith('start_'):
                    context = current_run.get()
                    subject_id = getattr(args, subject + '_id')
                    key = ('chat:' + context[1]['id'] + ':' + subject_id) if context else self.workflow_keys.setdefault(subject + ':' + subject_id, uuid4().hex)
                    # The request key is bounded even for long ticket IDs; identity is never model-owned.
                    import hashlib
                    key = hashlib.sha256(key.encode()).hexdigest()
                    row = service.start(self.employee_id, StartWorkflow(**{subject + '_id': subject_id, 'expected_' + subject + '_version': getattr(args, 'expected_' + subject + '_version')},
                        request_key=key, language=self.language,
                        conversation_id=context[1]['conversation_id'] if context else None))
                    workflow_id = row['id']
                else:
                    workflow_id = args.workflow_id
                    if name.startswith('resume_'):
                        service.resume(self.employee_id, workflow_id, args.expected_version)
                source_prefix = ('deal-workflow:' if deal_mode else 'workflow:') if name.startswith('get_') else 'workflow-request:'
                document = self.knowledge.read(self.employee_id, source_prefix + workflow_id, self.language)
                self.evidence[document.id] = document
                return {"ok":True,"risk_tier":TOOL_RISK[name],"workflow_id":workflow_id,"document":document.model_dump(mode='json')}
            if name in {'list_deals','get_deal','calculate_deal_terms'}:
                if self.knowledge.cases is None:
                    return {"ok":False,"error":{"code":"CASES_UNAVAILABLE","message":"PostgreSQL mode is required"}}
                from .deals.service import DealService
                if name == 'list_deals':
                    if not args.query:
                        document=self.knowledge.read(self.employee_id,'deal-list',self.language)
                        self.evidence[document.id]=document
                        import json
                        return {'ok':True,'risk_tier':0,'deals':json.loads(document.content)['records'],'document':document.model_dump(mode='json')}
                    return {"ok":True,"risk_tier":0,"deals":DealService(self.knowledge.repository).list(self.employee_id,args.query)}
                document=self.knowledge.read(self.employee_id,'deal:'+args.deal_id,self.language)
                self.evidence[document.id]=document
                return {"ok":True,"risk_tier":0,"document":document.model_dump(mode='json')}
            if name in {'list_sla_cases', 'get_sla_case', 'calculate_sla_impact', 'draft_incident_memo', 'export_incident_memo'}:
                service = self.knowledge.cases
                if service is None:
                    return {"ok": False, "error": {"code": "CASES_UNAVAILABLE", "message": "Start with PostgreSQL to enable SLA cases."}}
                if name == 'list_sla_cases':
                    if not args.query and not args.status and not args.account_id:
                        document=self.knowledge.read(self.employee_id,'case-list',self.language)
                        self.evidence[document.id]=document
                        import json
                        return {'ok':True,'risk_tier':0,'tickets':json.loads(document.content)['records'],'document':document.model_dump(mode='json')}
                    return {"ok": True, "risk_tier": 0, "tickets": service.list_tickets(self.employee_id, **args.model_dump())}
                if name == 'draft_incident_memo':
                    row = service.save_memo(self.employee_id, args.ticket_id,
                        CreateMemo(**args.model_dump(exclude={'ticket_id'}), language=self.language))
                    source_id = 'memo:' + row['id']
                    extra = {'memo_id': row['id'], 'version': row['version'], 'ticket_id': args.ticket_id}
                elif name == 'export_incident_memo':
                    if self.files is None:
                        return {"ok": False, "error": {"code": "FILES_UNAVAILABLE", "message": "File storage is unavailable"}}
                    from .sla.api import export_memo
                    data = export_memo(service, self.files, self.employee_id, **args.model_dump())
                    self.artifacts.append(Artifact.model_validate(data))
                    source_id, extra = 'memo:' + args.memo_id, {'file': data}
                else:
                    source_id, extra = 'case:' + args.ticket_id, {}
                document = self.knowledge.read(self.employee_id, source_id, self.language)
                self.evidence[document.id] = document
                return {"ok": True, "risk_tier": TOOL_RISK[name], "document": document.model_dump(mode='json'), **extra}
            if name == "write_file":
                if any(key.startswith(('case:', 'memo:', 'workflow:', 'deal:', 'deal-workflow:', 'workflow-request:')) for key in self.evidence):
                    return {"ok": False, "error": {"code": "USE_MEMO_EXPORT", "message": "For SLA files, use incident memo export. Deal review memos are available in the workflow UI; arbitrary deal exports are not supported."}}
                if self.files is None:
                    return {"ok": False, "error": {"code": "FILES_UNAVAILABLE", "message": "File storage is unavailable"}}
                try:
                    artifact = self.files.create(self.employee_id, args)
                except (ValueError, OSError):
                    return {"ok": False, "error": {"code": "WRITE_FAILED", "message": "Could not create file. Check the format and content or retry."}}
                self.artifacts.append(artifact)
                return {"ok": True, "risk_tier": 1, "file": artifact.model_dump()}
            if name == "search_knowledge":
                result = self.knowledge.search(self.employee_id, args.query, self.language, args.limit)
                return {"ok": True, "hits": [
                    {"document_id": hit.document.id, "title": hit.document.title,
                     "preview": (hit.excerpt or hit.document.content)[:180], "score": hit.score, "chunk_id":hit.chunk_id, "version":hit.document.version}
                    for hit in result.hits]}
            document = self.knowledge.read(self.employee_id, args.document_id, self.language)
            self.evidence[document.id] = document
            return {"ok": True, "document": document.model_dump(mode="json")}
        except CapacityError:
            return {"ok":False,"error":{"code":"CAPACITY_LIMIT","message":"Workflow capacity reached; retry after existing reviews finish. No new work was accepted."}}
        except ConflictError:
            return {"ok": False, "error": {"code": "CONFLICT", "message": "Record changed; read current inputs and retry with the current version."}}
        except LookupError:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "Record not found"}}
        # PermissionError deliberately propagates: an inactive actor ends the run.
