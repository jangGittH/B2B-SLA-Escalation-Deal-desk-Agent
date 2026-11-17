# B2B SLA Escalation/Deal desk Agent

B2B SLA Escalation/Deal desk Agent is a bilingual assistant for customer escalations and commercial deal review. The product follows the [development plan](docs/DEVELOPMENT_PLAN.md), derived from the supplied B2B Enterprise Agent Architecture Guide.

Unit 8 adds durable dispatch, separate workers, recoverable delivery, live progress, and an operations dashboard to both local workflows. Both use persistent checkpoints, independent approve/modify/reject review, exact business calculations and verified sandbox receipts. The interface has five working sections: **Case assistant**, **SLA cases**, **Deal desk**, **Operations**, and **Policies & evidence**.

The implemented SLA loop is intake → account/contract investigation → exact calculation → memo → human approval → sandbox action → verified closure. Missing facts pause the workflow; stale approvals require fresh review. Credits are recorded only in the local sandbox ledger. Technical incident resolution and customer delivery remain separate; no real credit or customer message is sent.

Development order: **Unit 10 completed → Unit 11: conditional real pilot.** Unit 10 adds validated specialist plans, shared budgets, claim verification, a 60-scenario comparison and versioned inference pricing. Unit 9 supplies bounded context, private inspection, hybrid retrieval and governed memory. Evaluation and measurement grow with every increment. The [milestone table](docs/DEVELOPMENT_PLAN.md#7-development-sequence-and-dependency-gates) distinguishes delivered foundations from remaining work.

## Run locally

```bash
uv sync --group dev
docker compose up -d --wait
uv run python -m work_assistant db-init
npm ci --prefix apps/web
npm run build --prefix apps/web
uv run python -m work_assistant demo
```

Open **http://127.0.0.1:8001/**. Copy `.env.example` to `.env` and set `GOOGLE_API_KEY` for conversational model answers and tool selection. Case editing, calculations, memo templates, approvals, sandbox execution and exports work without a model key. `WORK_AGENT_MODEL` optionally selects a model. Saved history and policy search remain readable without a model key.

The local demo profile lets you switch fictional employees to compare permissions. It is not production login. Each server restart prints fresh API bearer tokens, while saved conversations remain bound to employee IDs. Swagger is available at `/docs`.

For a database-free evidence/chat fixture demonstration (persistent cases and workflows require PostgreSQL):

```bash
uv run python -m work_assistant demo --ephemeral
```

The seed contains fictional Acme SLA terms, incident INC-104 evidence, a renewal-discount request, and a restricted credit-review policy. Typed INC-104 inputs deliberately remain incomplete; INC-105 demonstrates a USD 45.00 unexecuted credit proposal from fully specified fictional inputs. Sample policies are development data, not real contract or financial guidance. [Data strategy](docs/DATA_STRATEGY.md).

## Try the SLA workflow

1. As **Mei Chen**, open **SLA cases → INC-105 → Start SLA review**.
2. Switch to **Lin Zhao**, the independent Operations reviewer, and open the same case. Inspect the proposal and memo.
3. Approve, reject with a reason, or enter a reduced percentage, preview its exact amount, and **Modify and approve**. A 50% approval records USD 22.50 for a fresh USD 45.00 proposal.
4. Inspect the verified receipt and saved progress. Refreshing or restarting preserves the workflow. INC-104 instead demonstrates a durable missing-facts pause.

Only the requester and assigned reviewer can read a workflow. Review memos share only evidence both can access; ordinary private memos remain author-only. [Unit 6](docs/UNIT_06.md) explains recovery, expiry, cancellation and sandbox limits.

## Try deal review

1. As **Alex Wang**, open **Deal desk → DEAL-101 → Start deal review**.
2. Switch to **Lin Zhao** and open the same deal. Inspect the review memo, monthly price, margin and contract-term preview.
3. Approve, reject with a reason, or reduce the discount and preview **Modify and approve**. With the fresh demo policy, 10% discount means USD 900.00/month; reducing it to 5% means USD 950.00/month.
4. Inspect the receipt and reload the deal to see sandbox contract version 2 and its new expiry. The source SLA contract, real CRM and customer messages remain unchanged.

DEAL-102 demonstrates an out-of-policy request. Owners can edit requests; Operations can attest missing policy cost with a version check and audit reason. Changed inputs invalidate approval. [Unit 7](docs/UNIT_07.md) explains the implementation and requirement mapping.

## Operate and recover workflows

The demo starts an embedded worker. For independent API and worker processes, use separate terminals:

```bash
uv run python -m work_assistant demo --api-only
uv run python -m work_assistant worker --slots 2
```

Starts and approvals return after a durable database commit; workers advance saved state asynchronously. The UI replays authorized progress after reconnecting. **Operations** shows scoped outcomes, queue status, system versus human wait, and reported model usage. Inference cost uses a dated verified price schedule when model usage is complete; unsupported or expired pricing remains unknown.

[Unit 8](docs/UNIT_08.md) explains limits and recovery. Follow the [operations runbook](docs/OPERATIONS.md) for drain, backup and restore. The [measured results](docs/UNIT_08_RESULTS.md) include a 200-request burst and real process-death/restore checks; these local sandbox measurements are not production capacity or customer ROI.

## Try knowledge and memory

In **Policies & evidence**, upload private TXT/Markdown, DOCX or text PDF evidence. Revisions preserve originals and invalidate stale search results. In **Preferences and memory**, propose and confirm a layout preference; future incident memo exports use it. Editing requires fresh confirmation; deletion, expiry and source changes disable reuse.

Persistent search defaults to BM25. For pinned local multilingual embeddings and reranking:

```bash
uv sync --group dev --extra retrieval
uv run --extra retrieval python scripts/prepare_retrieval.py
WORK_RETRIEVAL_MODE=hybrid uv run --extra retrieval python -m work_assistant demo
```

See [Unit 9](docs/UNIT_09.md) for access rules, limits and API details, and [retrieval results](docs/UNIT_09_RESULTS.md) for the 24-query comparison. Large tool outputs stay in private storage and are inspected through bounded pages.

## Try the assistant

Select **Specialist team** for a validated supervisor plan or **Single assistant** for the simpler comparison mode. Expand the answer trace to inspect role-specific calls, plan dependencies and claim checks. Both preserve independent human approval. See [Unit 10](docs/UNIT_10.md) and its [measured results](docs/UNIT_10_RESULTS.md).

- “Start an SLA review for INC-105 and tell me who needs to approve it.”
- “Calculate the SLA impact for INC-105 and create a Word incident memo. Do not issue a credit.”
- “Investigate Acme INC-104. What is confirmed, and what evidence is missing?”
- “Calculate the price and margin for DEAL-101, then start a deal review for independent approval.”
- “Create a Word incident memo for Acme INC-104 using the evidence I can access. Label unknowns and proposed next steps.”
- “请总结 Acme 的 SLA 响应时限，并列出事件调查尚缺的信息。”

Saved conversations retain bounded prior-turn context. The assistant must read current evidence again for new business claims. Employee changes clear the visible session and load that employee's history.

Private exports support TXT, Markdown, JSON, CSV/TSV, HTML, DOCX, XLSX, PPTX, and PDF. Word supports headings/text; Excel uses literal text cells; PowerPoint uses title/body slides; PDF supports Chinese text. Files persist under `.work_assistant/files` or `WORK_FILES_DIR`. Downloads require the creating employee and current access to the evidence used by a persistent run. Case memo exports use DOCX/PDF/Markdown/JSON and additionally check the saved memo revision and current case inputs. The case editor supports versioned notes and restoring earlier notes as a new revision.

## Implementation guides

- [Unit 1: identities and permission-aware evidence](docs/UNIT_01.md)
- [Unit 2: evidence and document tools](docs/UNIT_02.md)
- [Unit 4: persistent conversations and run records](docs/UNIT_04.md)
- [Unit 5: SLA cases, calculations and private memos](docs/UNIT_05.md)
- [Unit 6: approvals, durable SLA workflow and sandbox closure](docs/UNIT_06.md)
- [Unit 7: deal desk, pricing policy and sandbox contract renewal](docs/UNIT_07.md)
- [Unit 8: durable delivery, progress and operating evidence](docs/UNIT_08.md)
- [Unit 9: bounded context, hybrid knowledge and governed memory](docs/UNIT_09.md)
- [Unit 10: planning, verification and full showcase](docs/UNIT_10.md)
- [Operations runbook](docs/OPERATIONS.md)
- [UI guide and browser checks](docs/WORKSPACE_UI.md)
- [Development plan and requirement mapping](docs/DEVELOPMENT_PLAN.md)

The retired Unit 3 meeting feature, generic workspace placeholder pages, launch-demo corpus, and unrelated benchmark assets have been removed. New workflow development is limited to SLA escalations and deal review.

## Verify

```bash
uv run pytest -q
npm run build --prefix apps/web
# Real PostgreSQL integration tests (creates/drops isolated test databases):
TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run pytest -q
```

See Unit 4 for browser-test setup. `db-init` applies migrations through 0008, initializes the PostgreSQL checkpointer and inserts missing product seed records. Run it before starting the updated app; startup does not migrate the schema. The cleanup migration retires only unchanged legacy seed documents; custom records and historical runs are preserved. Existing unsupported workflow records are not exposed through the current run API. Keep the database and artifact directory together when backing up local state.
