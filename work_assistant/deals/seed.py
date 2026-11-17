"""Synthetic commercial terms; preserve existing edits and effects on reseed."""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from . import tables as t
from .domain import Deal, PricingPolicy
from ..sla.tables import contracts


def seed(conn, workspace):
    employees={e.id for e in workspace.employees}
    for org,contract_id,owner in [('northstar','acme-2026','alex'),('otherco','other-account-2026','outsider')]:
        if owner not in employees: continue
        contract=conn.execute(select(contracts).where(contracts.c.organization_id==org,contracts.c.id==contract_id)).mappings().first()
        if not contract: continue
        policy=PricingPolicy(id='renewal-standard',organization_id=org,name='Fictional renewal pricing',currency='USD',
            monthly_list_price='1000.00',monthly_cost='600.00',cost_evidence='Synthetic approved monthly service cost; not production data.',
            maximum_discount='0.20',minimum_margin='0.25',effective_from='2026-01-01T00:00:00Z',effective_until='2027-01-01T00:00:00Z')
        conn.execute(insert(t.policies).values(**policy.model_dump()).on_conflict_do_nothing())
        conn.execute(insert(t.sandbox_contracts).values(organization_id=org,id=contract_id,version=1,source_contract_version=contract['version'],
            monthly_fee=contract['monthly_fee'],currency=contract['currency'],ends_at=contract['ends_at']).on_conflict_do_nothing())
        for rid,discount in ([('DEAL-101','0.10'),('DEAL-102','0.30')] if org=='northstar' else [('OTHER-DEAL','0.10')]):
            deal=Deal(id=rid,organization_id=org,contract_id=contract_id,pricing_policy_id=policy.id,owner_id=owner,
                title='Acme sandbox renewal' if org=='northstar' else 'Other tenant renewal',requested_discount=discount,
                renewal_end='2028-01-01T00:00:00Z',evidence_note='Synthetic renewal request. Requires independent review; no CRM mutation.',
                updated_at=datetime(2026,11,16,tzinfo=timezone.utc))
            conn.execute(insert(t.requests).values(**deal.model_dump()).on_conflict_do_nothing())
