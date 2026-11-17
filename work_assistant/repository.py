"""Workspace storage protocol and read-only fixtures; PostgreSQL implements this boundary in db/store.py."""

from pathlib import Path
from typing import Protocol

from .models import Document, Employee, Workspace, Project, Organization

DEFAULT_FIXTURE = Path(__file__).parent / "data" / "workspace.json"


class WorkspaceRepository(Protocol):
    def employees(self) -> tuple[Employee, ...]: ...
    def projects(self) -> tuple[Project, ...]: ...
    def organization(self, organization_id: str) -> Organization | None: ...
    def employee(self, employee_id: str) -> Employee | None: ...
    def document(self, document_id: str) -> Document | None: ...
    def documents(self) -> tuple[Document, ...]: ...


class FixtureRepository:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self._employees = {employee.id: employee for employee in workspace.employees}
        self._documents = {document.id: document for document in workspace.documents}

    @classmethod
    def load(cls, path: Path = DEFAULT_FIXTURE):
        return cls(Workspace.model_validate_json(path.read_text(encoding="utf-8")))

    def employee(self, employee_id: str) -> Employee | None:
        return self._employees.get(employee_id)

    def document(self, document_id: str) -> Document | None:
        return self._documents.get(document_id)

    def documents(self) -> tuple[Document, ...]:
        return self.workspace.documents

    def employees(self):
        return self.workspace.employees

    def projects(self):
        return self.workspace.projects

    def organization(self, organization_id):
        return next((org for org in self.workspace.organizations if org.id == organization_id), None)
