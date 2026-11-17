import { useEffect, useRef, useState } from "react";
import { WorkflowPanel } from "./WorkflowPanel";
type Lang = "en" | "zh";
type Deal = {
  id: string;
  version: number;
  title: string;
  owner_id: string;
  contract_id: string;
  pricing_policy_id: string;
  requested_discount: string | null;
  renewal_end: string | null;
  evidence_note: string;
};
type Policy = {
  id: string;
  version: number;
  name: string;
  currency: string;
  monthly_list_price: string;
  monthly_cost: string | null;
  cost_evidence: string;
  maximum_discount: string;
  minimum_margin: string;
};
type Contract = {
  id: string;
  version: number;
  monthly_fee: string;
  currency: string;
  ends_at: string;
};
type Detail = {
  deal: Deal;
  policy: Policy;
  contract: Contract;
  sandbox_contract: Contract;
  account: { name: string };
  can_edit: boolean;
  can_edit_cost: boolean;
  applied_operation_id: string | null;
  applied_terms: { monthly_fee: string; ends_at: string; version: number } | null;
  calculation: {
    status: string;
    proposal_amount: string | null;
    margin_fraction: string | null;
    missing_inputs: string[];
    formula: string;
  };
};
type Form = {
  id: string;
  title: string;
  contract_id: string;
  pricing_policy_id: string;
  percent: string;
  renewal_end: string;
  evidence_note: string;
  change_reason: string;
};
const empty: Form = {
  id: "",
  title: "",
  contract_id: "",
  pricing_policy_id: "",
  percent: "",
  renewal_end: "2028-01-01",
  evidence_note: "",
  change_reason: "",
};

export function DealsPanel({
  token,
  lang,
  actorId,
  available,
  people,
  ask,
}: {
  token: string;
  lang: Lang;
  actorId: string;
  available: boolean;
  people: { id: string; name: { en: string; zh: string } }[];
  ask: (question: string) => void;
}) {
  const t = (en: string, zh: string) => (lang === "zh" ? zh : en);
  const [rows, setRows] = useState<Deal[]>([]),
    [selected, setSelected] = useState(""),
    [detail, setDetail] = useState<Detail | null>(null);
  const [options, setOptions] = useState<{
    contracts: Contract[];
    policies: Policy[];
  }>({ contracts: [], policies: [] });
  const [query, setQuery] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [editing, setEditing] = useState(false),
    [creating, setCreating] = useState(false);
  const [form, setForm] = useState<Form>(empty),
    [cost, setCost] = useState(""),
    [costEvidence, setCostEvidence] = useState(""),
    [costReason, setCostReason] = useState("");
  const controller = useRef(new AbortController()),
    generation = useRef(0);
  async function api<T>(
    path: string,
    method = "GET",
    body?: unknown,
  ): Promise<T> {
    const r = await fetch(path, {
      method,
      signal: controller.current.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : t("Check the request fields.", "请检查请求字段。"),
      );
    }
    return r.json();
  }
  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      if (!controller.current.signal.aborted)
        setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!controller.current.signal.aborted) setBusy(false);
    }
  }
  async function open(id: string) {
    const n = ++generation.current;
    const data = await api<Detail>(`/v1/deals/${encodeURIComponent(id)}`);
    if (n !== generation.current || controller.current.signal.aborted) return;
    setDetail(data);
    setSelected(id);
    setEditing(false);
    setCreating(false);
    setCost(data.policy.monthly_cost || "");
    setCostEvidence(data.policy.cost_evidence);
    setCostReason("");
  }
  async function list() {
    const data = await api<{ deals: Deal[] }>(
      `/v1/deals?query=${encodeURIComponent(query)}`,
    );
    setRows(data.deals);
  }
  useEffect(() => {
    if (available)
      void run(async () => {
        const opts = await api<typeof options>("/v1/deals/options");
        setOptions(opts);
        const data = await api<{ deals: Deal[] }>("/v1/deals");
        setRows(data.deals);
        if (data.deals.length) await open(data.deals[0].id);
      });
    return () => {
      controller.current.abort();
    };
  }, []);
  function edit() {
    if (!detail) return;
    const d = detail.deal;
    setForm({
      ...empty,
      ...d,
      percent:
        d.requested_discount === null
          ? ""
          : String(Number(d.requested_discount) * 100),
      renewal_end: d.renewal_end?.slice(0, 10) || "",
    });
    setEditing(true);
    setCreating(false);
  }
  function create() {
    setForm({
      ...empty,
      contract_id: options.contracts[0]?.id || "",
      pricing_policy_id: options.policies[0]?.id || "",
    });
    setCreating(true);
    setEditing(true);
  }
  async function save() {
    await run(async () => {
      const body = {
        title: form.title,
        requested_discount:
          form.percent === "" ? null : (Number(form.percent) / 100).toFixed(4),
        renewal_end: form.renewal_end ? form.renewal_end + "T00:00:00Z" : null,
        evidence_note: form.evidence_note,
      };
      const data = await api<Detail>(
        creating ? "/v1/deals" : `/v1/deals/${encodeURIComponent(selected)}`,
        creating ? "POST" : "PATCH",
        creating
          ? {
              ...body,
              id: form.id,
              contract_id: form.contract_id,
              pricing_policy_id: form.pricing_policy_id,
            }
          : {
              ...body,
              expected_version: detail!.deal.version,
              change_reason: form.change_reason,
            },
      );
      await list();
      await open(data.deal.id);
    });
  }
  const field = (key: keyof Form, value: string) =>
    setForm((old) => ({ ...old, [key]: value }));
  const pct = (value: string | null) =>
    value === null
      ? t("Unknown", "未知")
      : `${(Number(value) * 100).toFixed(2)}%`;
  if (!available)
    return (
      <section className="case-panel">
        <h1>{t("Deal desk", "交易审核")}</h1>
        <p>
          {t(
            "Start PostgreSQL to review saved deals.",
            "请启动 PostgreSQL 以审核已保存交易。",
          )}
        </p>
      </section>
    );
  return (
    <section className="case-panel" aria-busy={busy}>
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            {t("PRICE · REVIEW · RENEW", "定价 · 审核 · 续约")}
          </div>
          <h1>{t("Deal desk", "交易审核")}</h1>
          <p>
            {t(
              "Review fictional renewals against price and margin policy. Approved terms update a local sandbox contract.",
              "按价格与毛利策略审核虚构续约。批准后的条款仅更新本地沙盒合同。",
            )}
          </p>
        </div>
        <button className="primary-button" disabled={busy} onClick={create}>
          {t("New deal", "新建交易")}
        </button>
      </div>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      <form
        className="case-filters"
        onSubmit={(e) => {
          e.preventDefault();
          void run(list);
        }}
      >
        <label>
          {t("Find a deal", "查找交易")}
          <input
            value={query}
            maxLength={200}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("Deal ID or title", "交易编号或标题")}
          />
        </label>
        <button className="secondary-button" disabled={busy}>
          {t("Filter deals", "筛选交易")}
        </button>
      </form>
      <div className="case-table-wrap">
        <table className="case-table">
          <thead>
            <tr>
              <th>{t("Deal", "交易")}</th>
              <th>{t("Requested discount", "申请折扣")}</th>
              <th>{t("Owner", "负责人")}</th>
              <th>{t("Revision", "版本")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id} className={selected === d.id ? "selected" : ""}>
                <td>
                  <button
                    disabled={busy}
                    onClick={() => void run(() => open(d.id))}
                  >
                    {d.id}
                    <span>{d.title}</span>
                  </button>
                </td>
                <td>{pct(d.requested_discount)}</td>
                <td>
                  {people.find((p) => p.id === d.owner_id)?.name[lang] ||
                    d.owner_id}
                </td>
                <td>v{d.version}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <form
          className="case-detail"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <h2>
            {creating
              ? t("New deal request", "新建交易请求")
              : t("Edit deal request", "编辑交易请求")}
          </h2>
          <fieldset disabled={busy}>
            <div className="case-form-grid">
              {creating && (
                <>
                  <label>
                    {t("Deal ID", "交易编号")}
                    <input
                      required
                      maxLength={100}
                      pattern="[A-Za-z0-9_-]+"
                      value={form.id}
                      onChange={(e) => field("id", e.target.value)}
                    />
                  </label>
                  <label>
                    {t("Source contract", "来源合同")}
                    <select
                      value={form.contract_id}
                      onChange={(e) => field("contract_id", e.target.value)}
                    >
                      {options.contracts.map((c) => (
                        <option key={c.id}>{c.id}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {t("Pricing policy", "定价策略")}
                    <select
                      value={form.pricing_policy_id}
                      onChange={(e) =>
                        field("pricing_policy_id", e.target.value)
                      }
                    >
                      {options.policies.map((p) => (
                        <option key={p.id}>{p.id}</option>
                      ))}
                    </select>
                  </label>
                </>
              )}
              <label>
                {t("Title", "标题")}
                <input
                  required
                  maxLength={200}
                  value={form.title}
                  onChange={(e) => field("title", e.target.value)}
                />
              </label>
              <label>
                {t("Requested discount (%)", "申请折扣（%）")}
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="0.01"
                  value={form.percent}
                  onChange={(e) => field("percent", e.target.value)}
                />
              </label>
              <label>
                {t("Renewal end (UTC)", "续约到期日（UTC）")}
                <input
                  type="date"
                  value={form.renewal_end}
                  onChange={(e) => field("renewal_end", e.target.value)}
                />
              </label>
              <label>
                {t("Request evidence note", "请求依据说明")}
                <textarea
                  maxLength={3000}
                  value={form.evidence_note}
                  onChange={(e) => field("evidence_note", e.target.value)}
                />
              </label>
              {!creating && (
                <label>
                  {t("Change reason", "变更原因")}
                  <input
                    required
                    maxLength={1000}
                    value={form.change_reason}
                    onChange={(e) => field("change_reason", e.target.value)}
                  />
                </label>
              )}
            </div>
            <div className="case-actions">
              <button className="primary-button">
                {t("Save deal request", "保存交易请求")}
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => setEditing(false)}
              >
                {t("Cancel edit", "取消编辑")}
              </button>
            </div>
          </fieldset>
        </form>
      )}
      {detail && !editing && (
        <>
          <section className="case-detail">
            <div className="case-section-heading">
              <div>
                <h2>
                  {detail.deal.id} · {detail.account.name}
                </h2>
                <p>
                  {detail.deal.title} · v{detail.deal.version}
                </p>
              </div>
              <div className="case-actions">
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => void run(() => open(selected))}
                >
                  {t("Reload deal", "刷新交易")}
                </button>
                <button
                  className="secondary-button"
                  disabled={busy || !detail.can_edit}
                  onClick={edit}
                >
                  {t("Edit request", "编辑请求")}
                </button>
                <button
                  className="primary-button"
                  onClick={() =>
                    ask(
                      t(
                        `Review deal ${selected}. Explain its exact discount, margin and missing inputs.`,
                        `审核交易 ${selected}，解释准确的折扣、毛利和缺失信息。`,
                      ),
                    )
                  }
                >
                  {t("Ask about deal", "询问交易")}
                </button>
              </div>
            </div>
            <div className="deal-summary">
              <div>
                <h3>{t("Pricing policy", "定价策略")}</h3>
                <p>
                  {detail.policy.id} · v{detail.policy.version}
                </p>
                <p>
                  {t("Monthly list price", "月度标价")}:{" "}
                  {detail.policy.currency} {detail.policy.monthly_list_price}
                </p>
                <p>
                  {t("Monthly cost", "月度成本")}:{" "}
                  {detail.policy.monthly_cost ?? t("Unknown", "未知")}
                </p>
                <p>
                  {t("Maximum discount", "最高折扣")}:{" "}
                  {pct(detail.policy.maximum_discount)} ·{" "}
                  {t("Minimum margin", "最低毛利率")}:{" "}
                  {pct(detail.policy.minimum_margin)}
                </p>
                <small>
                  {detail.policy.cost_evidence ||
                    t(
                      "Operations must attest the cost basis.",
                      "运营人员需确认成本依据。",
                    )}
                </small>
              </div>
              <div className="workflow-callout">
                <h3>{detail.applied_terms ? t("Recorded monthly fee", "已记录月费") : t("Proposed monthly fee", "建议月费")}</h3>
                <div className="proposal-amount">
                  {detail.policy.currency}{" "}
                  {detail.applied_terms?.monthly_fee ?? detail.calculation.proposal_amount ?? "—"}
                </div>
                {!detail.applied_terms && <p>
                  {t("Margin", "毛利率")}:{" "}
                  {pct(detail.calculation.margin_fraction)}
                </p>}
                <p>
                  {detail.applied_terms ? t("Contract change recorded. Check workflow verification below.", "合同变更已记录。请查看下方工作流核验结果。") : detail.calculation.status === "ready"
                    ? t(
                        "Within policy · requires independent approval",
                        "符合策略 · 需要独立审批",
                      )
                    : t(
                        "Blocked pending corrected terms or missing facts",
                        "需修正条款或补充事实",
                      )}
                </p>
                <ul>
                  {!detail.applied_terms && detail.calculation.missing_inputs.map((x) => (
                    <li key={x}>{x.replaceAll("_", " ")}</li>
                  ))}
                </ul>
              </div>
              <div>
                <h3>{t("Current sandbox contract", "当前沙盒合同")}</h3>
                <p>
                  {detail.sandbox_contract.id} · v
                  {detail.sandbox_contract.version}
                </p>
                <p>
                  {detail.sandbox_contract.currency}{" "}
                  {detail.sandbox_contract.monthly_fee} / {t("month", "月")}
                </p>
                <p>
                  {t("Ends", "到期")}:{" "}
                  {detail.sandbox_contract.ends_at.slice(0, 10)}
                </p>
                <small>
                  {t(
                    "Source SLA contract remains unchanged. Cost and request notes are human attestations.",
                    "来源 SLA 合同保持不变。成本和请求说明均为人工确认。",
                  )}
                </small>
              </div>
            </div>
            <details>
              <summary>
                {t("Calculation and request evidence", "计算及请求依据")}
              </summary>
              <p>{detail.calculation.formula}</p>
              <p>{detail.deal.evidence_note}</p>
            </details>
            {detail.applied_operation_id && (
              <p className="case-notice">
                {t(
                  "This request has a sandbox contract receipt. Inspect its recorded decision and verification below; another renewal requires a new request.",
                  "此请求已有沙盒合同回执。请查看下方已记录的决定和核验结果；再次续约需新建请求。",
                )}
              </p>
            )}
            {detail.can_edit_cost && (
              <details className="deal-cost">
                <summary>
                  {t("Attest policy cost (Operations)", "确认策略成本（运营）")}
                </summary>
                <p>
                  {t(
                    "Cost edits create a policy revision and invalidate pending reviews. Provide an evidence note and change reason.",
                    "成本变更会生成策略新版本并使待审核方案失效。请提供依据和变更原因。",
                  )}
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(async () => {
                      await api(
                        `/v1/pricing-policies/${detail.policy.id}/cost`,
                        "PATCH",
                        {
                          expected_version: detail.policy.version,
                          monthly_cost: cost,
                          cost_evidence: costEvidence,
                          change_reason: costReason,
                        },
                      );
                      await open(selected);
                    });
                  }}
                >
                  <fieldset disabled={busy}>
                    <div className="case-form-grid">
                      <label>
                        {t("Confirmed monthly cost", "已确认月度成本")}
                        <input
                          required
                          type="number"
                          min="0"
                          max="999999999"
                          step="0.01"
                          value={cost}
                          onChange={(e) => setCost(e.target.value)}
                        />
                      </label>
                      <label>
                        {t("Cost evidence", "成本依据")}
                        <textarea
                          required
                          maxLength={2000}
                          value={costEvidence}
                          onChange={(e) => setCostEvidence(e.target.value)}
                        />
                      </label>
                      <label>
                        {t("Cost change reason", "成本变更原因")}
                        <input
                          required
                          maxLength={1000}
                          value={costReason}
                          onChange={(e) => setCostReason(e.target.value)}
                        />
                      </label>
                    </div>
                    <button className="secondary-button">
                      {t("Save cost attestation", "保存成本确认")}
                    </button>
                  </fieldset>
                </form>
              </details>
            )}
          </section>
          <WorkflowPanel
            key={`${actorId}:${selected}:${lang}`}
            token={token}
            lang={lang}
            actorId={actorId}
            ticketId={selected}
            ticketVersion={detail.deal.version}
            people={people}
            kind="deal"
            canSubmit={detail.can_edit}
          />
        </>
      )}
    </section>
  );
}
