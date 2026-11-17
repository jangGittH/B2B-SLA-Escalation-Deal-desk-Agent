"""Fictional signed demo terms; not a commercial policy or an issued credit."""
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import insert
from .domain import Account, Policy, Contract, Ticket
from .tables import accounts, policies, contracts, tickets


def values(record):
    data = record.model_dump()
    if 'exclusions' in data:
        data['exclusions'] = record.model_dump(mode='json')['exclusions']
    return data


def seed(connection, workspace):
    # Do not add Northstar records to custom workspaces or overwrite human edits.
    orgs = {o.id for o in workspace.organizations}
    employees = {e.id for e in workspace.employees}
    for org, account_id, owner in [('northstar', 'acme', 'mei'), ('otherco', 'other-account', 'outsider')]:
        if org not in orgs or owner not in employees:
            continue
        common = dict(organization_id=org, version=1)
        policy_id, contract_id = account_id + '-gold-v1', account_id + '-2026'
        account = Account(**common, id=account_id, name='Acme' if org == 'northstar' else 'Other account',
                          contact_name='Demo account owner', contact_email=f'owner@{account_id}.example')
        policy = Policy(**common, id=policy_id, name='Fictional P1 credit policy', response_minutes=60,
                        credit_rate_per_minute='0.0005', monthly_cap_fraction='0.05',
                        effective_from='2026-01-01T00:00:00Z', effective_until='2027-01-01T00:00:00Z')
        contract = Contract(**common, id=contract_id, account_id=account_id, policy_id=policy_id,
                            signed=True, currency='USD', monthly_fee='1000.00',
                            starts_at=policy.effective_from, ends_at=policy.effective_until)
        incident = Ticket(**common, id='INC-104' if org == 'northstar' else 'OTHER-1', contract_id=contract_id,
                          title='Intermittent API errors — facts pending', assignee_id=owner,
                          reported_at='2026-11-15T09:00:00+08:00', updated_at=datetime(2026, 11, 15, 1, tzinfo=timezone.utc),
                          evidence_note='Reported intermittent errors. Outage, exclusions and eligibility are unconfirmed.')
        records = [(accounts, account), (policies, policy), (contracts, contract), (tickets, incident)]
        if org == 'northstar':
            complete = Ticket(**common, id='INC-105', contract_id=contract_id,
                title='Sandbox calculation example', assignee_id=owner, status='investigating',
                reported_at='2026-11-14T09:00:00+08:00', first_response_at='2026-11-14T09:30:00+08:00',
                incident_started_at='2026-11-14T09:00:00+08:00', incident_ended_at='2026-11-14T11:00:00+08:00',
                exclusions=[{'start':'2026-11-14T09:00:00+08:00','end':'2026-11-14T09:30:00+08:00'}],
                eligibility_confirmed=True, prior_credit_amount='0.00',
                billing_period_start='2026-11-01T00:00:00Z', billing_period_end='2026-12-01T00:00:00Z',
                evidence_note='Synthetic support attestation: 120 minutes, 30 minutes maintenance excluded, no prior monthly credit. Not production evidence.',
                updated_at=incident.updated_at)
            records.append((tickets, complete))
        for table, record in records:
            connection.execute(insert(table).values(**values(record)).on_conflict_do_nothing())
