"""Workspace and agent HTTP boundary. Start via `python -m work_assistant demo`."""

import secrets
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import anyio
import httpx
from google import genai
from fastapi import Depends, FastAPI, HTTPException, Security, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .knowledge import DocumentView, KnowledgeService, SearchResponse, can_read
from .models import Employee, Language
from .repository import FixtureRepository
from .files import ArtifactStore
from .runtime.queue import CapacityError
from .agent import AgentRequest, AgentResponse, KnowledgeAgent, configured_model


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)
    language: Language = "en"
    limit: int = Field(default=5, ge=1, le=20)


class IdentityResponse(BaseModel):
    employee_id: str
    organization_id: str
    name: str
    team_ids: tuple[str, ...]


class DemoSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str


def create_app(tokens: dict[str, str], repository: FixtureRepository | None = None, *,
               model_factory=configured_model, request_timeout: float = 90,
               max_concurrent: int = 2, demo_mode: bool = False, artifact_root: Path | None = None, database_url: str | None = None, embedded_worker: bool = True) -> FastAPI:
    """tokens maps opaque credentials to server-owned employee IDs (demo auth only)."""
    database = None
    if database_url:
        from .db.store import Database
        database = Database(database_url)
        repository = database
    repository = repository if repository is not None else FixtureRepository.load()
    tokens = dict(tokens)
    if not tokens:
        raise ValueError("At least one demo credential is required")
    for token, employee_id in tokens.items():
        employee = repository.employee(employee_id)
        if not token or employee is None or not employee.active:
            raise ValueError("Credentials must map to active fixture employees")
    service = KnowledgeService(repository)
    files = ArtifactStore(artifact_root if artifact_root is not None else Path(os.getenv("WORK_FILES_DIR", ".work_assistant/files")))
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(app):
        model = model_factory()
        from .agents.runtime import CollaborationAgent
        agent_class = CollaborationAgent if model and hasattr(model, 'structured') else KnowledgeAgent
        app.state.agent = agent_class(service, model, files=files) if model else None
        app.state.capacity = anyio.CapacityLimiter(max_concurrent)
        worker = None
        try:
            if database and embedded_worker:
                from .runtime.worker import Worker
                worker = Worker(database).start()
            yield
        finally:
            if worker:
                await anyio.to_thread.run_sync(worker.close)
            if database:
                database.close()
            if model:
                with anyio.CancelScope(shield=True):
                    await model.aclose()

    app = FastAPI(
        title="B2B SLA Escalation/Deal desk Agent",
        description="Local fictional workspace. Authorize with a token printed by the demo launcher. "
                    "Search returns source documents, not generated answers.",
        version="0.11.0", lifespan=lifespan,
    )

    def authorize(credentials: HTTPAuthorizationCredentials | None = Security(bearer)) -> Employee:
        provided = credentials.credentials if credentials else ""
        employee_id = next((employee_id for token, employee_id in tokens.items()
                            if secrets.compare_digest(provided.encode(), token.encode())), None)
        employee = repository.employee(employee_id) if employee_id else None
        if employee is None or not employee.active:
            raise HTTPException(401, "Invalid or inactive employee credential",
                                headers={"WWW-Authenticate": "Bearer"})
        return employee

    @app.get("/v1/ui/config")
    def ui_config():
        # Only the explicit loopback demo profile offers identity simulation.
        return {"demo_mode": demo_mode, "employees": [
            {"id": employee.id, "name": employee.name.model_dump(),
             "organization_id": employee.organization_id}
            for employee in (repository.employee(employee_id) for employee_id in dict.fromkeys(tokens.values()))
            if employee and employee.active
        ] if demo_mode else []}

    @app.post("/v1/demo/session")
    def demo_session(body: DemoSessionRequest, request: Request):
        if not demo_mode:
            raise HTTPException(404, "Not found")
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise HTTPException(403, "Local demo only")
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            allowed = {f"{request.url.scheme}://{request.url.netloc}",
                       "http://127.0.0.1:5173", "http://localhost:5173"}
            if origin not in allowed or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise HTTPException(403, "Invalid demo origin")
        token = next((key for key, employee_id in tokens.items() if employee_id == body.employee_id), None)
        if token is None:
            raise HTTPException(404, "Demo employee not available")
        return {"token": token}

    @app.get("/v1/workspace")
    def workspace(language: Language = "en", actor: Employee = Depends(authorize)):
        documents = [service.read(actor.id, doc.id, language) for doc in repository.documents()
                     if can_read(actor, doc)]
        organization = repository.organization(actor.organization_id)
        return {"identity": {"id": actor.id, "name": actor.name.localized(language),
                             "organization_id": actor.organization_id},
                "organization": organization.name.localized(language),
                "documents": documents,
                "projects": [{"id": p.id, "name": p.name.localized(language)}
                             for p in repository.projects() if p.organization_id == actor.organization_id],
                "agent_available": app.state.agent is not None, "persistence_available": database is not None, "cases_available": database is not None}

    @app.get("/health/live")
    def live():
        return {"status": "ok", "profile": "sla-deal-desk"}

    @app.get("/v1/me", response_model=IdentityResponse)
    def me(language: Language = "en", actor: Employee = Depends(authorize)):
        return IdentityResponse(employee_id=actor.id, organization_id=actor.organization_id,
                                name=actor.name.localized(language), team_ids=actor.team_ids)

    @app.post("/v1/knowledge/search", response_model=SearchResponse)
    def search(request: SearchRequest, actor: Employee = Depends(authorize)):
        return service.search(actor.id, request.query, request.language, request.limit)

    @app.get("/v1/documents/{document_id}", response_model=DocumentView)
    def read(document_id: str, language: Literal["en", "zh"] = "en",
             actor: Employee = Depends(authorize)):
        try:
            return service.read(actor.id, document_id, language)
        except LookupError:
            raise HTTPException(404, "Document not found") from None

    @app.get("/v1/files/{artifact_id}")
    def download_file(artifact_id: str, actor: Employee = Depends(authorize)):
        try:
            artifact, path = files.read(actor.id, artifact_id, database=database)
            if database:
                service.cases.check_export(actor.id, artifact_id)
                from .durable_api import evidence_available
                run_id = database.artifact_run(actor.id, artifact_id)
                if run_id and not evidence_available(database, service, actor, run_id):
                    raise LookupError('File evidence is no longer available')
        except LookupError:
            raise HTTPException(404, "File not found") from None
        return FileResponse(path, filename=artifact.filename, media_type=artifact.media_type,
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "sandbox"})

    async def execute_agent(operation):
        if app.state.agent is None:
            raise HTTPException(503, "Set GOOGLE_API_KEY in .env and restart to enable the agent")
        try:
            app.state.capacity.acquire_nowait()
        except anyio.WouldBlock:
            raise HTTPException(429, "Agent is busy; retry later", headers={"Retry-After": "5"}) from None
        try:
            with anyio.fail_after(request_timeout):
                return await operation()
        except CapacityError as exc:
            raise HTTPException(429,str(exc),headers={'Retry-After':'60'}) from None
        except TimeoutError:
            raise HTTPException(504, "Agent request deadline exceeded") from None
        except PermissionError:
            raise HTTPException(403, "Employee access is no longer available") from None
        except (genai.errors.APIError, httpx.HTTPError):
            raise HTTPException(503, "Language model unavailable; retry later") from None
        except ValueError:
            raise HTTPException(502, "Language model returned an unusable response") from None
        finally:
            app.state.capacity.release()

    @app.post("/v1/agent/ask", response_model=AgentResponse)
    async def ask(request: AgentRequest, actor: Employee = Depends(authorize)):
        return await execute_agent(lambda: app.state.agent.ask(actor.id, request))


    if database:
        @app.get('/v1/runs/{run_id}/plan')
        def inspect_plan(run_id: str, actor: Employee = Depends(authorize)):
            from .agents.storage import inspect
            try: return inspect(database, actor, run_id)
            except LookupError: raise HTTPException(404, 'Plan unavailable') from None

        from .context.api import install_routes as install_context_routes
        install_context_routes(app,database,service,authorize)
        from .runtime.api import install_routes as install_runtime_routes
        install_runtime_routes(app,database,authorize)
        from .durable_api import install_routes
        install_routes(app, database, service, authorize, execute_agent, request_timeout)
        from .sla.api import install_routes as install_case_routes
        install_case_routes(app, service.cases, files, authorize)
        from .workflows.service import WorkflowService
        from .workflows.engine import WorkflowEngine
        from .workflows.api import install_routes as install_workflow_routes
        workflow_service = WorkflowService(database)
        install_workflow_routes(app, workflow_service, WorkflowEngine(workflow_service), authorize)
        from .deals.service import DealService
        from .deals.workflow import DealWorkflowService
        from .deals.api import install_routes as install_deal_routes
        from .deals.domain import StartDealWorkflow, DecideDeal, PreviewDeal
        install_deal_routes(app, DealService(database), authorize)
        deal_workflows = DealWorkflowService(database)
        install_workflow_routes(app, deal_workflows, WorkflowEngine(deal_workflows), authorize,
            prefix='/v1/deal-workflows', StartSchema=StartDealWorkflow, DecisionSchema=DecideDeal, PreviewSchema=PreviewDeal)

    web_dist = Path(__file__).resolve().parents[1] / "apps" / "web" / "dist"

    @app.get("/", include_in_schema=False)
    def frontend():
        if not (web_dist / "index.html").is_file():
            raise HTTPException(503, "Build the UI: cd apps/web && npm ci && npm run build")
        return FileResponse(web_dist / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/ui", StaticFiles(directory=web_dist, check_dir=False, html=True), name="web")
    return app
