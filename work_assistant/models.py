"""Unit 1: typed domain records and fixture integrity checks."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["en", "zh"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class BilingualText(Record):
    en: str = Field(min_length=1)
    zh: str = Field(min_length=1)

    def localized(self, language: Language) -> str:
        return self.zh if language == "zh" else self.en


class Organization(Record):
    id: str
    name: BilingualText


class Team(Record):
    id: str
    organization_id: str
    name: BilingualText


class Employee(Record):
    id: str
    organization_id: str
    name: BilingualText
    team_ids: tuple[str, ...] = ()
    active: bool = True


class Project(Record):
    id: str
    organization_id: str
    name: BilingualText


class Document(Record):
    id: str
    organization_id: str
    project_id: str | None = None
    title: BilingualText
    content: BilingualText
    version: int = Field(ge=1)
    updated_at: datetime
    visibility: Literal["organization", "restricted"]
    reader_team_ids: tuple[str, ...] = ()
    reader_employee_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def explicit_access(self):
        if self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must include a timezone")
        if self.visibility == "organization" and (self.reader_team_ids or self.reader_employee_ids):
            raise ValueError("Organization-visible documents must not also declare restricted grants")
        if self.visibility == "restricted" and not (self.reader_team_ids or self.reader_employee_ids):
            raise ValueError("Restricted documents need at least one explicit reader grant")
        return self


class Workspace(Record):
    fixture_version: str
    organizations: tuple[Organization, ...]
    teams: tuple[Team, ...]
    employees: tuple[Employee, ...]
    projects: tuple[Project, ...]
    documents: tuple[Document, ...]

    @model_validator(mode="after")
    def valid_relationships(self):
        def index(records):
            result = {record.id: record for record in records}
            if len(result) != len(records):
                raise ValueError("Duplicate IDs within a record type")
            return result

        organizations = index(self.organizations)
        teams = index(self.teams)
        employees = index(self.employees)
        projects = index(self.projects)
        index(self.documents)
        for record in (*self.teams, *self.employees, *self.projects, *self.documents):
            if record.organization_id not in organizations:
                raise ValueError(f"Unknown organization on {record.id}")

        def same_org(ids, records, organization_id):
            for record_id in ids:
                if record_id not in records or records[record_id].organization_id != organization_id:
                    raise ValueError(f"Unknown or cross-organization reference: {record_id}")

        for employee in self.employees:
            same_org(employee.team_ids, teams, employee.organization_id)
        for document in self.documents:
            same_org(document.reader_team_ids, teams, document.organization_id)
            same_org(document.reader_employee_ids, employees, document.organization_id)
            if document.project_id is not None:
                same_org((document.project_id,), projects, document.organization_id)
        return self
