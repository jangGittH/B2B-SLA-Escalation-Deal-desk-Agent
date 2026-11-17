# Unit 10 — dynamic planning and verified collaboration

Unit 10 adds a request-bound supervisor and Knowledge, Analyst, Action and Critic specialists. The existing durable SLA/deal graphs still own review, independent approval, execution and receipt verification. Planning cannot approve credits or contract changes.

## Try it

Apply migration `0008_agent_plans` with `uv run python -m work_assistant db-init`, build the web app, then start the demo as described in the README. In **Case assistant**, select **Specialist team** (default) or **Single assistant**. Both modes use the same evidence, approval and mandatory critic boundaries.

- As Mei: “Calculate the proposed SLA credit for INC-105 from current records. Do not start a review.”
- As Alex: “Calculate the monthly renewal price for DEAL-101.”
- As Mei: “Investigate INC-105 and start an SLA review for independent approval.”
- Ask in Chinese or English; expand the answer's trace to see roles, plan revision, dependencies, completed nodes and checked claims.

`POST /v1/agent/ask` and conversation run submissions accept `mode: "supervisor" | "single"`. The CLI accepts `ask --mode single` or `--mode supervisor`. `GET /v1/runs/{id}/plan` returns authorized saved plan revisions and step summaries. It checks run ownership and current evidence access. Plan/step rows, task state and sanitized events are persisted; hidden reasoning is not stored.

## Implementation and boundaries

| Area | Behavior |
|---|---|
| Plan contracts | Up to eight typed nodes; unique IDs, acyclic dependencies, declared inputs, role/tool allowlists, output schemas and completion conditions. Every node contributes to one terminal critic. Ambiguity can return clarification without an executable graph. |
| Specialists | Separate prompts, declared upstream handoffs, bounded evidence excerpts and scoped tools. Knowledge must read sources; Analyst must perform a typed read/calculation. Action may create private artifacts or start human review. Critic has no tools. |
| Scheduling | Ready independent knowledge/analyst tasks run concurrently; mutation-capable tasks run serially. Distributed provider admission can still serialize inference. |
| Changed evidence | At most two replans. Unchanged completed reads survive; stale nodes and their dependents are invalidated. A read-only rebase starts a new effective evidence epoch while preserving audit events. Changes after a mutation stop for inspection rather than replaying the action. |
| Verification | Facts carry source ID, version and exact quote. Code checks current access, provenance, numeric support and action receipts; a separate model call checks entailment, negation, units and scope. Every claim needs a verdict. Specific feedback permits at most two shared schema/claim repairs. Only the verified claim list is rendered. |
| Failures | A transient model failure has one bounded retry; read transport errors have one retry. Unknown mutation outcomes stop. Normalized successful calls can be reused, and repeated ineffective calls stop. Capability, identity and approval checks remain server-owned. |
| Budgets | Shared maximum 24 model calls, 24 tool calls, 75-second request deadline, 48 KB input per model call, 500 KB aggregate input, 550,000 conservative token reservations and 2,048 output tokens per call. Retries and repairs consume the same budget. UTF-8 byte reservations are a conservative proxy, not billed token counts. |
| Persistence | Plans are inspectable after restart, but unfinished specialist execution is request-bound and is not resumed node-by-node. Durable business workflows retain their existing checkpoint/receipt recovery. |

The critic is model-based and has not been calibrated against a human-labelled evaluation set. Passing it is not proof of factual truth. Exact business money, authority and sandbox effects remain independently checked in code. Private memo notes remain explicitly unverified. There is no real customer messaging, incident resolution or external financial execution.

## Telemetry and operating metrics

Model/tool events include role, node, plan revision, prompt version, latency and reported usage. Operations adds plan revisions/invalidations/stops, claim checks and rejected draft attempts, approval/modified-decision counts and time to a review draft. Claim-check counts include repair attempts; they are not a human-labelled groundedness score. Draft latency measures workflow creation to `approval_requested`, including later revisions; it does not measure incident resolution or customer response compliance.

`runtime/prices.json` records a dated standard paid text price schedule for the exact configured Gemini model. Known calls produce a decimal USD estimate; missing usage, unsupported modalities/models, inconsistent token counts or expired prices remain unknown. An aggregate numeric total is shown only when every included call is priced; the known subtotal remains separately available. This is a list-price estimate, not reconciled billing. See [results](UNIT_10_RESULTS.md) and the [operations runbook](OPERATIONS.md).

## Evaluation

The versioned 60-scenario manifest covers 12 SLA, 8 deal, 10 access/adversarial, 8 context/memory, 10 approval/concurrency, 8 recovery and 4 file cases. The same scenarios run through fixed workflow, single worker and supervisor strategies. A deterministic provider uses requests and observed tool results; independent assertions check stored state, money, receipts and forbidden effects. Human decisions and fault injection are harness actions. File and memory boundary probes are shared service checks, not claims of autonomous agent choices.

A separate cost-capped live script runs four read-only fictional questions in both model modes with repetitions. It asserts expected sources/numbers and absence of workflows or financial effects. It is a small provider smoke evaluation, not a 60-case live quality study or evidence that more agents improve outcomes. Raw reports preserve failures and prompt versions. [Results and reproduction](UNIT_10_RESULTS.md).

## Requirement mapping

- **F12–F14:** shared budgets, validated dynamic plans, scoped specialist handoffs, independent reads, replanning, deterministic gates and bounded semantic repair.
- **F18:** correlated plan/role events, reported usage and versioned pricing with explicit unknowns; OpenTelemetry export remains future work.
- **F19:** 60-case control benchmark, three strategies, independent effect assertions and a separate repeated live smoke set. Held-out customer families, human critic calibration and online sampling require pilot data.
- **F20:** event-derived review/claim/cost indicators. Real response/resolution SLA, manual baseline and ROI remain Unit 11.

Unit 11 requires production identity, one real integration, named reviewers and measured manual/assisted business outcomes. The local showcase does not establish those results.

## Conversational intake — 2026-11-17

A model-based intake now reads the latest message and bounded conversation history before choosing conversation, clarification or investigation. Greetings, capability explanations and questions seeking missing inputs can receive a natural reply without business tools or evidence repair. A separate conversation guard rejects replies asserting unsupported customer facts or completed actions, routing them into the existing investigation path. These replies have no evidence-verification badge. The guard is model-based, with the same calibration limitation as the semantic critic; it cannot execute tools or grant authority.

Workers now receive the bounded authorized recent history so business follow-ups can resolve references and re-read current evidence. All intake/guard calls consume the shared model budget. Repair-limit and deadline stops identify the actual constraint instead of always asking users to narrow their request. Prompt version v6 adds these behaviors; the earlier v4 benchmark reports remain historical measurements and do not measure the additional intake overhead.

## Exact case counts — 2026-11-17

`count_workspace_cases` is a read-only Analyst/Single tool returning a citable `case-summary` source. It counts all tenant-visible stored SLA tickets and deal requests with database aggregates in one snapshot, separately and combined; capped list pages cannot establish totals. Current source fingerprints detect changed counts. The organization scope matches the case/deal list access policy; aggregate counts do not grant access to private workflows or memos.

Prompt v7 supplies tool descriptions to planner/workers, routes record aggregates to Analyst, and explains that per-node limits count model turns. Plan validation rejects workers with fewer than two turns, which could request a tool but never consume its observation. The earlier v4 benchmark remains a historical comparison.

## Contextual acceptance and discovery — 2026-11-17

Prompt v8 resolves short replies against recent offers into an explicit task shared by planner, workers and repair. Up to six bounded authorized turns retain both the beginning and end of long answers. Accepting a read-only offer proceeds to discovery/detail reads; it does not authorize approval or external actions. The conversation guard rejects redundant confirmation after a clear accepted offer.

Plans using list_deals/list_sla_cases must include the corresponding detail tool. Unfiltered lists return citable, current organization-scoped catalog sources with explicit returned/total/truncated fields. Source freshness checks detect changed records. Filtered lists remain discovery results requiring individual detail reads. Disallowed read selections receive bounded corrective feedback; mutation capability violations remain denied. Numeric verification permits exact fraction-to-percent display conversion while the critic still checks field meaning.
