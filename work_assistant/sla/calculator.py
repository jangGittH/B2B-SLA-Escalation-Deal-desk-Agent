"""Fictional policy sla-minute-v1. No provider/model or financial mutations."""
from datetime import timedelta, timezone, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .domain import Contract, Policy, Ticket

FORMULA_VERSION = 'sla-minute-v1'


def minutes(start, end):
    delta = end - start
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return Decimal(micros) / Decimal(60_000_000)


def calculate(ticket: Ticket, contract: Contract, policy: Policy) -> dict:
    """One confirmed incident in one attested billing period; overlapping cases block upstream."""
    result = {
        'formula_version': FORMULA_VERSION,
        'ticket_version': ticket.version, 'contract_version': contract.version,
        'policy_id': policy.id, 'policy_version': policy.version, 'currency': contract.currency,
        'status': 'missing_inputs', 'missing_inputs': [], 'proposal_amount': None,
        'execution_status': 'not_executed', 'approval_required': True,
        'rounding': 'ROUND_HALF_UP, 2 decimal places',
        'calendar': '24x7 elapsed time; UTC calendar-month billing',
        'formula': 'min(monthly_fee × eligible_minutes × rate, max(0, monthly_fee × cap - prior_credit_amount))',
        'response_deadline': None, 'response_status': 'unknown',
    }
    if (ticket.severity == 'P1' and contract.signed
            and contract.starts_at <= ticket.reported_at < contract.ends_at
            and policy.effective_from <= ticket.reported_at < policy.effective_until):
        deadline = ticket.reported_at + timedelta(minutes=policy.response_minutes)
        result['response_deadline'] = deadline.isoformat()
        if ticket.first_response_at is not None:
            result['response_status'] = 'met' if ticket.first_response_at <= deadline else 'breached'
        else:
            result['response_status'] = 'awaiting_response_timestamp'
    missing = [key for key in ('incident_started_at', 'incident_ended_at', 'exclusions',
               'eligibility_confirmed', 'prior_credit_amount', 'billing_period_start', 'billing_period_end')
               if getattr(ticket, key) is None]
    if not contract.signed:
        missing.append('signed_contract')
    if ticket.severity != 'P1':
        missing.append('policy_for_severity')
    if not (contract.starts_at <= ticket.reported_at < contract.ends_at
            and policy.effective_from <= ticket.reported_at < policy.effective_until):
        missing.append('applicable_contract_and_policy')
    if ticket.incident_started_at and ticket.incident_ended_at:
        if not (max(contract.starts_at, policy.effective_from) <= ticket.incident_started_at
                <= ticket.incident_ended_at <= min(contract.ends_at, policy.effective_until)):
            missing.append('incident_within_contract_and_policy')
        if ticket.billing_period_start and ticket.billing_period_end and not (
                ticket.billing_period_start <= ticket.incident_started_at <= ticket.incident_ended_at
                <= ticket.billing_period_end):
            missing.append('single_billing_period')
    if ticket.billing_period_start and ticket.billing_period_end:
        start = ticket.billing_period_start.astimezone(timezone.utc)
        expected_start = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
        expected_end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)
        if start != expected_start or ticket.billing_period_end != expected_end:
            missing.append('utc_calendar_month_billing_period')
    result['missing_inputs'] = missing
    if missing:
        return result
    with localcontext() as ctx:
        ctx.prec = 40
        total = minutes(ticket.incident_started_at, ticket.incident_ended_at)
        # Clip and union exclusions so duplicates/overlaps cannot double subtract.
        windows = sorted((max(w.start, ticket.incident_started_at), min(w.end, ticket.incident_ended_at))
                         for w in ticket.exclusions)
        merged = []
        for start, end in windows:
            if end <= start:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        excluded = sum((minutes(a, b) for a, b in merged), Decimal(0))
        eligible = total - excluded if ticket.eligibility_confirmed else Decimal(0)
        raw = contract.monthly_fee * eligible * policy.credit_rate_per_minute
        cap = (contract.monthly_fee * policy.monthly_cap_fraction).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        remaining = max(Decimal(0), cap - ticket.prior_credit_amount)
        proposal = min(raw.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), remaining)
        result.update(status='calculated', total_minutes=str(total), excluded_minutes=str(excluded),
                      eligible_minutes=str(eligible), monthly_cap=str(cap), remaining_cap=str(remaining),
                      raw_amount=str(raw), proposal_amount=f'{proposal:.2f}',
                      eligibility_confirmed=ticket.eligibility_confirmed,
                      reason='eligible_input_confirmed' if ticket.eligibility_confirmed else 'confirmed_ineligible')
    return result
