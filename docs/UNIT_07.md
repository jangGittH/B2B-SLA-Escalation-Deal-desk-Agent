# Unit 7 — deal desk and verified sandbox renewal

Implemented November 16, 2026. Unit 7 completes the second local business loop: renewal request → exact discount/margin checks → review memo → independent approve/modify/reject → version-checked sandbox contract update → verified receipt and closure. It reuses [Unit 6’s](UNIT_06.md) review lifecycle and checkpoint engine. [Unit 8 reliability](DEVELOPMENT_PLAN.md#7-development-sequence-and-dependency-gates) is next.

The sandbox contract is a separate copy of an existing source SLA contract. Applying a deal changes that copy’s monthly fee and expiry; it does not rewrite historical SLA terms, update a real CRM, sign an amendment or send a customer message.

## Start and demonstrate

Follow the [README](../README.md) setup. Existing installations must run `uv run python -m work_assistant db-init` before restarting: migration 0005 adds the deal tables and extends the shared workflow schema without resetting existing records. Re-seeding never overwrites human edits or applied contract terms.

1. Open `http://127.0.0.1:8001/`, switch to **Alex Wang**, and open **Deal desk → DEAL-101**.
2. Inspect the policy and quote. The fictional policy has USD 1,000.00 monthly list price, USD 600.00 monthly cost, maximum 20% discount and minimum 25% margin. DEAL-101 requests 10%, producing USD 900.00/month and a 33.33% displayed margin. Its requested expiry is January 1, 2028.
3. Choose **Start deal review**. The server saves a versioned review memo and designates independent Operations reviewer **Lin Zhao**. The workflow pauses; no contract has changed.
4. Switch to **Lin**, select the same deal, and read its memo and before/after terms. Approve, reject with a reason, or reduce the discount. Entering **5%** and previewing produces exactly **USD 950.00/month**; provide a reason and choose **Modify and approve**.
5. Inspect the verified receipt, then reload the deal. The sandbox contract is version 2, with the approved monthly fee and expiry. The same deal cannot apply again under another request key or workflow.

DEAL-102 requests 30%, exceeding both the discount and margin rules. Starting it persists explicit exceptions instead of requesting approval. Alex can edit the request and recheck the workflow. Empty discount/end-date fields remain missing; a missing policy cost requires an Operations employee to enter a cost attestation, evidence note and change reason. Policy edits create a new version and invalidate affected pending reviews.

Both seed requests share a contract. Once one extends it, the other must be evaluated against the new sandbox end date; it cannot silently apply the same renewal again. This is intentional conflict handling. The example pricing policy is valid during calendar 2026. Requests outside its validity require an applicable policy, not an invented extension.

The structured UI works without a model key. With a configured model, ask as Alex: “Calculate the price and margin for DEAL-101, then start a deal review.” The assistant can inspect, submit and resume an owned workflow; human approval remains in the review UI.

## Delivered functions and implementation

| Function | Code and behavior |
|---|---|
| Deal records and intake | `deals/domain.py`, `tables.py`, `service.py`: strict tenant-scoped requests linked to existing accounts/contracts; list, create, owner-only versioned edits and before/after audit |
| Price/cost policies | Typed list price, currency, cost/evidence, maximum discount, minimum margin, extension limit and validity; Operations-only cost attestation increments policy version with audit |
| Exact quote and exception checks | `deals/calculator.py`: Decimal monthly fee, exact margin comparison, date/currency/signed-contract validation and explicit missing/exception fields |
| Shared durable review | `workflows/service.py`, `engine.py`, `api.py`: existing graph, pause/resume, independent reviewer, expiry, cancellation, retry budgets, idempotent decisions, progress and receipt storage |
| Deal adapter and memo | `deals/workflow.py`: shared evidence intersection, immutable proposal memo, exact renewal payload, modification rules, contract mutation and independent read-back |
| User interface | `DealsPanel.tsx`: bilingual table, request editor, quote, policy cost form and contract status; shared `WorkflowPanel.tsx` renders both kinds of review |
| Conversation tools and provenance | `tools.py`, `knowledge.py`, `policy.py`: six deal tools, citable current deal/workflow sources, server-bound identity and closed Tier 2 model gate |
| Regression evidence | `tests/test_deals.py` and `apps/web/tests/deals.spec.ts`: business-state assertions, real process-death/concurrency tests, HTTP and browser checks |

### Calculation rules

`monthly_fee = round_half_up(monthly_list_price × (1 − discount), 2)`

`margin = (monthly_fee − monthly_cost) / monthly_fee`

The margin gate compares `monthly_fee − monthly_cost >= monthly_fee × minimum_margin` with Decimal values. It never compares a rounded display percentage. The proposed fee must be positive; known cost requires an evidence note. Unknown cost is not zero. A requested discount must be between zero and the policy maximum, and expiry must extend the current sandbox end date by no more than the policy’s permitted calendar months (twelve in the fixture).

Currency mismatch, an unsigned/not-yet-started source contract, an expired pricing policy, or a changed source-contract baseline prevents approval. Out-of-policy requests remain waiting for corrected facts/terms; this unit provides no override or model-controlled exception authority.

A reviewer may reduce the requested discount, including to zero, while preserving the renewal end date. The server recomputes the fee and margin for both preview and approval. Increasing the discount, editing arbitrary contract fields, changing currency/recipients, or modifying policy through the approval payload is rejected. Full approval accepts the original verified request.

### Review and execution boundaries

Deal record reads are tenant-scoped; only the owner edits or submits a request. Policy cost changes require current Operations membership. Workflow reads are limited to its requester and designated active independent Operations reviewer. Source documents in the review memo must be readable by both participants. The model cannot select the reviewer, approve a proposal or call `update_contract`.

Approval binds the exact approval ID/version, normalized original and approved payload hashes, deal/policy/source/sandbox contract versions, shared evidence, memo, reviewer, 24-hour expiry and before/after terms. Editing the request, policy, source evidence or contract requires fresh review. Even after a decision is saved, execution rechecks current facts, authority, payload integrity and expiry. A decision for an older proposal cannot approve its replacement simply because both have version 1.

The source contract version and sandbox baseline version are separate. A source-contract edit makes the copied baseline require reconciliation; the application does not automatically adopt changed signed terms. A sandbox update increments its own version. Contract changes and shared receipts commit in one transaction, together with an immutable `deal_contract_effects` row. A unique tenant/deal constraint prevents applying the same request again; stable operation IDs are `deal:<workflow-id>:<proposal-revision>`.

The verify step independently reads the sandbox contract, effect, approval and receipt. It checks version, fee, expiry, currency, source baseline and matching IDs/hashes. Mismatch produces `partial` and preserves evidence for reconciliation. It never retries an already committed renewal as a second update. If another renewal changes the contract before verification, the conservative outcome is reconciliation, not a false success. Rejection retains feedback and stops; cancellation is allowed only before commitment.

A proposal memo is immutable within its approval revision. Rechecking changed facts creates a new memo/proposal. Deal memo editing and downloadable deal exports are not included; the authenticated review UI and workflow source expose the current memo. Existing private SLA memo editing/export remains available. Arbitrary file writing from deal/workflow evidence is blocked so it cannot bypass provenance checks.

### Shared persistence and recovery

New tables are `deal_requests`, `deal_pricing_policies`, `deal_sandbox_contracts`, `deal_memos`, `deal_contract_effects` and `deal_record_events`. The existing `sla_workflows`, `sla_approvals`, `sla_action_receipts` and `sla_workflow_events` table names are retained for compatibility and now hold the common lifecycle. Each workflow must have exactly one subject: ticket or deal. Separate active-request indexes prevent duplicate active reviews.

The graph implementation is shared; business-specific proposal/execute/verify methods are supplied by the deal adapter. Threads use `northstar-deal-v1:<id>` and a separate advisory-lock namespace. SLA endpoints cannot resume/read deal workflows, or vice versa. Startup recovery dispatches each kind through its matching adapter. A worker that dies after the contract transaction but before the graph checkpoint resumes from saved state and does not redraft or apply another revision.

Unit 6’s operating limits remain: a shared in-process workflow capacity of two, bounded transient retries, five proposal revisions, polling progress and startup recovery of up to fifty runnable workflows per kind. There is no periodic distributed queue/sweeper yet. Attempt/decision/outcome events persist, including closure duration and unknown inference cost. This deterministic graph adds no model calls; conversation usage remains tracked separately. Production identity, live CRM integration, aggregate cost/latency dashboards, dynamic planning and real business ROI remain later milestones.

## API and tool contract

All endpoints require an active employee credential; PostgreSQL is required. `/docs` exposes exact schemas. Extra fields and model-supplied identities are rejected.

| Endpoint | Inputs and effect |
|---|---|
| `GET /v1/deals/options` | Tenant contract and pricing-policy choices |
| `GET /v1/deals?query=...` | Up to fifty tenant requests |
| `POST /v1/deals` | Create owned request: ID, title, contract/policy IDs, optional discount/end date, evidence note |
| `GET /v1/deals/{id}` | Linked source/sandbox terms, exact quote, exceptions, current edit authority and any applied terms |
| `PATCH /v1/deals/{id}` | Owner edits request with expected version and reason; applied requests are immutable |
| `PATCH /v1/pricing-policies/{id}/cost` | Operations attests monthly cost/evidence with expected policy version and change reason |
| `POST /v1/deal-workflows` | Owner submits deal ID/version and request key; optional owned conversation; returns 202 |
| `GET /v1/deal-workflows?deal_id=...`, `GET /v1/deal-workflows/{id}` | Authorized saved review, memo, progress, decision and receipt |
| `POST /v1/deal-workflows/{id}/preview` | Reviewer supplies exact approval ID/version and `discount_fraction` |
| `POST /v1/deal-workflows/{id}/decision` | Approve/modify/reject with exact approval ID/version and request key; modifications/rejections require reason; returns 202 |
| `POST /v1/deal-workflows/{id}/resume`, `/cancel` | Owner supplies workflow expected version; shared recovery/cancellation rules apply |

Tools: `list_deals`, `get_deal`, `calculate_deal_terms`, `start_deal_workflow`, `get_deal_workflow`, `resume_deal_workflow`. Read a deal before submitting its version and read a workflow before resuming its version. Start binds the current conversation/run and idempotency key on the server. Sources use `deal:<id>` and `deal-workflow:<id>`; current snapshots are reauthorized on subsequent reads. No approval, contract-edit or cost-policy mutation tool is exposed to the model.

## Verification and scenario coverage

Verified with **174 Python tests against PostgreSQL**, **16 browser tests**, and a successful TypeScript/Vite build. Unit 7 adds **35** deterministic deal cases and three browser flows. Alongside the 31 Unit 6 workflow cases, this exceeds the Unit 7 gate of thirty scenarios across both workflows. These are application correctness checks, not the full held-out 60-case live-model benchmark or measured pilot ROI.

| Test cases | Independently asserted outcome |
|---|---|
| 1 | USD 900.00 renewal, version 2 sandbox contract, matching memo/receipt and unchanged source SLA terms |
| 2–5 | Modified discount 0%, 5%, 10%, 3.33% gives exact USD 1,000.00, 950.00, 900.00, 966.70 |
| 6 | Reviewer discount increase and tampered approved payload cannot execute |
| 7 | Out-of-policy request pauses; owner corrects terms and resumes the same workflow |
| 8 | Missing cost waits; only Operations can attest; stale cost writes conflict |
| 9–16 | Changed request, pricing policy, source contract, sandbox contract, memo, expiry, reviewer or source evidence blocks approved execution |
| 17 | Wrong tenant, non-owner submit, wrong workflow kind and self-approval are denied |
| 18 | Rejection retains feedback; explicit cancellation applies no contract change |
| 19 | Duplicate submissions/decisions and two graph drivers produce one renewal |
| 20 | Two approved renewals for one contract: the first applies, the second becomes stale |
| 21 | Subprocess exits immediately after transaction commit; new engine verifies without another revision |
| 22 | Failed read-back yields partial outcome and never reapplies or hides the committed effect |
| 23 | HTTP validation and authorization; review survives new app instance and closes correctly |
| 24–29 | Exact margin boundary, one-cent cost breach, excessive discount, zero cost, missing cost and missing discount |
| 30–33 | Currency mismatch, expired policy, excessive extension and absent renewal date prevent approval |
| 34 | Stale request edit and old approval ID cannot replace/approve current terms |
| 35 | Conversation tool binds the correct run/conversation, pauses for a human, blocks direct contract mutation and cross-tenant reads |

Browser tests cover create/edit/missing-facts/rejection; modified approval, exact preview, receipt and reload; Operations cost editing, tenant isolation and Chinese mobile layout. Desktop review/closure and mobile screenshots were visually inspected. Existing SLA and workspace tests remain green.

```bash
TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run pytest -q
npm run build --prefix apps/web
```

Use [Unit 4’s isolated browser-test server](UNIT_04.md#verify) for Playwright. Tests create and drop separate databases; they never reset or apply test renewals to the demo database. Without `TEST_DATABASE_URL`, PostgreSQL tests are skipped. Scripted model tests do not establish live-model commercial judgment.

## Knowledge used and original prompt requirements

R1–R6 retain the original prompt mapping from the [development plan](DEVELOPMENT_PLAN.md#requirement-ids-used-throughout). Each row contributes to those requirements; none implies that future production/pilot work is already complete.

| Delivered function | Engineering/business knowledge used | Original requirements covered |
|---|---|---|
| Typed requests, policies and audit | Relational/composite tenant keys, strict schemas, foreign keys, optimistic concurrency, policy provenance and cost attestation | R1 backend and permissions; R6 commercial records — F01/F02/F03 |
| Discount/margin/renewal calculations | Decimal arithmetic, cent rounding, exact inequality checks, calendar-month arithmetic, list price versus cost, margin thresholds and applicability | R1 correctness; R2 deterministic verification; R6 pricing workflow — F07/F14 |
| Review memo and current evidence | Immutable snapshots, hashes, ACL intersections, confirmed numeric inputs versus human narrative, bilingual templates | R3 evidence/task context; R5 controlled sharing; R6 deal memo — F08/F14 |
| Shared human review and recovery | State machines, LangGraph interrupts/checkpoints, separation of duties, exact approval binding, expiry, retry budgets and cancellation | R1 persistence/recovery; R2 next-step/retry control; R3 durable state; R5 human collaboration — F04/F05/F09/F10/F12 |
| Atomic sandbox renewal and verification | Idempotency, unique business operations, compare-and-set versioning, transactions, receipt read-back and reconciliation | R1 concurrency; R2 self-verification; R5 controlled effects; R6 complete deal closure — F11/F14/F21 |
| UI, conversation tools and tests | Typed APIs, server-bound identity, React request cancellation, structured tool output, real-DB/process-death/browser tests | R1 API/access; R3 contextual follow-up; R4 observable/testable outcomes; R5 review controls; R6 usable workflow — F18/F19 and initial F20 |

The remaining roadmap begins with Unit 8 durable dispatch, operational measurements and restore verification. Dynamic multi-agent planning, richer memory and real-provider integration remain required later for the complete guide objective.

## Runtime follow-up

[Unit 8](UNIT_08.md) replaces request-process dispatch/startup recovery with a transactional PostgreSQL queue, independent reclaimable workers and replayable progress. HTTP acceptance is now asynchronous; the approval, calculation, version and sandbox-effect guarantees described above remain in force.
