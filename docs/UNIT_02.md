# Unit 2 — case investigation and private document tools

`work_assistant/agent.py` implements a bounded Gemini tool loop for SLA escalation and commercial deal-desk evidence. `tools.py` retains these three foundation tools; [Unit 5](UNIT_05.md#api-and-tools) adds five SLA case/calculation/memo tools. [Unit 6](UNIT_06.md) adds start/get/resume SLA workflow tools; [Unit 7](UNIT_07.md) adds six deal list/read/calculation/start/get/resume tools:

| Tool | Purpose |
|---|---|
| `search_knowledge(query, limit)` | Find authorized evidence previews and document IDs |
| `read_document(document_id)` | Read an authorized source before using it as a citation |
| `write_file(filename, content)` | Create a private downloadable memo or supporting file |

The backend binds identity and language; neither is a model-controlled tool argument. Unknown tools and malformed arguments return structured safe errors. Inactive employees terminate the run. The default budgets are six model turns and eight tool calls; final answers allow one repair before stopping.

## Try it

Set `GOOGLE_API_KEY` and use the [local setup](../README.md).

```bash
uv run python -m work_assistant ask --employee mei --language zh \
  "请查阅 Acme SLA 和 INC-104 证据，说明响应时限与尚缺的信息。"
```

The CLI and `POST /v1/agent/ask` are stateless debugging interfaces. The UI uses the persistent conversation API described in [Unit 4](UNIT_04.md).

The model receives a source-backed final-response contract. It must cite only successfully read sources, distinguish unknowns and proposals, and report tool success accurately. Search previews are not read evidence. Tools return files separately from answer prose; the model must not invent download URLs.

## Execution boundary

The application preserves function-call IDs and model thought signatures in the conversation sent back to Gemini. Tool execution happens explicitly in application code, not through SDK automatic execution. Pydantic schemas reject extra identity/scope fields. The model cannot approve credits, update contracts, change tickets, send customer messages, or deploy changes because no such tools exist.

Private exports remain basic: DOCX headings/paragraphs, PDF text, XLSX literal text cells, and PPTX title/body slides. JSON is validated. Content and file size are bounded; filenames cannot choose filesystem paths. Source verification of every generated claim is still a roadmap item: schema/citation checks alone do not establish semantic support, and full claim-level validation remains necessary before acting on arbitrary narrative. Unit 6 executes only a deterministically verified numeric sandbox proposal after human approval; it does not publish narrative or send customer messages. Unit 5 restricts uncited file responses to a server-generated acknowledgement and checks typed memo source versions.

## Knowledge to learn

Tool schemas and dispatch; structured model output; evidence provenance; async boundaries; deadlines and capacity limits; bounded recovery; injection-resistant application boundaries; binary file formats and authenticated downloads. Read `tools.py`, `agent.py`, `files.py`, and `telemetry.py`.

Tests in `test_agent.py` and `test_files.py` use scripted provider responses. They validate the runtime contract, not real-model business quality. Unit 5 adds exact SLA calculations and business-state tests. Unit 6 implements a fixed checkpointed SLA workflow and independent human approval. Dynamic planning and collaborating specialists remain later increments.
