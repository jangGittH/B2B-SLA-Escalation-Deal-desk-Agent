# Unit 10 — measured results

Measured locally on 2026-11-16. The committed reports preserve individual outcomes and limitations. These are fictional sandbox scenarios, not customer outcomes.

## Deterministic 60-scenario comparison

[Full report](results/unit10-showcase.json) records dataset/source hashes, prompt `case-collaboration-v4`, schema/policy versions, dependency versions, environment and every row. The 60-case allocation is 12 SLA, 8 deal, 10 access/adversarial, 8 context/memory, 10 approval/concurrency, 8 recovery and 4 files.

| Strategy | Expected safe outcome | Model calls | Tool calls | p50 / p95 elapsed |
|---|---:|---:|---:|---:|
| Fixed workflow | 60/60 | 0 | 0 | 133 / 209 ms |
| Single worker + critic | 60/60 | 235 | 115 | 263 / 369 ms |
| Supervisor + specialists + critic | 60/60 | 463 | 172 | 452 / 680 ms |

Latency covers scenario body and state assertions, excluding database fixture setup; it is one timed pass with a deterministic provider. Inference cost is unknown/not applicable to this provider double, not imputed as zero. The fixed strategy directly calls typed business services; its zero tool/model counters do not imply zero computation or operating cost.

A pass means the scenario's independently asserted expected state: completion, waiting, denial, rejection or a safe partial outcome. It does not mean every scenario completes a business action. Human approvals, injected faults and shared memory/file probes are driven by the harness. The fixed baseline has no narrative generation or provider-fault exposure. These results establish control correctness and demonstrate collaboration overhead; they do not establish a quality advantage for the supervisor.

## Repeated live-provider smoke evaluation

[Final live report](results/unit10-live.json): `gemini-3.5-flash-lite`, prompt v4, four read-only questions × two modes × two repetitions. Cases cover English/Chinese response-versus-resolution, INC-105 proposed credit and DEAL-101 proposed renewal price. Assertions independently require the expected source/number and no workflow or financial effect.

| Mode | Passed | Model calls | p50 / maximum elapsed | Sample latency standard deviation |
|---|---:|---:|---:|---:|
| Single | 8/8 | 26 | 5,964 / 8,749 ms | 1,389 ms |
| Supervisor | 7/8 | 51 | 11,853 / 19,698 ms | 4,915 ms |

Total: **15/16**, 77 provider calls, **USD 0.06309184 estimated inference cost**, all usage known, under the USD 0.25 per-invocation cap. Median uses the report's upper-middle convention. Two repetitions and four fixed questions are too small for a general quality or latency claim.

The failed supervisor DEAL-101 attempt supplied a non-exact evidence quotation. Deterministic provenance checks rejected it through two repair attempts; the run stopped with `budget_exceeded` and no business effects. This is a remaining availability limitation, not a successful answer. We retain the stop rather than weakening quote validation or expanding repair loops.

Development runs are retained: [v1](results/unit10-live-initial.json) 5/16, [v2](results/unit10-live-v2.json) 5/8 and [v3](results/unit10-live-v3.json) 6/8. They exposed numeric display/ID false positives, unclear worker JSON contracts, search-preview misuse and skipped independent analyst calculations. Those feedback paths and prompts were corrected. These runs are development feedback, not held-out evaluation. A 60-case live-model study and human-labelled critic calibration remain future pilot work.

## Regression and UI checks

- **413 Python tests passed**, including all 180 showcase rows and 16 collaboration-specific tests, with real PostgreSQL.
- Production TypeScript/Vite build passed.
- **21 browser tests passed** on the structured-provider test server, including plan persistence, mode switching, bilingual views and mobile layout. The planning screenshot was also checked visually after waiting for the settled layout.
- Targeted behavior includes role escalation denial, owned plan inspection, real overlap of independent read calls, source-change replanning with preserved unaffected work, shared-budget stops, citation-without-read repair, false claims using real citations, exact pricing and expiry/unknown-price behavior.

Existing business workflow tests continue to verify independent approval, stale input conflicts, idempotent effects and receipt read-back. No live provider test exercised a real connector or customer message.

## Reproduce

```bash
uv sync --group dev
docker compose up -d --wait
export TEST_DATABASE_URL=postgresql+psycopg://northstar:northstar-local@127.0.0.1:55432/northstar
uv run pytest -q
uv run python scripts/unit10_eval.py
npm run build --prefix apps/web
# Optional paid live smoke; GOOGLE_API_KEY must be configured.
uv run python scripts/unit10_live_eval.py --cases 4 --repeats 2 --max-cost-usd 0.25
```

Both evaluation scripts create and drop isolated databases. The live script requires a currently valid price schedule and stops if cost/usage cannot be bounded. Timed-out calls retain a conservative reservation. It does not log credentials. Start a fresh structured-provider UI server for each complete browser suite; workflow tests intentionally mutate shared fixtures:

```bash
PYTHONPATH=. UI_STRUCTURED_MODEL=1 UI_TEST_PORT=8013 uv run python tests/serve_unit4_ui.py
# Another terminal:
UI_BASE_URL=http://127.0.0.1:8013 UI_STRUCTURED_MODEL=1 npm run test:e2e --prefix apps/web
```

The [official Gemini pricing page](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite), checked 2026-11-16, supplies the versioned paid standard text rates: USD 0.30/million input, 0.03/million cached input and 2.50/million output including thinking. Estimates exclude free-tier effects, infrastructure and cache storage; the schedule expires at 2026-12-01 00:00 UTC. Unknown usage or prices remain unknown. No actual billing, manual baseline, customer SLA compliance, resolution cost or ROI is claimed.

## Saved-history loading fix — 2026-11-17

A manual demo exposed repeated evidence-read audit entries copied into follow-up runs while validating prior answers. Reopening that existing conversation repeatedly revalidated duplicate fingerprints and fetched its entire event history, keeping the composer busy. Validation now suppresses new read events, deduplicates identical fingerprints while retaining distinct versions, and loads conversation summaries without full audit events. Original audit records remain available through the owned run endpoint.

History loading now has a separate UI state and 15-second timeout. Users can start a new conversation while an old one loads; a late history response cannot replace the new turn. Verification: 66 targeted Python tests, 9 browser tests and the production build passed. The existing demo conversation reopened in about four seconds, and an actual live-model send returned a sourced answer and unlocked the composer. These timings are smoke observations, not a capacity benchmark.

## Conversational routing fix — 2026-11-17

The message “can you understan what i m saying?” previously exhausted shared repair attempts because an ordinary conversational reply was treated as an uncited business assertion. Prompt v6 adds model-based conversational intake and an independent guard; workers also receive bounded recent history. New regressions cover English/Chinese conversation in both modes, clarification, rejected business assertions masquerading as chat, follow-up history and accurate repair-limit messages.

A live provider smoke covered greetings, the exact reported message, Chinese understanding, mode explanations and a contextual Acme response-target follow-up. All five returned answers; the four conversational turns used no tools, and the business follow-up used sources and passed claim verification. After tightening the conversational prompt, an actual browser check sent a greeting and the exact reported message and reloaded both saved answers successfully. These are smoke observations, not a general quality benchmark; the existing v4 cost/latency comparison remains historical.

Regression verification for this fix: the first full run had 420 passes and four malformed-intake recovery failures. After adding bounded intake schema correction, all four failing cases and the two corresponding fixed-baseline cases passed. The final combined conversation/persistence/recovery selection passed 43 tests (174 showcase cases deselected). The live browser check also passed; a second full-suite run was not performed.

## Case-count repair — 2026-11-17

“how many cases do we have now” failed before tool use because the planner assigned a tool to the wrong role and then omitted a required source-reading capability. The fix adds an exact, citable tenant aggregate, tool descriptions and aggregate-specific role guidance. A live check then exposed an insufficient one-turn worker budget; validation now rejects that plan and prompts explain the turn accounting.

Three additional regression cases check organization isolation, forged identity rejection, counts beyond 100 records, changed-count fingerprints and both execution modes. All 29 collaboration tests pass. The related agent/history/context regression run had 63 passes and one expected tool-inventory mismatch; the inventory assertion was updated for the new tool and rerun. Live model checks in both modes returned 2 SLA cases and 2 deal requests (4 combined), with one read-only aggregate tool call each.

## Follow-up coherence repair — 2026-11-17

The reported sequence offered existing deal details, received “yea,” repeated confirmation, then failed after listing because the node lacked a permitted detail tool. Intake now resolves contextual intent for planner, worker and repair; list plans require detail tools, and unfiltered list results are citable with source freshness checks. The history slice includes six bounded turns and retains the end of long answers. Exact fraction-to-percent conversion avoids rejecting ordinary discount display.

Validation: 69 agent/persistence/collaboration tests passed before the percentage addition; 27 context/knowledge/memory tests passed; the final collaboration selection passes 33 tests including accepted offers in both modes, repair-stage context, organization-scoped listing freshness and numeric conversion rejection cases. An initial repeated live probe exposed loss of resolved context during repair, which was corrected. The final repeated live probe answered four of four acceptance attempts across both modes and made zero tool calls on a decline. One acceptance returned a brief listing rather than full detail; the other three read individual deal records. The actual browser sequence also continued after “yea,” read both deals, returned their terms/policy state and survived reload. These small checks do not establish universal conversational reliability; response detail and latency still vary with model output.
