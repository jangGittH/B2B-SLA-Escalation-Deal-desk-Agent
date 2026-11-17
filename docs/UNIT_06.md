# Unit 6 — durable SLA review and verified sandbox closure

Implemented November 16, 2026. This unit completes the local SLA loop on top of [Unit 5](UNIT_05.md): intake → investigation → exact calculation → review memo → independent approve/modify/reject → one sandbox action → read-back verification → closure. [Unit 7](UNIT_07.md) now adds deal review on these controls. Unit 8 reliability is next; see the [development plan](DEVELOPMENT_PLAN.md).

A completed workflow means its reviewed sandbox operation was verified. It does **not** mean the technical incident was resolved, a real credit was issued, or a customer message was sent.

## Run and try it

Follow the [README](../README.md) to start PostgreSQL, install dependencies and build the UI. Existing installations must run `uv run python -m work_assistant db-init` before restarting the app. This applies migration 0004 and initializes the PostgreSQL checkpointer; startup itself does not migrate or overwrite existing records.

1. Open `http://127.0.0.1:8001/` as **Mei Chen**. In **SLA cases**, select **INC-105** and **Start SLA review**. The workflow calculates from the sandbox ledger, saves its memo and pauses for review.
2. Switch to **Lin Zhao**, the independent Operations reviewer, and open INC-105. Inspect the proposed amount, before/after monthly total, source versions, expiry and review memo.
3. Choose **Approve sandbox credit**, **Reject with reason**, or enter a percentage and reason, then **Preview modified credit → Modify and approve**. On a fresh ledger, INC-105 proposes USD 45.00; 50% produces exactly USD 22.50. Modifications can reduce the amount, never increase it above the verified maximum.
4. Refresh or switch back to Mei. The decision, progress and verified receipt persist. A credited case cannot create another credit through a new workflow/request key.
5. Start INC-104 to see durable missing facts. Update incident facts as its assignee, then use **Recheck facts and continue**. Changed material facts require a new reviewed proposal. A requester may cancel before commitment; rejection is terminal and never silently resubmits.

These structured UI flows work without a model key. With a configured model, ask “Start an SLA review for INC-105.” The agent reads the case version, calls the start tool and returns the saved workflow source. A follow-up can inspect or resume an owned workflow after human edits. The model has no approval tool. Reviewer identity comes from server policy, not tool arguments.

## Implementation and state boundaries

| Component | Responsibility |
|---|---|
| `work_assistant/workflows/domain.py` | Strict request schemas; separate start, resume, preview and decision inputs |
| `work_assistant/workflows/service.py` | Tenant/participant checks, exact proposals, decision authority, transactions, ledger, receipts and verification |
| `work_assistant/workflows/engine.py` | Fixed LangGraph, PostgreSQL checkpoints, human interrupts, bounded recovery and per-workflow advisory lock |
| `work_assistant/workflows/api.py` | Authenticated API, background dispatch and bounded retry scheduling |
| `work_assistant/workflows/tables.py`, migration `0004_sla_workflow.py` | Workflow, approval, sandbox-credit, receipt and audit-event tables |
| `apps/web/src/WorkflowPanel.tsx` | Bilingual review, preview, missing facts, cancellation, progress and receipt UI |
| `tools.py`, `knowledge.py`, `policy.py` | Start/get/resume tools, citable authorized workflow sources, server-owned risk tiers |

The graph has investigate, prepare, wait, execute and verify nodes. SQL business state is authoritative; a checkpoint records where graph execution paused. The graph’s thread ID is server-owned (`northstar-sla-v1:<workflow-id>`). A workflow links its ticket, private conversation, optional originating chat run, proposal revisions, memo, approval, operation and receipt. Checkpoints use JSON-compatible state with pickle fallback disabled.

The prepare transaction creates the memo and approval once. The wait node performs only the human interrupt; resumed interrupts do not repeat memo drafting. Each step checks its persisted phase, so a crash between a committed business transaction and its graph checkpoint is replay-safe. A PostgreSQL session advisory lock prevents concurrent graph writers; short transactions protect the business effects. The executor’s database constraints remain necessary even with that graph lock.

Status values include `queued`, `running`, `waiting_for_input`, `waiting_for_approval`, `approved`, `verifying`, `retry_scheduled`, `completed`, `rejected`, `cancelled`, `failed` and `partial`. A pending review releases the worker. Progress is saved in SQL and polled by the UI, so reloading preserves it.

## Human authority, evidence and exact execution

- **Independent review:** the server chooses another active employee in the same tenant’s Operations team. In the demo, Mei’s reviewer is Lin. If no independent reviewer exists, the workflow waits for that missing prerequisite. A requester cannot approve their own work. Workflow reads are limited to the requester and current designated reviewer; team membership and active status are checked again before decisions and execution.
- **Shared review evidence:** typed case facts and evidence snapshots are versioned. Review memos use only source documents both participants can currently read. Engineering-private runbooks are not promoted to Operations. Ordinary memo endpoints remain author-only; the designated reviewer reads the review memo through the workflow boundary. Current evidence access/version checks also govern workflow sources and memo exports.
- **Exact binding:** approval stores the normalized payload/hash, exact approval ID and revision, reviewer, policy version, case/contract/source/memo versions, recipients, expiry and before/after totals. Both preview and decision require the approval ID and expected version. An old decision cannot authorize a replacement proposal merely because both start at version 1. Decisions with the same key and content return the persisted result; different content conflicts.
- **Modification:** only a fraction between zero and one is editable. The server recomputes the approved amount with `Decimal` and half-up cent rounding. Modification/rejection require a reason. Unknown fields, arbitrary recipients, currencies and action types cannot be supplied through that endpoint. The original proposal and resulting approved payload remain stored.
- **Freshness:** review expires after 24 hours. Case, policy, source, memo, permissions and ledger changes invalidate the proposed action. The owner can recheck and request a new proposal, up to five revisions per workflow. Changed or expired approvals are never silently reused. Narrative notes are labeled unverified and never become financial input or a sent customer message.
- **Authoritative cap:** execution uses actual sandbox ledger entries for the matching contract and UTC billing period. Unit 5’s human-entered prior-credit field remains recorded context, not a financial balance. Contract locking serializes competing credits; a second proposal becomes stale when the first changes the ledger. Overlapping known incidents and inconsistent ledger currency/periods block execution for reconciliation.
- **Idempotency:** start requests are deduplicated by tenant/requester/key and content hash; one active workflow is permitted per case. A stable operation ID (`sla:<workflow>:<proposal-revision>`) identifies the action. Credit and receipt commit atomically. A unique tenant/case credit constraint prevents issuing another sandbox credit under a new workflow. A zero amount records a no-credit receipt, not a financial ledger entry.
- **Verification and closure:** a separate step reads back the receipt, approved hash, credit amount, currency, period, tenant, contract, case and approval IDs. A mismatch yields `partial` and requires reconciliation; it never blindly reissues a credit. Successful closure reports `workflow_closed=true`, `incident_resolved=false` and `customer_message_sent=false`. Case technical status is preserved.

Approved payloads are checked again immediately before execution, including their allowed keys, action type, risk tier and derived amount. The model’s Tier 2 tool gate stays closed; only the human-authorized workflow executor can record the sandbox effect. Cancellation after commitment is refused so it cannot hide an existing effect. No automatic financial reversal is offered in this unit.

## API and tools

All routes require an active employee token and PostgreSQL. Identity and organization are server-bound. Swagger at `/docs` includes exact request schemas.

| Endpoint | Purpose |
|---|---|
| `POST /v1/sla-workflows` | Accept a case/version, request key and optional owned conversation; return 202 with saved workflow |
| `GET /v1/sla-workflows?ticket_id=INC-105` | List up to 50 workflows visible to this participant |
| `GET /v1/sla-workflows/{id}` | Current status, missing facts, authorized review, progress and receipt |
| `POST /v1/sla-workflows/{id}/resume` | Owner rechecks facts or resumes interrupted work using workflow `expected_version` |
| `POST /v1/sla-workflows/{id}/preview` | Reviewer previews an exact modified fraction using `approval_id` and approval `expected_version` |
| `POST /v1/sla-workflows/{id}/decision` | Persist approve/modify/reject, exact approval ID/version, request key and reason; return 202 |
| `POST /v1/sla-workflows/{id}/cancel` | Owner cancels before commitment using workflow `expected_version` |

Conversation tools are `start_sla_workflow(ticket_id, expected_ticket_version)`, `get_sla_workflow(workflow_id)` and `resume_sla_workflow(workflow_id, expected_version)`. Start binds the current conversation/run server-side. The workflow result is cited as `workflow:<id>`. Generic file writing cannot bypass typed memo export/source checks after consuming case, memo or workflow evidence.

## Recovery and operating limits

The local API accepts work before background execution. It limits background workflow execution to two at a time, retries transient storage failures with persisted backoff (2 then 4 seconds, at most three failures), and recovers up to 50 queued/running/approved/verifying/retry-scheduled workflows at startup. Human-input/review waits remain paused. Owners can explicitly resume interrupted nonterminal work. Semantic missing inputs request human edits; denied/internal failures stop; an effect followed by a failure remains visible as partial.

This is a fixed, deterministic workflow with template memo preparation. It adds no model inference calls of its own; chat retains its existing model usage events. Attempt durations, decision feedback and closure duration are recorded, but closure duration includes human waiting. Inference cost remains unknown rather than fabricated as zero. A live-model quality benchmark, price schedule and aggregate business dashboard are later milestones.

The bounded in-process dispatcher is not the Unit 8 durable queue, periodic sweeper, distributed fairness or SSE implementation. Local employee switching is demo authentication, not production login. The sandbox is isolated from financial providers and customer channels. Dynamic planning, specialist agents, richer memory and semantic claim criticism remain later units. A real pilot is needed to establish business value.

## Verification and business scenarios

Verified with **139 Python tests against PostgreSQL**, **13 browser tests**, and a successful TypeScript/Vite production build. Tests use isolated databases and scripted model responses; they do not modify the demo database or establish live-model quality. Desktop approval/closure and Chinese mobile screenshots were also inspected.

```bash
TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run pytest -q
npm run build --prefix apps/web
```

For the deterministic browser server and `UI_BASE_URL`, follow [Unit 4’s test instructions](UNIT_04.md#verify). Without `TEST_DATABASE_URL`, PostgreSQL tests are skipped.

`tests/test_sla_workflows.py` supplies **31 parameterized test cases**. The rows below enumerate their business/failure assertions, exceeding the Unit 6 gate of twenty scenarios; they are not the roadmap’s complete 60-case benchmark.

| Cases | Scenario and asserted outcome |
|---|---|
| 1 | Standard INC-105: one USD 45.00 credit, matching receipt, verified closure, unchanged incident status |
| 2–5 | Modify to 50%, 0%, 100%, 33.33%: exact USD 22.50, 0.00, 45.00, 15.00; zero creates no credit entry |
| 6 | Rejection retains reason and never resubmits automatically |
| 7 | Missing INC-104 facts survive reload; edits resume the same workflow/conversation |
| 8 | Duplicate start, decision and execution return existing work/effect |
| 9 | Cross-tenant access, wrong reviewer and self-approval cannot authorize an effect |
| 10–16 | Changed ticket, policy, memo, source, reviewer role, inactive requester or expiry prevents execution |
| 17 | Facts changed after approval require a fresh proposal and human review |
| 18 | Cancellation before commitment prevents a credit |
| 19 | Cancellation after commitment cannot erase the receipt |
| 20 | A subprocess dies immediately after effect commit, before checkpoint; a new engine recovers without duplicate credit or memo |
| 21 | Concurrent graph drivers and duplicate decisions produce one effect |
| 22 | Two cases share the monthly cap; actual ledger enforces USD 45.00 + USD 5.00, ignoring a false manual prior-credit value |
| 23 | Transient failure preserves prepared work and resumes after backoff |
| 24 | Read-back mismatch produces partial outcome, with no automatic reissue |
| 25 | Tampered approved payload is denied before an effect |
| 26 | HTTP acceptance, persistent review across app instances, identity checks and schema validation |
| 27 | Conversation tool binds run/conversation, pauses for review and exposes no model approval |
| 28 | Old approval ID cannot approve a replacement proposal with the same version number |
| 29 | Repeated transient failures stop at the three-failure budget |
| 30 | No independent reviewer causes a durable prerequisite wait |
| 31 | A due automatic retry succeeds without requiring a second human decision |

Three new browser scenarios cover modified approval/receipt/reload, rejected review feedback, and missing facts/cancellation in Chinese on mobile. Existing ten browser scenarios continue to cover case/memo editing, private history, sources, files, identity switching and error handling.

## Knowledge used and original requirement coverage

R1–R6 refer to the guide’s original prompt as mapped in the [development plan](DEVELOPMENT_PLAN.md#requirement-ids-used-throughout). These are contributions to those requirements, not claims that the full roadmap is complete.

| Delivered function | Knowledge and implementation concepts | Prompt requirements and plan functions |
|---|---|---|
| Case/conversation workflow binding and clarification | Relational modeling, tenant keys, versioned state machines, task context | R1 backend; R3 persistent context; R6 case intake — F01/F02/F04 |
| Durable graph and restart recovery | LangGraph checkpoints/interrupt replay, SQL transactions, advisory locks, restart fault injection | R1 reliability; R2 fixed next-step selection; R3 durable state; R5 resume — F05/F11 |
| Shared evidence and review memo | ACL intersections, version/hash provenance, private-versus-review disclosure, deterministic templates | R1 access; R3 evidence context; R5 controlled sharing; R6 memo — F03/F07/F08 |
| Approve/modify/reject and exact previews | Separation of duties, strict schemas, Decimal rounding, canonical hashes, compare-and-set, expiry | R1 authorization; R5 human control; R6 approval — F09/F10 |
| Atomic sandbox credit and verified closure | Idempotency keys, uniqueness constraints, contract locking, authoritative ledgers, independent read-back | R1 correctness/concurrency; R2 self-verification; R5 controlled effects; R6 end-to-end outcome — F11/F14/F21 subset |
| Missing-input/conflict handling and bounded retry | Error taxonomy, exponential backoff, replay safety, budgets, explicit partial outcomes | R1 exceptions; R2 bounded recovery; R5 safe stop — F12 |
| Progress, decision audit and regression suite | Persistent events, correlated IDs, duration measurement, real-DB integration, process-death/browser tests | R4 observability/evaluation; R1/R5 failure validation; R6 outcome assertions — F18/F19, initial F20 |

Implementation references: [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) explains replay of interrupting nodes; [PostgreSQL checkpoint setup](https://docs.langchain.com/oss/python/langgraph/add-memory) describes durable checkpointers. Versions are locked in `uv.lock`; checkpointer schema setup runs only through the explicit database initialization command.

## Runtime follow-up

[Unit 8](UNIT_08.md) replaces request-process dispatch/startup recovery with a transactional PostgreSQL queue, independent reclaimable workers and replayable progress. HTTP acceptance is now asynchronous; the approval, calculation, version and sandbox-effect guarantees described above remain in force.
