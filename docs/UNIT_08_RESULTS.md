# Unit 8 — Verified load and recovery results

Measured November 16, 2026 on local synthetic data. [Raw load report](results/unit8-load.json) · [Implementation](UNIT_08.md) · [Operations](OPERATIONS.md).

## Automated checks

| Check | Observed result |
|---|---|
| Full Python suite with real PostgreSQL | 190 passed in 32.66 seconds |
| Browser suite with isolated PostgreSQL and deterministic model | 18 passed in 48.6 seconds |
| Targeted runtime/agent checks after final backup and timing changes | 42 passed in 9.05 seconds |
| TypeScript check and Vite production build | Passed |
| API stopped before dispatch | Previously accepted workflow advances under a separate worker |
| Worker process killed after claim | Expired lease reclaimed; both SLA and deal work advance |
| Worker killed after sandbox effect commit, before checkpoint/ack | Recovered SLA and deal each retain exactly one receipt/effect |
| Delivery generation changed before old acknowledgment | Newly approved work remains pending and is executed |
| Capacity/fairness/dead delivery/provider limits | Explicit rejection, cross-tenant progress, bounded stopped delivery and owner recovery verified |
| Authenticated progress | Cursor replay, bearer header, browser reconnect, cancellation, tenant checks and revoked-actor denial verified |
| Backup/restore | Real `pg_dump`/`pg_restore`, hashes/counts, private file access and restored approval execution passed; source state unchanged |
| Backup failure checks | Existing restore target rejected, tampered archive rejected, missing registered artifact rejected |
| Dashboard | Participant/tenant scope, correlated traces, separate human wait, historical timing exclusion and explicit unknown cost checked |

Screenshots of the Operations page, Chinese mobile layout and live progress were inspected. Browser data and integration databases are isolated from the local demo. Test totals are infrastructure/regression evidence, not a held-out live-model business-quality benchmark.

## 200-request burst

Environment: macOS/Darwin ARM64, 10 logical CPUs, Python 3.13.12, PostgreSQL 17.12 in Docker. One real loopback HTTP API process, 16 submitter threads, 200 distinct synthetic SLA/deal requests across two tenants. Capacity deliberately set to 60 active globally and 40 per tenant. Workers are stopped during intake; then the API is stopped and two independent one-slot worker processes drain accepted work with one lease per tenant.

| Measurement | Observed value |
|---|---:|
| Submitted | 200 |
| Durably accepted (202) | 60 |
| Explicit capacity responses (429) | 140 |
| Unexpected responses | 0 |
| Intake wall time | 0.743 s |
| Intake latency p50 / p95 | 41.94 / 159.91 ms |
| Drain time, including worker process startup | 1.658 s |
| Delivery attempts / workers used | 60 / 2 |
| Queue wait p95 | 2,052 ms |
| Final waiting for approval / input | 16 / 44 |
| Lost accepted workflows | 0 |
| Expected effects / duplicate effects in this intake scenario | 0 / 0 |

No approval is issued in the load scenario, so zero duplicate effects there is not an execution-idempotency test. Separate process-death tests verify one committed effect across replay for both workflows. There are no live model/provider calls in this benchmark. The numbers do not establish 200 simultaneous LLM calls, production capacity, financial execution throughput or customer ROI.

The first load attempt exposed nested database-connection acquisition while holding admission/actor locks. Under contention it exhausted the pool. Reviewer and memo reads were changed to use the existing transaction connection; the reported clean run above then passed. The benchmark fails on any response other than 202/429 rather than treating unexpected errors as acceptable overload.

## Reproduce

Start Docker PostgreSQL and install Python/frontend dependencies per README. From the repository root:

```bash
TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run pytest -q

PYTHONPATH=. TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run python scripts/unit8_load.py

npm run build --prefix apps/web
PYTHONPATH=. TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run python tests/serve_unit4_ui.py
```

While the isolated browser server is running, in another terminal:

```bash
UI_BASE_URL=http://127.0.0.1:8012 npm run test:e2e --prefix apps/web
```

Stop that server with Ctrl-C after testing so its temporary database is removed. The Python suite requires database-create permission for isolated test databases. The restore test uses the local Compose PostgreSQL client tools. Browser checks use installed Chrome or Playwright's Chromium. Run the load script without another test suite running for a less noisy comparison; it writes `.work_assistant/unit8-load.json` and removes its isolated database/processes on completion.

Numeric inference cost remains unmeasured without a verified versioned price schedule. Retrieval quality, full 60-scenario business evaluation, supervisor-versus-baseline comparison and measured pilot outcomes remain later units.
