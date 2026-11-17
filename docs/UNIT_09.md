# Unit 9 — Context, knowledge and governed memory

Completed November 16, 2026. Builds on the two sandbox workflows and durable workers from [Units 6](UNIT_06.md), [7](UNIT_07.md) and [8](UNIT_08.md). [Development plan](DEVELOPMENT_PLAN.md) · [Measured results](UNIT_09_RESULTS.md).

## Delivered behavior

| Capability | Implementation | Requirement contribution |
|---|---|---|
| Bounded task state | Persisted goal, case/deal identifiers, recent tool outcomes, evidence/version fingerprints, offload references, unresolved clarification, confirmed memory references and remaining budgets. Follow-ups carry authorized pointers and summaries. | F15; R2/R3 |
| Large-result inspection | Tool output over 12,000 serialized UTF-8 bytes is stored privately in PostgreSQL; the model gets a bounded preview and a reference. Validated fragment pagination and literal filtering recheck owner, tenant, expiry and current source fingerprints. | F15; R1/R3/R5 |
| Versioned ingestion | TXT/Markdown, DOCX paragraphs/table rows and text PDF pages; original bytes/checksum, revisions, language and ACL retained. Nested heading paths and revision-bound chunks. | F16; R1/R3/R6 |
| Hybrid retrieval | Actual BM25, multilingual dense exact search, reciprocal rank fusion and a local cross-encoder; authorization before candidate encoding/reranking, and freshness checks before returning results. | F16; R1/R3/R4 |
| Governed memory | Pending proposals, explicit human confirmation, edit/delete, expiry, source fingerprints, scope, attribution, last-use and supersession. Confirmed layout preferences change subsequent incident memo exports. | F17; R3/R5/R6 |
| Evaluation | Fixed synthetic retrieval comparison, database access/revocation/race tests, large-table and model-loop budgets, restored originals/memory, and bilingual browser checks. | F03/F14/F19; R1/R4/R5 |

Migration `0007_context_knowledge_memory` adds knowledge originals/revisions, chunks/embeddings, private offloads, task contexts, memories and memory audit events. It leaves workflow approval and action tables intact. Unit 10 still owns dynamic plans, specialists and semantic claim verification.

## Run and use

```bash
uv sync --group dev
uv run python -m work_assistant db-init
npm run build --prefix apps/web
uv run python -m work_assistant demo
```

Open **Policies & evidence → Upload or revise evidence**. Upload a private document, search it, and open its current source. To revise/delete it after reloading, use the upload ID and current version. Only Operations can publish organization/team evidence. Uploads do not modify signed policies, deterministic calculation inputs or approvals.

Open **Preferences and memory**, choose a layout and propose it. Confirm it to activate; editing makes it pending again. An `evidence_first` preference moves the provenance section before recorded incident facts in subsequent memo exports without changing facts, arithmetic or approval. Delete removes the memory's stored value/source pointers. User defaults override shared defaults. Shared defaults require an Operations reviewer who can see the scope and source.

The model can propose either supported presentation preference; it cannot confirm one. The worker periodically examines completed chat runs and proposes verified sandbox workflow episodes. It never promotes them automatically or infers a root cause. Replayed consolidation deduplicates proposals and records completion. Episodes describe historical sandbox outcomes only.

## Retrieval choice and measured spike

Default persistent search is **BM25**; fixture mode retains the original lexical baseline. Hybrid search is opt-in because it requires larger local model dependencies and weights:

```bash
uv sync --group dev --extra retrieval
uv run --extra retrieval python scripts/prepare_retrieval.py
WORK_RETRIEVAL_MODE=hybrid uv run --extra retrieval python -m work_assistant demo
# Reproduce the independent query comparison:
PYTHONPATH=. uv run --extra retrieval python scripts/unit9_retrieval_eval.py
```

The preparation script downloads pinned model files. Application inference loads local files only with remote code disabled; the retrieval models receive no network-uploaded application documents. Pins are in `retrieval/models.json`; library versions are locked in `uv.lock`.

The selected pipeline uses `rank-bm25`'s BM25Okapi, `paraphrase-multilingual-MiniLM-L12-v2` embeddings and `mmarco-mMiniLMv2-L12-H384-v1` reranking. The upstream [BM25 implementation](https://github.com/dorianbrown/rank_bm25), [retrieve/rerank documentation](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html), [embedding model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) and [reranker model card](https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1) informed this local choice.

Both branches operate only over currently authorized chunks. Top 40 candidates per branch are fused with RRF constant 60; at most 30 go to the cross-encoder. Exact ticket/code matching takes precedence in the returned ordering. The final result rechecks identity, ACL and source fingerprint. Embeddings are cached in PostgreSQL JSON against document fingerprint and model revision. Changed/deleted uploads invalidate their chunks transactionally; search also reconciles lazily after a crash. No approximate index is used: exact CPU similarity over at most 4,000 authorized chunks is the deliberate small-corpus baseline. pgvector/ANN remains a scaling option, not a completion claim.

Missing optional dependencies/weights explicitly return method `bm25_models_unavailable`. Evaluation refuses to count this fallback as hybrid. Raw ranking scores are not confidence probabilities. The 24-query comparison improved top-one accuracy from 75% lexical to 87.5% BM25 and 95.83% hybrid; the latency tradeoff and remaining error are recorded in [results](UNIT_09_RESULTS.md).

## Context and inspection contracts

- Task state is bounded to 24,000 serialized UTF-8 bytes. Older carried context and lists are trimmed before current goal/budget. No hidden reasoning is stored.
- The agent checks serialized conversation input before each provider call (default 262,144 bytes, configurable on `KnowledgeAgent`). `context_budget` events record measured size and the limit. These are byte budgets, **not exact model-token estimates or TPM enforcement**. Provider-reported usage remains the token measurement.
- Tool responses above 12,000 bytes are offloaded, up to a 2 MB storage cap. Documents and SLA/deal lists have explicit validation guards. Unsupported oversized result types stop with a structured error rather than entering the model context.
- Offloads expire after 24 hours. Inspection returns at most eight 400-character fragments, a cursor, row count and a partial-view notice. Literal filters are limited to 100 characters. Cursor and filter describe a fragment view; samples never support whole-table financial totals. Use the existing deterministic case/deal tools for arithmetic.
- Stored payloads are checked against the exact version read before storage. Each page rechecks the owner, active identity, tenant, expiry and all guards. Lists invalidate if the corresponding authorized query result changes; document revocation/version changes deny old references.
- Prior chat state is loaded only after current evidence checks. A summary, memory or inspection reference grants no new access. Current source reads and existing approval invalidation remain mandatory.

Expired offload bytes remain in backups/database until an operator retention policy is added. Memory deletion clears its live value and sources, while minimal audit metadata and already-generated historical artifacts remain; it is not erasure of backups or past conversation messages.

## Memory controls

User/team/tenant scopes use current membership and tenant checks. Personal memories require their owner; shared confirmation/edit/delete requires Operations and visibility. Team conflicts with equally ranked different values are omitted. Confirming a replacement supersedes active values in the same scope/key, increments their versions and records audit events. A personal preference outranks a team preference, which outranks a tenant default.

Only two layout values and completed verified sandbox episodes are supported; arbitrary prose cannot become policy memory. Defaults expire in 90 days (API supports 1–365). Recall uses scope priority, lexical relevance, confidence and recency, with hard expiry rather than learned decay. Stale or inaccessible episode content and source IDs are withheld. Editing preferences returns them to review; source-invalid episodes require a fresh proposal. Revision checks and transactional locking prevent concurrent confirmations from leaving competing active values in one scope.

## API and tools

All routes require the existing bearer identity; model arguments cannot supply an actor or tenant.

| Surface | Purpose |
|---|---|
| `POST /v1/knowledge/uploads` | New private/shared evidence with original bytes |
| `PUT /v1/knowledge/uploads/{id}` | Expected-version revision and optional ACL change |
| `GET /v1/knowledge/uploads/{id}/revisions` | Currently authorized historical metadata |
| `GET /v1/knowledge/uploads/{id}/revisions/{version}/original` | Original download; both old and current ACL must permit it |
| `DELETE /v1/knowledge/uploads/{id}?expected_version=N` | Authorized deletion and index invalidation |
| `GET/POST /v1/memories` | Inspect visible memories / propose a pending memory |
| `PATCH /v1/memories/{id}` | Expected-version confirm, edit or delete |
| `GET /v1/context-artifacts/{ref}` | Private fragment inspection with `cursor`, `limit`, `contains` |
| `GET /v1/runs/{id}/context` | Owned, still-authorized bounded task state |
| `inspect_artifact` tool | Same guarded fragment read, including omitted evidence |
| `propose_memory` tool | Pending presentation preference only |

Uploads allow 2 MB original bytes, 100,000 extracted characters and at most 50 unencrypted PDF pages. Scanned PDF OCR, rich document layout preservation and external corpus ingestion are outside this unit. DOCX tables retain row text and cell separators, not spreadsheet formulas/types. Originals preserve the source file. There is no automatic translation.

## Verification and next unit

See [Unit 9 results](UNIT_09_RESULTS.md) for reproducible checks and limitations. The new tests exercise omitted-tail inspection through the actual model tool loop, paginated reconstruction of a 100-row case table, pre-storage version races, policy-stale memories, scope/expiry/concurrency, input budget rejection before inference, saved follow-up pointers and database restore.

Next is Unit 10: validated dynamic planning, role-specific workers, bounded repair, claim verification and the full scenario evaluation. The current workflows remain fixed graphs, and no synthetic result establishes production retrieval quality or business ROI.
