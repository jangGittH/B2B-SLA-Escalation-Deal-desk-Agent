# SLA Escalation / Deal Desk data strategy

Use a coherent fictional B2B service-provider dataset that supports the entire business loop: intake, evidence gathering, exact calculations, private memos, human decisions, verified actions, and closure metrics. This is the active data scope; generic collaboration demos and unrelated benchmark corpora have been removed.

## Existing seed

`work_assistant/data/workspace.json` defines two tenants, their employees/teams, customer evidence groupings, and eight bilingual documents. Northstar's fictional Acme account has SLA terms, incident INC-104 history, an engineering runbook, private investigator notes, a renewal request, and a restricted credit-review policy. Another tenant supplies an isolation test case. The guide is shared within Northstar.

These documents are **development evidence**, not live CRM rows, complete signed contracts, or an oracle proving a credit is owed. The incident's end time and root cause remain unknown; the proposed discount is unapproved. Preserve that uncertainty in answers and generated artifacts.

## Typed Unit 5 seed

`work_assistant/sla/seed.py` inserts tenant-scoped account, contract, versioned fictional policy and incident-ticket rows after migration 0003. INC-104 retains unknown incident inputs; INC-105 is a separate fully specified USD 45.00 calculation example. The policy uses P1, 24/7 elapsed time, UTC calendar months, a per-minute rate and monthly cap. These are explicit synthetic demo terms, not inferred business rules or production evidence. Re-seeding preserves existing records and edits.

Case and memo versions, source snapshots, private revisions and audit/export records are now implemented. Unit 6 adds proposal snapshots, approvals, workflow checkpoints/events, the sandbox credit ledger and atomic action receipts. Unit 7 adds typed deal requests, versioned price/cost policies, sandbox contract copies, review memos, record audits and immutable contract-effect records. Both workflows share the approval/receipt lifecycle.

The sandbox ledger is authoritative for workflow monthly caps; human-entered prior-credit attestations remain visible context and cannot create, erase or authorize ledger entries. The demo reviewer policy requires a separate active Operations employee. Memos shared through review contain only evidence currently readable by both participants. There are no pre-approved or pre-executed seed credits.

## Typed Unit 7 seed

`work_assistant/deals/seed.py` inserts the fictional `renewal-standard` policy: USD 1,000.00 monthly list price, USD 600.00 attested monthly cost, at most 20% discount, at least 25% margin, and at most twelve months of contract extension. Its stated validity is calendar 2026. DEAL-101 requests 10% discount and renewal through January 1, 2028; DEAL-102 requests an out-of-policy 30%. Alex owns both. The other tenant has its own policy and request. Seed runs preserve edits and do not apply renewals.

Sandbox contract copies retain the source contract version. Only approved deal execution changes their monthly fee and end date; the source SLA contract remains untouched so historical SLA calculations are not retroactively repriced. Source-contract changes block review until the sandbox baseline is reconciled. Operations may attest cost changes with a new policy version and before/after audit; this is demo governance, not an automatic financial data feed.

## Domain data across the roadmap

| Data | Purpose | Required properties |
|---|---|---|
| Customer accounts and contacts | Resolve the correct customer and recipients | Tenant scope, stable IDs, contact roles and channel permissions |
| Contracts and SLA-policy revisions | Determine eligible response/credit rules | Effective dates, service scope, exclusions, currency, caps and explicit policy version |
| Incidents and ticket histories | Investigate reported impact and progress | Timezone-aware timestamps, severity, confirmed versus claimed facts, assignee and revision |
| Pricing/cost policies and deal requests | Evaluate discount and margin proposals | Decimal amounts, currency, costs, approval rules, proposal versus approved state |
| Memos and customer-response drafts | Review supported narrative and proposed actions | Claim-level evidence IDs, row/document versions, private/public status |
| Proposals, approvals and receipts | Verify controlled execution | Exact payload, approver, expiry, expected versions, idempotency key, observed outcome |
| Run/model/outcome events | Measure reliability, cost and business value | Correlation IDs, latency, token usage, unknown costs explicit, complete denominators |

Extend the typed transactional records already implemented in Unit 5. Do not parse contract amounts from a generated narrative to execute a credit. The existing `projects` grouping is a temporary evidence organization mechanism, not a substitute for an account/contract model.

## Scenario design

Unit 5 implements the initial ten automated business scenario groups; [Unit 6](UNIT_06.md) adds 31 workflow test cases including independent review, amount changes, staleness, duplicate effects and process-death recovery. Unit 7 adds 35 deal tests, including exact margin boundaries, missing costs, expired policy, concurrent renewals and process-death recovery. These deterministic tests are a growing subset of the full 60-case, two-workflow evaluation in the development plan. Include multiple accounts with similar names, conflicting policy revisions, missing downtime evidence, unauthorized documents, expired approvals, human edits during reasoning, duplicate intake, failed tools, and timeouts after an action commits.

Maintain separate development and held-out customer/policy families. Store expected calculations, allowed tool trajectories, forbidden actions and final-state assertions outside the runtime knowledge corpus. Hidden test answers must never enter agent prompts through search.

Every source and generated result carries tenant, source ID, version and provenance. Current access is authoritative; a prior read, stored summary or memory cannot bypass a revoked grant. Preserve source dates and timezones, and explicitly represent unknown fields.

Public datasets may be evaluated later only if they test a defined capability in these workflows. Document provenance, licensing, transformations, selection, and limitations before adding any dataset. Synthetic test results demonstrate engineering behavior; a measured customer pilot is needed to establish business ROI.

## Existing database cleanup

Migration 0002 retires unchanged old seed documents using content fingerprints, leaving customized evidence, identities, conversations, artifact metadata and historical runs intact. The new product seed is inserted by `db-init`. Legacy workflow records are not execution inputs for this product. Unit 6 uses `sla_*` tables and the `northstar-sla-v1:` checkpoint thread namespace. Unit 7 extends those review tables with a mutually exclusive deal subject and uses `northstar-deal-v1:` threads; it never resumes legacy workflow IDs. Do not reset a user's database to obtain a cleaner demo.

## Unit 8 operational evidence

Migration 0006 stores durable delivery generations/leases, worker attempts, tenant scheduler state, central capacity/provider limits and new-workflow timing. Existing workflow history stays intact; pre-migration timing is unknown and excluded from timing percentiles. Load tests create and remove isolated synthetic databases. No customer records, real credits or external sends are used. Backup bundles contain private database and artifact data and must remain access-restricted; see [Operations](OPERATIONS.md).
