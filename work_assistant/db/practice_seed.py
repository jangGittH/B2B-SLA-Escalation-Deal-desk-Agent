"""Paired customer incidents and renewals for the fictional Northstar workspace."""
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..deals import tables as deals
from ..deals.domain import Deal, PricingPolicy
from ..sla import tables as sla
from ..sla.domain import Account, Contract, Policy, Ticket
from ..sla.seed import values


def seed(connection, workspace):
    org = 'northstar'
    employees = {e.id for e in workspace.employees if e.organization_id == org}
    projects = {p.id for p in workspace.projects if p.organization_id == org}
    if not {'mei', 'alex', 'lin'} <= employees:
        return

    def insert_missing(table, fields):
        connection.execute(insert(table).values(**fields).on_conflict_do_nothing())

    fixture = Path(__file__).resolve().parents[1] / 'data' / 'account_reviews.json'
    for scenario in json.loads(fixture.read_text(encoding='utf-8')):
        account_id = scenario['account_id']
        if account_id not in projects:
            continue
        account = Account(id=account_id, organization_id=org, name=scenario['name'],
                          contact_name=scenario['contact_name'], contact_email=f'operations@{account_id}.example')
        policy = Policy(id='hosted-p1-2026', organization_id=org, name='Hosted service P1 schedule',
                        response_minutes=30, credit_rate_per_minute='0.0005', monthly_cap_fraction='0.05',
                        effective_from='2026-01-01T00:00:00Z', effective_until='2027-01-01T00:00:00Z')
        contract = Contract(id=f'{account_id}-2026', organization_id=org, account_id=account_id,
                            policy_id=policy.id, signed=True, currency='USD', monthly_fee=scenario['monthly_fee'],
                            starts_at=policy.effective_from, ends_at=policy.effective_until)
        ticket = Ticket(**scenario['ticket'], organization_id=org, contract_id=contract.id, assignee_id='mei',
                        status='investigating', prior_credit_amount='0.00',
                        billing_period_start='2026-11-01T00:00:00Z', billing_period_end='2026-12-01T00:00:00Z')
        pricing = PricingPolicy(id=f'{account_id}-renewal', organization_id=org, name=f'{account.name} renewal',
                                currency=contract.currency, monthly_list_price=contract.monthly_fee,
                                monthly_cost=scenario['monthly_cost'], cost_evidence=scenario['cost_evidence'],
                                maximum_discount='0.20', minimum_margin='0.25',
                                effective_from=policy.effective_from, effective_until=policy.effective_until)
        deal = Deal(**scenario['deal'], organization_id=org, contract_id=contract.id,
                    pricing_policy_id=pricing.id, owner_id='alex')
        for table, record in [(sla.accounts, account), (sla.policies, policy), (sla.contracts, contract),
                              (sla.tickets, ticket), (deals.policies, pricing)]:
            insert_missing(table, values(record))
        # A prior installation may already have a newer source contract.
        source = connection.execute(select(sla.contracts).where(
            sla.contracts.c.organization_id == org, sla.contracts.c.id == contract.id)).mappings().one()
        insert_missing(deals.sandbox_contracts, dict(organization_id=org, id=contract.id, version=1,
            source_contract_version=source['version'], monthly_fee=source['monthly_fee'],
            currency=source['currency'], ends_at=source['ends_at']))
        insert_missing(deals.requests, deal.model_dump())
