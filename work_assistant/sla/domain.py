from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator
from ..models import Record

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')]
Money = Annotated[Decimal, Field(ge=0, le=Decimal('999999999'), max_digits=11, decimal_places=2)]


class Account(Record):
    id: Identifier
    organization_id: Identifier
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(min_length=1, max_length=200)
    contact_email: str = Field(min_length=1, max_length=200)


class Policy(Record):
    id: Identifier
    organization_id: Identifier
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=200)
    response_minutes: int = Field(ge=1, le=10080)
    credit_rate_per_minute: Decimal = Field(gt=0, le=1, max_digits=10, decimal_places=8)
    monthly_cap_fraction: Decimal = Field(gt=0, le=1, max_digits=6, decimal_places=5)
    effective_from: AwareDatetime
    effective_until: AwareDatetime

    @model_validator(mode='after')
    def period(self):
        if self.effective_until <= self.effective_from:
            raise ValueError('Policy period must be positive')
        return self


class Contract(Record):
    id: Identifier
    organization_id: Identifier
    version: int = Field(default=1, ge=1)
    account_id: Identifier
    policy_id: Identifier
    signed: bool
    currency: Literal['USD', 'CNY', 'EUR']
    monthly_fee: Money
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode='after')
    def period(self):
        if self.ends_at <= self.starts_at:
            raise ValueError('Contract period must be positive')
        return self


class Window(Record):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode='after')
    def positive(self):
        if self.end <= self.start:
            raise ValueError('Window must have positive duration')
        return self


class IncidentFields(Record):
    title: str = Field(min_length=1, max_length=200)
    severity: Literal['P1', 'P2', 'P3'] = 'P1'
    status: Literal['open', 'investigating'] = 'open'
    assignee_id: Identifier
    reported_at: AwareDatetime
    first_response_at: AwareDatetime | None = None
    incident_started_at: AwareDatetime | None = None
    incident_ended_at: AwareDatetime | None = None
    exclusions: list[Window] | None = Field(default=None, max_length=100)
    eligibility_confirmed: bool | None = None
    # Explicit human attestation, not inferred from incident duration.
    prior_credit_amount: Money | None = None
    billing_period_start: AwareDatetime | None = None
    billing_period_end: AwareDatetime | None = None
    evidence_note: str = Field(default='', max_length=3000)

    @model_validator(mode='after')
    def time_order(self):
        for start, end in [(self.reported_at, self.first_response_at),
                           (self.incident_started_at, self.incident_ended_at),
                           (self.billing_period_start, self.billing_period_end)]:
            if start and end and end < start:
                raise ValueError('End timestamp cannot precede start')
        if self.billing_period_start and self.billing_period_end == self.billing_period_start:
            raise ValueError('Billing period must be positive')
        return self


class CreateTicket(IncidentFields):
    id: Identifier
    contract_id: Identifier


class EditTicket(IncidentFields):
    expected_version: int = Field(ge=1)
    change_reason: str = Field(min_length=1, max_length=1000)


class Ticket(IncidentFields):
    id: Identifier
    organization_id: Identifier
    version: int = Field(default=1, ge=1)
    contract_id: Identifier
    updated_at: AwareDatetime


class MemoNotes(Record):
    # Editable content is explicitly unverified; confirmed facts are generated from records.
    hypotheses: str = Field(default='', max_length=3000)
    next_steps: str = Field(default='', max_length=3000)
    customer_response_draft: str = Field(default='', max_length=4000)


class CreateMemo(MemoNotes):
    expected_ticket_version: int = Field(ge=1)
    language: Literal['en', 'zh'] = 'en'


class EditMemo(MemoNotes):
    expected_version: int = Field(ge=1)
    expected_ticket_version: int = Field(ge=1)


def dump(record):
    return record.model_dump(mode='json')
