# Unit 4 — persistent case conversations and run records

PostgreSQL stores identity/workspace evidence, conversations, user turns, run requests/results, artifact metadata, source references, and execution events. These are the retained foundation for SLA Escalation / Deal Desk workflows.

The earlier meeting-specific graph and recovery UI have been removed following the product scope decision. Saved chat remains available. [Unit 6](UNIT_06.md) now adds SLA business checkpoints and approval resume on top of this conversation foundation.

## Setup

```bash
uv sync --group dev
docker compose up -d --wait
uv run python -m work_assistant db-init
npm ci --prefix apps/web
npm run build --prefix apps/web
uv run python -m work_assistant demo
```

Open <http://127.0.0.1:8001/>. `GOOGLE_API_KEY` enables generation. `WORK_DATABASE_URL` configures PostgreSQL; the Compose database uses loopback port 55432 and a persistent named volume. Its credentials are local development defaults. `WORK_FILES_DIR` controls artifact bytes.

`db-init` applies Alembic migrations and inserts missing product seed records without overwriting customized rows or permissions. Migration 0002 removes only unchanged legacy seed documents and their unused grouping records. User-edited evidence and historical execution records remain stored. The conversation run API exposes chat runs; Unit 6 adds a separate `/v1/sla-workflows` API. Legacy workflow IDs are not resumed. The new graph uses a distinct `northstar-sla-v1:` checkpoint namespace with pickle fallback disabled.

`demo --ephemeral` uses fixtures without saved history. The CLI `search` and `ask` commands are local debugging tools. Server tokens change on restart; persistent ownership uses stable employee IDs.

## User behavior

Send a question, then a follow-up in the same conversation. Reload the page to restore the latest conversation or choose an older one from **Saved conversations**. **New conversation** creates a separate context. Switching employees clears the current view and loads that employee's private history.

The model receives at most six usable previous question/answer pairs, bounded to 24,000 characters. These are context, not authoritative evidence: sources must be read again for new business claims. Changed or revoked source evidence hides affected saved results and blocks linked downloads. A follow-up clarification is a new run in the same conversation.

Interrupted chat runs are not replayed automatically because a tool may already have created a file. Review the saved run/files before sending a new turn. A repeated submission key returns its existing run; a new key explicitly creates another execution. A full crash-safe, idempotent action ledger remains planned.

## API

| Route | Purpose |
|---|---|
| `POST /v1/conversations` | Create `{title, language}` |
| `GET /v1/conversations` | List up to 100 recent owned conversations |
| `GET /v1/conversations/{id}` | Read owned conversation and validated saved turns |
| `POST /v1/conversations/{id}/messages` | Submit `{question, language, request_key}` |
| `GET /v1/runs?kind=chat` | List current employee's chat runs |
| `GET /v1/runs/{id}` | Read chat status, result, files, and events |
| `GET /v1/files/{id}` | Download a private artifact after current evidence checks |

All resource routes enforce employee and organization ownership. Resource UUIDs are not authorization. The API's `request_key` is unique per employee; reuse with different content returns 409. An active turn in the same conversation also returns 409. Submission is serialized in short database transactions and no transaction is held during a provider call.

Requests still wait for the current attempt over HTTP; there is no worker queue or SSE stream yet. Per-process model capacity remains bounded. Timeouts persist an interrupted result. An abandoned lease expires after the request deadline plus 30 seconds; later reads report interruption. Fenced completion prevents an older attempt from overwriting another owner of the run.

## Code and knowledge

| Module | What to learn |
|---|---|
| `db/models.py`, `migrations/` | Relational identifiers, constraints, schema evolution |
| `db/store.py` | SQLAlchemy transactions, idempotent seeding, row locks, leases and completion fences |
| `repository.py`, `knowledge.py` | Interchangeable storage and permission-aware evidence access |
| `durable_api.py` | Resource ownership, duplicate submissions, bounded authorized history |
| `telemetry.py`, `agent.py` | Run context, source fingerprints, model duration and available token usage |
| `files.py` | Private artifacts with durable metadata and filesystem bytes |
| `apps/web/src/main.tsx` | Saved history and session isolation |

The `projects` table groups customer evidence. Unit 5 adds separate typed account, contract, SLA-policy, incident-ticket and memo tables; see [Unit 5](UNIT_05.md). Workspace documents and identities retain validated bilingual JSON payloads with relational identity/organization keys.

Model usage is stored when the provider reports it. Missing usage and cost are null, not zero; versioned pricing and full OpenTelemetry/dashboard integration remain future work. Events contain safe source fingerprints and outcomes, not hidden reasoning or provider credentials. Back up PostgreSQL and the artifact directory together.

## Verify

```bash
uv run pytest -q
# Requires permission to create/drop uniquely named test databases:
TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run pytest -q
npm run build --prefix apps/web
```

Without `TEST_DATABASE_URL`, database integration tests are skipped. They never reset the configured application database.

For deterministic browser tests, run these in separate terminals:

```bash
PYTHONPATH=. TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run python tests/serve_unit4_ui.py
```

```bash
UI_BASE_URL=http://127.0.0.1:8012 npm run test:e2e --prefix apps/web
```

Stop the test server with Ctrl+C to remove its temporary database. Scripted models test application behavior without consuming live inference budget.

Requirement contributions: R1 database/authorization/concurrency; R3 saved context; R4 run events and usage. [Unit 6](UNIT_06.md) adds fixed-workflow recovery, independent action approval and verified sandbox closure. Dynamic planning, production integration and measured business outcomes remain later increments.
