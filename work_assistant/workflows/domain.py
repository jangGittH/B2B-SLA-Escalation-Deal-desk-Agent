from decimal import Decimal
from typing import Literal
from pydantic import Field, model_validator
from ..models import Record, Language
from ..sla.domain import Identifier


class StartWorkflow(Record):
    ticket_id: Identifier
    expected_ticket_version: int = Field(ge=1)
    request_key: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=32)
    language: Language = 'en'


class ResumeWorkflow(Record):
    expected_version: int = Field(ge=1)


class DecideApproval(Record):
    approval_id: str = Field(min_length=32, max_length=32)
    expected_version: int = Field(ge=1)
    request_key: str = Field(min_length=1, max_length=100)
    decision: Literal['approve','modify','reject']
    credit_fraction: Decimal | None = Field(default=None, ge=0, le=1, max_digits=5, decimal_places=4)
    reason: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def explicit_edits(self):
        if self.decision in ('modify','reject') and not self.reason:
            raise ValueError('Modification or rejection requires a reason')
        if (self.decision == 'modify') != (self.credit_fraction is not None):
            raise ValueError('Only modify accepts a credit_fraction, and requires it')
        return self


class PreviewDecision(Record):
    approval_id: str = Field(min_length=32, max_length=32)
    expected_version: int = Field(ge=1)
    credit_fraction: Decimal = Field(ge=0,le=1,max_digits=5,decimal_places=4)
