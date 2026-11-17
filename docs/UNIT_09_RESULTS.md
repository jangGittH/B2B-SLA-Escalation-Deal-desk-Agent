# Unit 9 — Verification and retrieval results

Measured locally on November 16, 2026. Synthetic SLA/deal data; no live Gemini calls, real payments, customer messages or production corpus.

## Retrieval comparison

The evaluation uses eight bilingual product fixture documents and 24 fixed query/expected-source pairs kept in `scripts/unit9_retrieval_eval.py`, outside searchable runtime data. Queries cover paraphrases, Chinese/English, cross-display language, exact codes, ticket identifiers, policy references and another tenant. They are held out of index text and model training; this is a small synthetic query set, not a customer/policy-family-held-out production benchmark. Models and thresholds were not tuned against these results.

| Method | Correct top 1 | Recall@3 | MRR@3 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|
| Original bilingual lexical | 18/24 (75.00%) | 100% | 0.8611 | 0.11 | 0.13 |
| BM25Okapi | 21/24 (87.50%) | 100% | 0.9236 | 0.28 | 0.33 |
| BM25 + multilingual dense + RRF + cross-encoder | 23/24 (95.83%) | 100% | 0.9792 | 133.95 | 196.74 |

[Machine-readable results](results/unit9-retrieval.json) include every query/ranking, model commit hashes, corpus/query/implementation hashes, dependency versions, hardware and search configuration. Exact CPU search runs with two Torch threads. Model loading is excluded from timings; authorized fixture chunk encoding and reranking are included. PostgreSQL mode caches embeddings by source/model fingerprint. This is one timed pass, not a latency SLA or a variance study.

The remaining hybrid error is **“Which facts about the outage remain unresolved?”**: private engineer notes rank first, expected incident history second. No claim-level answer correctness is inferred from these rankings. All returned documents are checked for actor authorization. Forbidden candidate text, stale policy/revocation and missing-model fallback are separately exercised by integration tests.

BM25 remains the default for a lightweight installation. The optional hybrid pipeline improves this small suite at substantially higher local latency. Missing optional models are reported as BM25 fallback, and the evaluation explicitly fails rather than labeling fallback results hybrid.

## Application checks

- **217 Python tests**, with real PostgreSQL and isolated databases, including **27 Unit 9 tests**.
- **20 browser scenarios** pass in a fresh complete run (1.1 minutes), including upload/revision/deletion and human memory confirmation/correction/deletion.
- TypeScript checking and Vite production build pass.
- English desktop and Chinese 390px mobile memory screenshots inspected; controls fit without horizontal overflow.
- The existing backup/restore harness restores uploaded original bytes and a confirmed preference into a new database. The original application database is not reset.

Unit 9 checks include private upload/version/history/delete, document/table/PDF extraction limits, old-revision ACL isolation, actual BM25 and mock-encoder forbidden-input assertions, stale candidate exclusion, explicit fallback, private offload expiry/revocation, pre-storage version races, omitted-tail inspection through model tool calls, full fragment pagination of a 100-row case table, input-budget rejection before inference, bounded saved follow-up pointers, memory confirmation/edit/delete/expiry/scope/concurrency/supersession, policy-invalid episode memories, refreshed pending proposals, replay-safe consolidation and changed memo layout with identical factual lines.

## Reproduce

```bash
uv sync --group dev --extra retrieval
docker compose up -d --wait
TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run pytest -q
npm run build --prefix apps/web
uv run --extra retrieval python scripts/prepare_retrieval.py
PYTHONPATH=. uv run --extra retrieval python scripts/unit9_retrieval_eval.py
```

Browser server in one terminal, tests in another (the server creates and removes its own database):

```bash
PYTHONPATH=. UI_TEST_PORT=8013 \
TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar \
  uv run python tests/serve_unit4_ui.py
```

```bash
UI_BASE_URL=http://127.0.0.1:8013 npm run test:e2e --prefix apps/web
```

The tests establish deterministic application boundaries, not the full 60-case showcase, live-model business quality, token billing precision or real business ROI. Exact model-token/TPM enforcement, arbitrary narrative verification, large-corpus ANN performance, scanned-PDF OCR and production retention remain outside Unit 9.
