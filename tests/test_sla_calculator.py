"""Independent expected outcomes for the fictional sla-minute-v1 policy."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from work_assistant.sla.domain import Contract, Policy, Ticket
from work_assistant.sla.calculator import calculate


def inputs(**changes):
    policy = Policy(id='gold-v1', organization_id='northstar', name='Fictional gold',
        response_minutes=60, credit_rate_per_minute='0.0005', monthly_cap_fraction='.05',
        effective_from='2026-01-01T00:00:00Z', effective_until='2027-01-01T00:00:00Z')
    contract = Contract(id='contract', organization_id='northstar', account_id='acme', policy_id=policy.id,
        signed=True, currency='USD', monthly_fee='1000.00', starts_at=policy.effective_from, ends_at=policy.effective_until)
    fields = dict(id='INC-1', organization_id='northstar', contract_id=contract.id, title='Example', assignee_id='mei',
        reported_at='2026-11-14T01:00:00Z', first_response_at='2026-11-14T02:00:00Z',
        incident_started_at='2026-11-14T01:00:00Z', incident_ended_at='2026-11-14T03:00:00Z',
        exclusions=[{'start':'2026-11-14T01:00:00Z', 'end':'2026-11-14T01:30:00Z'}],
        eligibility_confirmed=True, prior_credit_amount='0.00', billing_period_start='2026-11-01T00:00:00Z',
        billing_period_end='2026-12-01T00:00:00Z', updated_at=datetime.now(timezone.utc))
    return Ticket(**(fields | changes)), contract, policy


@pytest.mark.parametrize('changes, expected, label', [
    ({}, '45.00', 'standard_eligible'),
    ({'exclusions': []}, '50.00', 'cap'),
    ({'eligibility_confirmed': False}, '0.00', 'ineligible'),
    ({'prior_credit_amount': '49.99'}, '0.01', 'remaining_cap'),
    ({'prior_credit_amount': '70.00'}, '0.00', 'cap_exhausted'),
    ({'incident_ended_at':'2026-11-14T01:00:00Z'}, '0.00', 'zero_downtime'),
    ({'exclusions': [{'start':'2026-11-14T00:00:00Z', 'end':'2026-11-14T01:20:00Z'},
                     {'start':'2026-11-14T01:10:00Z', 'end':'2026-11-14T01:30:00Z'},
                     {'start':'2026-11-14T04:00:00Z', 'end':'2026-11-14T05:00:00Z'}]}, '45.00', 'clip_union'),
    ({'incident_ended_at':'2026-11-14T01:00:00.600000Z', 'exclusions':[]}, '0.01', 'half_cent_rounding'),
])
def test_exact_credit_scenarios(changes, expected, label):
    result = calculate(*inputs(**changes))
    assert result['status'] == 'calculated', label
    assert result['proposal_amount'] == expected
    assert result['execution_status'] == 'not_executed' and result['approval_required'] is True


@pytest.mark.parametrize('field', ['incident_started_at','incident_ended_at','exclusions',
    'eligibility_confirmed','prior_credit_amount','billing_period_start','billing_period_end'])
def test_unknown_inputs_are_not_zero(field):
    result = calculate(*inputs(**{field: None}))
    assert result['status'] == 'missing_inputs' and field in result['missing_inputs']
    assert result['proposal_amount'] is None


def test_response_boundary_timezone_and_credit_are_independent():
    result = calculate(*inputs(first_response_at='2026-11-14T10:00:00+08:00'))
    assert result['response_status'] == 'met'
    result = calculate(*inputs(first_response_at='2026-11-14T02:00:00.000001Z'))
    assert result['response_status'] == 'breached' and result['proposal_amount'] == '45.00'


@pytest.mark.parametrize('kind', ['unsigned', 'expired', 'severity', 'period'])
def test_policy_applicability_requires_clarification(kind):
    ticket, contract, policy = inputs()
    if kind == 'unsigned': contract = contract.model_copy(update={'signed':False})
    if kind == 'expired': policy = policy.model_copy(update={'effective_until': datetime(2026, 10, 1, tzinfo=timezone.utc)})
    if kind == 'severity': ticket = ticket.model_copy(update={'severity':'P2'})
    if kind == 'period': ticket = ticket.model_copy(update={'billing_period_end': datetime(2026, 11, 14, tzinfo=timezone.utc)})
    result = calculate(ticket, contract, policy)
    assert result['proposal_amount'] is None and result['missing_inputs']


def test_invalid_timestamps_negative_money_and_precision_rejected():
    for changes in [dict(reported_at='2026-11-15T00:00:00'), dict(prior_credit_amount='-1'),
                    dict(prior_credit_amount='0.001'), dict(incident_ended_at='2026-11-13T00:00:00Z'),
                    dict(exclusions=[{'start':'2026-11-14T02:00:00Z','end':'2026-11-14T01:00:00Z'}])]:
        with pytest.raises(ValidationError): inputs(**changes)


def test_nonmonthly_billing_period_is_unsupported():
    result = calculate(*inputs(billing_period_start='2026-11-10T00:00:00Z'))
    assert result['proposal_amount'] is None
    assert 'utc_calendar_month_billing_period' in result['missing_inputs']
