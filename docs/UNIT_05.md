# Unit 5 — SLA cases, exact calculations and private memos

Unit 5 is implemented. PostgreSQL now stores typed accounts, contracts, immutable policy identifiers, incident tickets, private memo revisions, export links and case audit events. The UI has **SLA cases** alongside the assistant and evidence library. This unit ends at a supported draft and an unexecuted credit proposal; [Unit 6](UNIT_06.md) now adds durable approval, sandbox execution and verified closure.

## Use it

```bash
uv run python -m work_assistant db-init
npm run build --prefix apps/web
uv run python -m work_assistant demo
```

Open <http://127.0.0.1:8001/> → **SLA cases**. The database upgrade is migration `0003`; seeding inserts missing demo rows without resetting edited records. Case inspection, edits, calculations, memo creation and exports work without a model key. Conversational tool selection requires `GOOGLE_API_KEY`. Fixture-only `--ephemeral` mode has no case storage; its case tools return `CASES_UNAVAILABLE`.

- **INC-104** deliberately lacks outage, exclusion and eligibility evidence. Its result lists missing inputs and has no proposed amount.
- **INC-105** demonstrates a fictional signed USD 1,000/month contract: 120 outage minutes minus 30 excluded minutes = 90 eligible minutes. At a 0.0005 fraction of monthly fee per minute, the proposal is **USD 45.00**, below the USD 50.00 monthly cap. No credit has been issued.
- As Mei, edit incident facts, specify a reason and save. The API checks the displayed version. Another Northstar employee can read the tenant's cases but only the current assignee can edit them. Newly created cases are assigned to their creator.
- Save a private memo, edit its hypothesis/next-step/customer-draft fields, restore an earlier revision as a new revision, and export DOCX, PDF, Markdown or JSON. Generated facts and calculations come from records; editable narrative is explicitly unverified. Memos are private to their author, including from other employees in the same tenant.
- Switch to Sam to see the separate tenant. Guessed IDs do not cross tenant boundaries.

Conversation example: “Calculate the SLA impact for INC-105 and create a Word incident memo. Keep the credit and customer response as proposals.” The agent reads current records, computes deterministically, saves a linked memo and exports it. It can cite `case:INC-105` and `memo:<id>` sources, which open through the existing source viewer.

## Policy semantics and explicit limits

The fixture policy is **fictional test policy**, not extracted or inferred from the architecture guide. `sla-minute-v1` implements P1, 24/7 elapsed-time response measurement and UTC calendar-month billing. Currency is USD, CNY or EUR with two-decimal amounts. Unsupported severity, unsigned/expired terms, non-monthly or cross-period windows and unknown inputs require clarification.

The formula is:

```text
eligible_minutes = elapsed_minutes − union(clipped_exclusion_intervals)
raw_credit = monthly_fee × eligible_minutes × credit_rate_per_minute
remaining_cap = max(0, rounded_monthly_cap − confirmed_prior_credit_amount)
proposal = min(round_half_up(raw_credit, 2), remaining_cap)
```

A confirmed ineligible incident produces zero eligible minutes. Unknown eligibility never becomes zero. Exclusions are nullable: `null` means not investigated; `[]` means confirmed none. Duplicate/overlapping exclusion windows count once. Money uses `Decimal`; timestamp durations use integer microseconds, not float arithmetic. A first response exactly at the deadline meets the response target. This response result is separate from credit eligibility.

A credit calculation needs explicit incident start/end, exclusions, eligibility, prior credits and billing period. These incident fields are human attestations with version/audit provenance, not independently verified monitoring or financial records. Known overlapping incidents under the same contract block a proposal until consolidated; this unit does not allocate shared incident credit automatically. Unit 6 now checks the authoritative sandbox ledger before both approval and execution. Policies, accounts and contracts are read-only seed records in this release; changing their schema or signed terms is not delegated to the model.

## API and tools

All endpoints use the existing employee bearer credential. Model arguments cannot supply an employee or tenant. Lists use bounded, parameterized customer/status/text filters; no arbitrary SQL or generic query DSL is exposed.

| Endpoint | Behavior |
|---|---|
| `GET /v1/cases/options` | Authorized accounts/contracts and active assignees |
| `GET /v1/cases?account_id=&status=&query=` | Up to 100 ticket summaries |
| `POST /v1/cases` | Typed intake; creator must be assignee |
| `GET /v1/cases/{id}` | Linked records, current calculation, own memo links |
| `PATCH /v1/cases/{id}` | Replace editable incident fields with `expected_version` and `change_reason` |
| `GET /v1/cases/{id}/events` | Case audit and current employee's private draft/export events |
| `POST /v1/cases/{id}/memos` | Save a template draft with `expected_ticket_version` |
| `GET /v1/memos/{id}` | Own draft, source snapshot, stale flag and revision numbers |
| `PATCH /v1/memos/{id}` | Save notes with expected memo and ticket versions |
| `POST /v1/memos/{id}/restore` | Restore earlier notes as a new revision; retain current source inputs |
| `POST /v1/memos/{id}/exports` | Version-checked DOCX/PDF/MD/JSON rendering |

| Tool | Server risk tier | Result |
|---|---|---|
| `list_sla_cases` | 0 | Authorized bounded summaries |
| `get_sla_case` | 0 | Versioned records, calculation and source |
| `calculate_sla_impact` | 0 | Exact stored-input result or explicit missing inputs |
| `draft_incident_memo` | 1 | Private editable draft, memo ID and source |
| `export_incident_memo` | 1 | Private export with current provenance checks |

The existing evidence tools are Tier 0 and `write_file` is Tier 1. Once typed case evidence has been read, the gateway routes file generation through memo export instead of accepting an untracked case file. Financial/external tool names such as `issue_credit` and `send_customer_response` are blocked at Tier 2; no executor or approval authority exists in Unit 5. Human edits only allow open/investigating states, not credit issuance or verified closure.

An uncited answer after ordinary file creation is now replaced by a server-generated filename acknowledgement. Creating a file cannot justify arbitrary uncited business claims. This does not establish semantic correctness of arbitrary cited prose: a bounded critic and full claim-level verification remain later work.

## Persistence, source checks and measurement

Composite tenant foreign keys connect accounts, contracts, policies and tickets. Transactional case edits increment versions and commit audit records together. Actor checks and short contract locks serialize mutations and overlapping-incident reads. No transaction stays open during model inference or file rendering. Stale edits return 409, hidden records 404, invalid fields 422, and unauthorized assignee edits 403.

Each memo stores exact inputs, row versions, formula version, calculation, author notes, revision, ticket and originating run where applicable. Restoring notes never rolls back someone else's case edits. Exports use the existing renderers, then store memo/version provenance. Changed case, policy, account, contract, overlap state or memo revision invalidates old exports. Persistent answers recheck their source snapshots and disappear from future model context when those sources change. Private drafts remain discoverable in the case after a lost model response.

Conversation tools emit tool name, duration, risk tier and outcome. Case calculation and memo events include case/memo identifiers and versions; the current run links these to existing model usage/duration records. Independent UI edits have durable case audit records even without an LLM run. Inference cost remains unknown until a versioned pricing schedule is implemented. This is operational trace evidence, not a business ROI dashboard or live-model quality measurement.

## Knowledge and original prompt coverage

| Function delivered | Knowledge to use | Requirement contribution |
|---|---|---|
| Typed case/account/contract/policy records | Relational modeling, composite tenant keys, SQLAlchemy, Alembic, Pydantic | R1 backend; R6 business records — F01/F02 |
| Authorized reads and assignee edits | Server-bound identity, row authorization, optimistic concurrency, transactions | R1 permissions/concurrency; R5 control — initial F03 |
| Bounded case tool calls and source reopening | Structured schemas, tool dispatch, durable chat context, source fingerprints | R2 tool execution; R3 task/evidence context — SLA portion F04/F07 |
| Exact SLA calculations and missing-input outcomes | Decimal, timezones, interval unions, caps, money rounding, policy applicability | R1 correctness; R2 deterministic verification; R6 business logic — F07/initial F14 |
| Editable private memos, revision restore and exports | Immutable snapshots, author versus source claims, document rendering, authenticated downloads | R1 persistence; R3 context/provenance; R5 private reversible drafts; R6 memo — F08/F09 |
| Server risk registry and prohibited Tier 2 actions | Least privilege, tool allowlists, policy enforcement | R5 controlled scope — initial F09; approval itself is Unit 6 |
| Calculation/tool/case events and tests | Context propagation, audit transactions, independent numeric assertions, failure injection, browser testing | R4 measurement/evaluation; R1/R5 safety checks; R6 exact outcomes — initial F18–F20 |

These are contributions to R1–R6, not a claim that all six are complete. Dynamic planning, collaborating specialists, memory, real identity/connectors, durable approvals and verified action closure remain in their roadmap units.

## Initial business scenarios

Expected values live in tests, never in searchable runtime evidence. The initial ten scenario groups are implemented, with additional boundary/regression checks:

| # | Scenario | Independently asserted outcome |
|---|---|---|
| 1 | Standard eligible SLA incident | USD 45.00; unchanged contract; no issued credit |
| 2 | Unknown inputs / unsupported policy | Null amount and named missing inputs; unsigned/expired terms rejected |
| 3 | Confirmed ineligible / zero outage | Exact zero proposal |
| 4 | Cap and prior credits | USD 50.00 cap, USD 0.01 remaining, exhausted cap zero |
| 5 | Overlapping exclusions / incidents | Exclusion union counts once; overlapping tickets block proposal |
| 6 | Money rounding and response boundary | Half-cent rounds up; exact deadline meets; one microsecond late breaches |
| 7 | Tenant, assignee, private memo and forged identity | No forbidden read/write/download; model cannot set identity |
| 8 | Stale and concurrent edits / restart / reseed | One edit wins; other conflicts; restart and reseed retain human state |
| 9 | Memo revisions, restore, bilingual exports, stale evidence | New revision preserves prior ones; changed inputs block exports and saved answers |
| 10 | Conversation calculation → memo → export | Correct tools, source IDs, risk/duration/run events and private download; Tier 2 blocked |

Tests: `tests/test_sla_calculator.py`, `tests/test_sla_cases.py`, `apps/web/tests/cases.spec.ts`, plus all prior regression suites. Use the real PostgreSQL and browser commands in [Unit 4](UNIT_04.md#verify). Validation on November 15, 2026: **108 Python tests passed** with real PostgreSQL enabled, **10 browser tests passed**, and the production frontend build passed. English/Chinese PDF samples and desktop/mobile screenshots were visually inspected; DOCX content was structurally checked. Scripted models establish application behavior; live model quality and real business value require separate evaluation.

Implemented next: [Unit 6](UNIT_06.md) — checkpointed escalation workflow, persisted approve/modify/reject, a version-bound idempotent sandbox effect, read-back verification and explicit closure. [Unit 7](UNIT_07.md) now completes deal review; the roadmap moves on to Unit 8 reliability.
