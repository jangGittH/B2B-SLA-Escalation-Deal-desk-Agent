# SLA Escalation / Deal Desk interface

The UI is a React/TypeScript app in `apps/web`. Its navigation sections are **Case assistant**, **SLA cases**, **Deal desk**, **Operations**, and **Policies & evidence**. The assistant helps investigate customer cases and draft private supporting documents. The evidence library exposes only authorized documents.

The scope is the customer escalation and commercial deal-desk workflow in [the development plan](DEVELOPMENT_PLAN.md). The SLA case table, incident editor, private memo editor and durable workflow panel are implemented. The workflow panel includes review memos, exact modification previews, independent approval/rejection, progress, cancellation and verified sandbox receipts. Unit 8 adds live replayable progress and Operations summaries; customer delivery remains later work.

## Development

Follow the [README](../README.md) for database initialization and a built UI served by FastAPI. During frontend development:

```bash
npm ci --prefix apps/web
npm run dev --prefix apps/web
```

Vite proxies API calls to the backend. The local demo supplies simulated employee sessions; credentials remain in browser memory, while language preference is stored locally. Switching identity aborts pending browser requests, clears visible content, and loads private history for the new employee. All data access is checked on the server.

## Working flows

- Open **SLA cases**, inspect INC-105’s exact proposal, edit incident inputs, save memo revisions and export a private document. See [Unit 5](UNIT_05.md).
- As Mei, start a review for INC-105; switch to Lin to approve, modify or reject it. Reload to inspect the persisted decision and sandbox receipt. See [Unit 6](UNIT_06.md).
- Investigate Acme INC-104 against permitted SLA terms and incident evidence.
- As Alex, create/edit a deal, inspect exact discount/margin checks and submit review; as Lin, preview modified terms, approve/reject and inspect the sandbox contract receipt. Operations can attest policy cost; changes require fresh review. See [Unit 7](UNIT_07.md).
- Generate private memo exports with authenticated download buttons.
- Reopen saved conversations after a refresh or server restart.
- Search and open English/Chinese policies, case records and source links.

The UI distinguishes calculated proposals, approvals, recorded sandbox credits/contracts and technical incident resolution. No real credit, contract update or customer message is sent. Persistent conversation requests use `/v1/conversations/{id}/messages`; fixture-only mode uses `/v1/agent/ask`. See [Unit 4](UNIT_04.md) for persistence boundaries and browser-test setup.

Browser tests cover focused navigation, source access, language, mobile layout, failed provider requests, identity switches, file downloads, persistent history, reviewer access, modified-credit previews, rejection, missing inputs, workflow cancellation, deal creation/editing, reduced discounts, sandbox renewal receipts and Operations cost attestation. Use the deterministic test server for the complete suite; it creates a separate disposable PostgreSQL database.

## Unit 8 operations and live progress

Workflow starts are durably queued. Progress streams use bearer headers and replay from the last event ID after reconnecting, with a polling fallback. Cancellation still preserves committed receipts. **Operations** shows all workflow outcome states, current delivery health, system/human timing, known token usage and explicit unknown inference cost. Operations employees see tenant aggregates; recent trace details remain requester/reviewer scoped. Other employees see only their requested/assigned workflows. See [Unit 8](UNIT_08.md) for metric definitions and [results](UNIT_08_RESULTS.md) for browser/recovery checks.

## Unit 9 knowledge and memory

**Policies & evidence** now includes versioned private uploads and a preference/memory review panel. TXT/Markdown, DOCX and text PDF uploads retain original bytes and current authorization. Use the returned upload ID and expected version to revise/delete an existing upload. Organization sharing is restricted to Operations; team scope is also supported by the API.

A proposed layout remains pending until confirmed by its owner (or an authorized Operations reviewer for shared defaults). Editing returns it to pending, and deletion disables its future use. Confirmed evidence-first layout affects subsequent incident memo exports. Stale/expired memories hide their remembered content. English desktop and Chinese mobile flows are covered in `tests/knowledge-memory.spec.ts`; [Unit 9](UNIT_09.md) documents API limits and retrieval setup.

## Unit 10 specialist plans

The composer offers Specialist team and Single assistant in English and Chinese. Answer traces show the active plan revision, role/dependency/completion summaries and supported versus checked claims. These summaries survive conversation reload. Operations includes plan/claim/review indicators and known-versus-unknown model cost. Neither display grants approval or treats a draft as executed.

Run the structured-provider browser server with `PYTHONPATH=. UI_STRUCTURED_MODEL=1 UI_TEST_PORT=8013` and the normal `TEST_DATABASE_URL`, then run `UI_BASE_URL=http://127.0.0.1:8013 UI_STRUCTURED_MODEL=1 npm run test:e2e --prefix apps/web`. The provider is deterministic; live model checks are separate. The planning test covers reload, mode switching and Chinese mobile layout.
