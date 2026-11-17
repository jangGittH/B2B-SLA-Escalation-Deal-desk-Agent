from decimal import Decimal
from typing import Annotated, Literal
from pydantic import AwareDatetime, Field, model_validator
from ..models import Record, Language
from ..sla.domain import Identifier, Money

Fraction = Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)]


class PricingPolicy(Record):
    id: Identifier
    organization_id: Identifier
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=200)
    currency: Literal['USD','CNY','EUR']
    monthly_list_price: Money
    monthly_cost: Money | None = None
    cost_evidence: str = Field(default='', max_length=2000)
    maximum_discount: Fraction
    minimum_margin: Fraction
    max_extension_months: int = Field(default=12, ge=1, le=36)
    effective_from: AwareDatetime
    effective_until: AwareDatetime

    @model_validator(mode='after')
    def valid_terms(self):
        if self.monthly_list_price <= 0 or self.effective_until <= self.effective_from:
            raise ValueError('Policy requires a positive list price and valid period')
        if self.monthly_cost is not None and not self.cost_evidence:
            raise ValueError('Known cost requires an explicit evidence note')
        return self


class DealFields(Record):
    title: str = Field(min_length=1, max_length=200)
    requested_discount: Fraction | None = None
    renewal_end: AwareDatetime | None = None
    evidence_note: str = Field(default='', max_length=3000)


class CreateDeal(DealFields):
    id: Identifier
    contract_id: Identifier
    pricing_policy_id: Identifier


class EditDeal(DealFields):
    expected_version: int = Field(ge=1)
    change_reason: str = Field(min_length=1, max_length=1000)


class Deal(DealFields):
    id: Identifier
    organization_id: Identifier
    version: int = Field(default=1, ge=1)
    contract_id: Identifier
    pricing_policy_id: Identifier
    owner_id: Identifier
    updated_at: AwareDatetime


class EditCost(Record):
    expected_version: int = Field(ge=1)
    monthly_cost: Money
    cost_evidence: str = Field(min_length=1, max_length=2000)
    change_reason: str = Field(min_length=1, max_length=1000)


class StartDealWorkflow(Record):
    deal_id: Identifier
    expected_deal_version: int = Field(ge=1)
    request_key: str = Field(min_length=1,max_length=100)
    conversation_id: str | None = Field(default=None,min_length=1,max_length=32)
    language: Language = 'en'


class DecideDeal(Record):
    approval_id: str = Field(min_length=32,max_length=32)
    expected_version: int = Field(ge=1)
    request_key: str = Field(min_length=1,max_length=100)
    decision: Literal['approve','modify','reject']
    discount_fraction: Fraction | None = None
    reason: str = Field(default='',max_length=2000)

    @model_validator(mode='after')
    def explicit_edits(self):
        if self.decision in ('modify','reject') and not self.reason:
            raise ValueError('Modification or rejection requires a reason')
        if (self.decision == 'modify') != (self.discount_fraction is not None):
            raise ValueError('Only modify accepts and requires discount_fraction')
        return self


class PreviewDeal(Record):
    approval_id: str = Field(min_length=32,max_length=32)
    expected_version: int = Field(ge=1)
    discount_fraction: Fraction
