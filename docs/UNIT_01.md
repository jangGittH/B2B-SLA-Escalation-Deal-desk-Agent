# Unit 1 — identity and permission-aware evidence

This foundation now serves the SLA Escalation / Deal Desk product. Domain records in `work_assistant/models.py` validate organizations, teams, employees, document versions, and grants. `repository.py` provides the fixture implementation; `db/store.py` provides PostgreSQL storage.

## Try it

Follow the [README setup](../README.md), or use `demo --ephemeral` for fixture-only mode.

```bash
uv run python -m work_assistant validate
uv run python -m work_assistant search --employee mei --language zh "Acme 响应时限"
uv run python -m work_assistant search --employee alex --language en "Acme"
```

CLI employee arguments explicitly simulate an identity locally. HTTP requests derive identity from a bearer token mapped by the server. An employee ID in search JSON is rejected.

| Employee | Role in the fictional case | Additional restricted evidence |
|---|---|---|
| Mei Chen / 陈梅 | Engineering investigator | Incident runbook and personal investigation notes |
| Alex Wang / 王安 | Account manager | Acme renewal deal draft |
| Lin Zhao / 赵林 | Operations reviewer | Credit-review policy |
| Sam Lee / 李森 | Employee of another tenant | Only that tenant's unrelated account policy |

Northstar employees also share the operating guide, Acme SLA terms, and incident history. Search permission checks run before scoring and limiting; forbidden IDs return the same 404 as missing IDs. Organization boundaries always apply, even for organization-visible documents.

## HTTP boundary

`GET /v1/me`, `POST /v1/knowledge/search`, and `GET /v1/documents/{id}` require authentication. For example:

```json
{"query":"Acme response target","language":"en","limit":5}
```

Try reading `inc-104-runbook` as Alex: it returns 404. Try the same search as Mei: authorized results include it. Chinese and English content both contribute to lexical matching regardless of display language. Scores measure keyword overlap, not confidence or semantic understanding.

## Knowledge to learn

Pydantic validation; immutable domain records; tenant/resource authorization; identity binding; repository/service boundaries; lexical ranking; consistent HTTP errors; tests for forbidden-source leakage. Read `models.py`, `repository.py`, `knowledge.py`, then `api.py`.

PostgreSQL persists these records in Unit 4, but production SSO, role-based write policies, and typed ticket/contract mutations are future work. The current `projects` field groups evidence by customer account for display; it is not yet a transactional customer/contract schema.

Run `uv run pytest tests/test_workspace.py tests/test_workspace_ui.py -q` to check permissions and fixture integrity.
