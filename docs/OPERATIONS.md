# Local operations and recovery

Unit 8 supports a local PostgreSQL-backed SLA/deal workflow service. This is a sandbox runbook, not a production identity, deployment or financial integration. [Runtime design](UNIT_08.md) · [Verification results](UNIT_08_RESULTS.md).

## Install, migrate and start

From the repository root:

```bash
uv sync --group dev
docker compose up -d --wait
uv run python -m work_assistant db-init
npm ci --prefix apps/web
npm run build --prefix apps/web
uv run python -m work_assistant demo
```

Set `WORK_DATABASE_URL` in `.env` for the intended database. `db-init` applies migrations through 0006, initializes the checkpointer and seeds only missing fixtures. Startup does not apply migrations. Do not delete the database volume to upgrade. Keep `.env` out of version control; demo bearer tokens are printed by the launcher and its log must stay private. `GOOGLE_API_KEY` is needed only for model-backed conversation answers, not the deterministic case/review UI.

By default `demo` runs an embedded worker. For separate processes, use separate terminals with the same database configuration:

```bash
uv run python -m work_assistant demo --api-only
uv run python -m work_assistant worker --slots 2
# Optional second process; central slot/tenant limits still apply:
uv run python -m work_assistant worker --slots 2
```

Keep one shared artifact directory for API processes (`WORK_FILES_DIR`, default `.work_assistant/files`). Worker processes currently operate on database-backed memos, approvals and sandbox receipts. The queue is PostgreSQL-backed; no Redis service is required.

## Health and routine inspection

- `GET /health/live`: API process is alive.
- `GET /health/ready`: database and Unit 8 runtime schema are accessible. A ready API may accept durable work even with no running workers.
- **Operations**: current queue states, oldest pending age, recent worker heartbeat, dead deliveries, expired reviews, cohort outcomes and timing. A worker heartbeat is healthy if seen within 30 seconds; it is global infrastructure status, not a guarantee that a specific tenant's job progressed.
- Workflow panel: persisted progress, current approval, delivery failure and exact receipt. Queued/approved is not equivalent to executed.
- Authentication: production login is not implemented. Do not expose the demo profile/token endpoints as a public service.

## Drain, restart and recover

Stop the API with Ctrl-C (or SIGTERM to its Python process) to stop new submissions. Send SIGTERM/Ctrl-C to each independent worker. Workers stop claiming new jobs and join their in-flight work before closing the database. An embedded worker drains during API shutdown. Avoid SIGKILL for normal maintenance.

If a process dies abruptly, its delivery lease expires and a running worker's reconciler reclaims it. Default lease is 15 seconds, with reconciliation approximately every second when its scheduling slot is free. Repeated crashes can exhaust the five-delivery budget. SQL outage recovery resumes once connectivity returns; unacknowledged work remains in PostgreSQL.

For a stopped/dead delivery:

1. Inspect the workflow's saved state, failure and receipt. Fix the underlying storage/service problem first.
2. As the requester, use the workflow's **Recover / resume** action. This creates a fresh delivery generation under the same saved workflow.
3. Observe progress. Expired, changed or revoked approvals still require valid fresh review. Recovery cannot authorize an action or overwrite an existing effect.
4. If a receipt already exists, use its saved outcome and verification; do not create a new workflow merely to repeat the action.

A partial outcome records a committed effect whose remaining verification/closure failed. Cancellation does not reverse it. There is no automatic real-provider compensation. Existing workflow retry and revision limits remain in effect independently of queue retries.

Provider admission returns 429 with Retry-After. Wait for capacity/the request window; do not blindly replay a whole chat interaction whose tools may already have accepted a workflow. Request keys deduplicate workflow starts. Model SDK retries are disabled.

## Central limits

Defaults are in [Unit 8](UNIT_08.md#defaults-and-bounded-behavior). One `runtime_settings` row controls all processes. To change limits, validate the entire merged payload with `Limits`, rather than editing unvalidated JSON. For example, run from the repository root:

```bash
uv run python - <<'PY'
from sqlalchemy import select, update
from work_assistant.db.store import Database, database_url
from work_assistant.runtime.tables import settings
from work_assistant.runtime.queue import Limits

db = Database(database_url())
try:
    with db.engine.begin() as conn:
        old = conn.execute(select(settings.c.payload).where(settings.c.id == 1).with_for_update()).scalar_one()
        new = Limits.model_validate(old | {'workers_total': 4, 'workers_per_tenant': 1})
        conn.execute(update(settings).where(settings.c.id == 1).values(payload=new.model_dump()))
finally:
    db.close()
PY
```

Coordinate configuration changes during a quiet/drained period. Lowering capacity does not cancel accepted workflows or preempt active leases; it blocks further admissions until usage falls. Keep provider limits within the actual account quota. The input-byte/output-token bounds do not implement exact provider token-per-minute accounting.

## Consistent backup

Stop **all API and worker processes that write the selected database or artifacts** and wait for them to exit. `--quiesced` is an explicit operator assertion; the command cannot prove other writers are stopped. Do not use it as a promise that an online database dump and a changing directory form a consistent snapshot.

Use a new backup path outside the artifact tree:

```bash
uv run python -m work_assistant backup .work_assistant/backups/unit8-drill \
  --files .work_assistant/files --quiesced --docker
```

Use the actual `WORK_FILES_DIR` if customized. Omitting `--files` honors that setting. The backup rejects a missing registered artifact, overlapping directories and symlink children. It creates:

- `database.dump`: custom-format PostgreSQL archive, including business records, approvals, queue, events and checkpoints.
- `files/`: all private artifact bytes and sidecar metadata.
- `manifest.json`: format/date, selected table counts and SHA-256 hashes for every archived file.

The destination is new-only with directory mode 0700 and file mode 0600. Credentials are passed through environment variables, not subprocess command-line arguments or printed results. `--docker` uses the Compose `postgres` container's tools against its own PostgreSQL service; use it only for this local Compose database. Omit it for another database and install compatible `pg_dump`/`pg_restore` clients. The verified local server is PostgreSQL 17; the host's PostgreSQL 14 dump client is not suitable for that server.

See official [pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html) and [pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html) documentation. Bundles contain private application data. Off-site encrypted storage and automatic retention are not provided by this local command.

## Restore drill into a fresh target

A restore never overwrites/drops the current database. Choose a new database name beginning `northstar_restore_` and a nonexistent artifact directory:

```bash
uv run python -m work_assistant restore .work_assistant/backups/unit8-drill \
  --name northstar_restore_unit8_drill \
  --files .work_assistant/restored-unit8-files --docker
```

The command validates archive/file hashes, creates a new database, restores in one transaction, copies artifacts and checks selected table counts plus registered files and copied bytes. Existing database names or file targets fail. Hash failure stops before database creation. A later restore failure may leave a newly created diagnostic database/directory; inspect it and choose a fresh target for another attempt. The source database and backup are not overwritten.

To inspect the restored state, start a separate local demo pointed at the restored database and artifact directory. For the standard Compose credentials:

```bash
WORK_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar_restore_unit8_drill \
WORK_FILES_DIR=.work_assistant/restored-unit8-files \
  uv run python -m work_assistant demo --port 8002
```

Check readiness, private history/file access, pending approvals and receipts. In a disposable drill, approve a pending review as its designated reviewer and verify exactly one sandbox receipt. Confirm the source database stayed unchanged. The automated integration test performs this sequence and denies another employee's artifact read. Do not switch an operational service to a restored snapshot without accounting for writes after the backup; local receipt idempotency cannot reconcile unseen external effects.

## Release and rollback procedure

Before upgrading, stop writers and take a matched database/artifact backup; retain the previous code revision. Apply migrations, rebuild the frontend, start API/workers, check readiness and inspect a saved workflow. Unit 8 migration preserves existing workflow records; their timing remains unknown.

If the new app fails, stop it first. Prefer a forward fix against the preserved migrated database. Do not downgrade/drop runtime tables while accepted work may exist. A full rollback uses the prior code and its matched backup restored into fresh database/artifact targets, with an explicit decision about any newer writes. Keep failed targets intact until recovery is verified.

## Retention and present limits

Provider admission rows older than a day are pruned during new admission. Business audits, progress events, attempts, worker registrations and backups have no automatic retention job yet. Monitor disk growth; do not manually delete pending jobs, approvals, receipts or checkpoint state. Add retention and on-call alerts as reviewed production work.

The delivered reports cover deterministic local workflows. Production identity, external billing reconciliation, OTel export, exact provider TPM limits, live-model quality evaluation, real connector reconciliation, automatic off-site backups and real business baselines remain future milestones.

## Unit 9 knowledge and memory operations

Apply migration 0007 before running Unit 9 code. Original upload bytes, embeddings, task context, offloads and memories are PostgreSQL records and are included in the database archive. Existing private export files still require the paired artifact backup. Originals and memory restore are covered by the Unit 9 integration drill.

`WORK_RETRIEVAL_MODE=bm25` is the default. For hybrid search install the `retrieval` extra and prepare the pinned weights with `scripts/prepare_retrieval.py` before starting the API. Runtime retrieval is local-only; missing models report `bm25_models_unavailable`. Weights are replaceable deployment assets, not private business data in the backup. Recreate them from `retrieval/models.json`. The exact local adapter rejects more than 4,000 authorized chunks; plan a measured index upgrade for larger corpora.

Offload reads expire after 24 hours, but expired payloads have no automatic deletion job. Memory expiry disables recall; deletion clears the live value and source references while retaining audit metadata. Neither removes historical conversation/export content or backups. Include these tables in a reviewed retention policy before a pilot. See [Unit 9](UNIT_09.md) for API controls and [results](UNIT_09_RESULTS.md) for reproduction.

## Unit 10 plans, budgets and pricing

Apply migration 0008 before starting the updated API/worker. `agent_plans` and `agent_steps` are included in PostgreSQL backups. Saved plans can be inspected through the owned run endpoint; an interrupted specialist session is not resumed from an individual node. Inspect existing private artifacts and accepted business workflows before retrying a stopped action. Business workflow receipts and checkpoints remain authoritative.

Default collaboration limits are in `agents/budget.py`. All specialists share the same call, input, deadline, repair and replan counters. Provider admission is still distributed. Do not increase model concurrency without repeating Unit 8 capacity checks. A stopped or uncertain action is not evidence of failure to commit; reconcile saved state first.

The checked-in standard paid text price schedule was verified on 2026-11-16 and expires at 2026-12-01 00:00 UTC. Refresh `runtime/prices.json` from the linked official provider page before extending validity. Unknown model IDs, unsupported token categories, missing usage and expired prices remain unknown. Cost estimates include reported cached and thinking tokens, exclude non-inference expenses, and are not provider invoices. Operations displays a full estimate only when every included call is known.

Reproduce Unit 10 with [the results guide](UNIT_10_RESULTS.md). Live evaluation uses isolated fictional databases and a shared per-invocation estimated cost cap. Keep credentials in environment configuration; reports contain synthetic responses and audit metadata only. Production traces still require access-aware retention and review.
