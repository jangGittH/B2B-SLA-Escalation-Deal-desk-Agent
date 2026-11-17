"""Exact, bounded commercial policy checks. No generated text authorizes price changes."""
from calendar import monthrange
from datetime import datetime
from decimal import Decimal, localcontext, ROUND_HALF_UP
from .domain import Deal, PricingPolicy


def add_months(value, count):
    year, month = divmod(value.year * 12 + value.month - 1 + count, 12)
    month += 1
    return value.replace(year=year,month=month,day=min(value.day,monthrange(year,month)[1]))


def terms(policy, discount):
    with localcontext() as ctx:
        ctx.prec = 32
        fee = (policy.monthly_list_price * (1-discount)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        margin = ((fee-policy.monthly_cost)/fee) if fee>0 and policy.monthly_cost is not None else None
        return fee, margin


def calculate(deal: Deal, policy: PricingPolicy, contract, sandbox, at: datetime, discount=None):
    discount = deal.requested_discount if discount is None else discount
    missing, exceptions = [], []
    result = dict(status='missing_inputs',missing_inputs=missing,exceptions=exceptions,proposal_amount=None,
        currency=policy.currency,formula_version='deal-renewal-v1',
        formula='monthly_fee = round(list_price × (1 - discount), 2); margin = (monthly_fee - cost) / monthly_fee',
        monthly_cost=str(policy.monthly_cost) if policy.monthly_cost is not None else None,
        discount_fraction=str(discount) if discount is not None else None, margin_fraction=None)
    if discount is None: missing.append('requested_discount')
    if deal.renewal_end is None: missing.append('renewal_end')
    if policy.monthly_cost is None or not policy.cost_evidence: missing.append('confirmed_policy_cost')
    if not contract['signed']: missing.append('signed_contract')
    if sandbox['source_contract_version'] != contract['version']: missing.append('source_contract_reconciliation')
    if policy.currency != contract['currency'] or sandbox['currency'] != contract['currency']: missing.append('matching_currency')
    if not policy.effective_from <= at < policy.effective_until: missing.append('applicable_pricing_policy')
    if contract['starts_at'] > at: missing.append('contract_has_started')
    if deal.renewal_end is not None:
        if not sandbox['ends_at'] < deal.renewal_end <= add_months(sandbox['ends_at'],policy.max_extension_months):
            exceptions.append('renewal_end_outside_allowed_extension')
    if discount is not None:
        if not Decimal(0) <= discount <= policy.maximum_discount: exceptions.append('discount_exceeds_policy')
        fee, margin = terms(policy,discount)
        result.update(proposal_amount=f'{fee:.2f}',margin_fraction=f'{margin:.6f}' if margin is not None else None)
        if fee <= 0: exceptions.append('positive_monthly_fee_required')
        # Compare exact products, never the rounded display percentage.
        if policy.monthly_cost is not None and fee-policy.monthly_cost < fee*policy.minimum_margin:
            exceptions.append('margin_below_policy')
    missing.extend(exceptions)
    result['status'] = 'missing_inputs' if missing else 'ready'
    return result
