import { WorkflowPanel } from "./WorkflowPanel";
import React, { useEffect, useRef, useState } from "react";

type Lang = "en" | "zh";
type Window = { start: string; end: string };
type Fields = {
  title: string;
  severity: string;
  status: string;
  assignee_id: string;
  reported_at: string;
  first_response_at: string | null;
  incident_started_at: string | null;
  incident_ended_at: string | null;
  exclusions: Window[] | null;
  eligibility_confirmed: boolean | null;
  prior_credit_amount: string | null;
  billing_period_start: string | null;
  billing_period_end: string | null;
  evidence_note: string;
};
type Ticket = Fields & {
  id: string;
  contract_id: string;
  version: number;
  updated_at: string;
};
type Calculation = {
  status: string;
  proposal_amount: string | null;
  currency: string;
  missing_inputs: string[];
  response_status: string;
  response_deadline: string | null;
  eligible_minutes?: string;
  excluded_minutes?: string;
  remaining_cap?: string;
  formula: string;
  formula_version: string;
  policy_id: string;
  policy_version: number;
};
type Detail = {
  ticket: Ticket;
  account: {
    id: string;
    name: string;
    contact_name: string;
    contact_email: string;
  };
  contract: {
    id: string;
    version: number;
    signed: boolean;
    monthly_fee: string;
    currency: string;
    starts_at: string;
    ends_at: string;
  };
  policy: {
    id: string;
    version: number;
    credit_rate_per_minute: string;
    monthly_cap_fraction: string;
  };
  calculation: Calculation;
  can_edit: boolean;
  memos: { id: string; version: number }[];
};
type Notes = {
  hypotheses: string;
  next_steps: string;
  customer_response_draft: string;
};
type Memo = {
  id: string;
  version: number;
  stale: boolean;
  content: string;
  notes: Notes;
  revisions?: number[];
  language: Lang;
};
type Options = {
  accounts: { id: string; name: string }[];
  contracts: { id: string; account_id: string }[];
  assignees: { id: string; name: { en: string; zh: string } }[];
};
const emptyNotes: Notes = {
  hypotheses: "",
  next_steps: "",
  customer_response_draft: "",
};
const editable = (ticket: Ticket): Fields => ({
  title: ticket.title,
  severity: ticket.severity,
  status: ticket.status,
  assignee_id: ticket.assignee_id,
  reported_at: ticket.reported_at,
  first_response_at: ticket.first_response_at,
  incident_started_at: ticket.incident_started_at,
  incident_ended_at: ticket.incident_ended_at,
  exclusions: ticket.exclusions,
  eligibility_confirmed: ticket.eligibility_confirmed,
  prior_credit_amount: ticket.prior_credit_amount,
  billing_period_start: ticket.billing_period_start,
  billing_period_end: ticket.billing_period_end,
  evidence_note: ticket.evidence_note,
});
const dateValue = (value: string | null) =>
  value ? new Date(value).toISOString().slice(0, 19) : "";
const dateInput = (value: string) =>
  value ? new Date(value + "Z").toISOString() : null;

export function CasesPanel({
  token,
  lang,
  actorId,
  available,
  ask,
}: {
  token: string;
  lang: Lang;
  actorId: string;
  available: boolean;
  ask: (question: string) => void;
}) {
  const t = (en: string, zh: string) => (lang === "zh" ? zh : en);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [options, setOptions] = useState<Options>({
    accounts: [],
    contracts: [],
    assignees: [],
  });
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [account, setAccount] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [fields, setFields] = useState<Fields | null>(null);
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newId, setNewId] = useState("");
  const [contractId, setContractId] = useState("");
  const [reason, setReason] = useState("");
  const [memo, setMemo] = useState<Memo | null>(null);
  const [notes, setNotes] = useState<Notes>(emptyNotes);
  const [revision, setRevision] = useState("");
  const [format, setFormat] = useState("docx");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const controller = useRef(new AbortController());
  const listing = useRef(0);
  useEffect(() => () => controller.current.abort(), []);

  async function api<T>(
    path: string,
    method = "GET",
    body?: unknown,
  ): Promise<T> {
    const response = await fetch(path, {
      method,
      signal: controller.current.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const message =
        typeof data.detail === "string"
          ? data.detail
          : t("Check the fields and timestamps.", "请检查字段和时间。");
      throw Error(
        response.status === 409
          ? t(
              "This record changed. Reload before saving. ",
              "记录已改变，请刷新后保存。 ",
            ) + message
          : message,
      );
    }
    return response.json();
  }
  async function run(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (err) {
      if (!controller.current.signal.aborted)
        setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!controller.current.signal.aborted) setBusy(false);
    }
  }
  async function list() {
    const version = ++listing.current;
    const params = new URLSearchParams({ query });
    if (status) params.set("status", status);
    if (account) params.set("account_id", account);
    const data = await api<{ tickets: Ticket[] }>(`/v1/cases?${params}`);
    if (version === listing.current) setTickets(data.tickets);
  }
  async function load(id: string) {
    const data = await api<Detail>(`/v1/cases/${encodeURIComponent(id)}`);
    setDetail(data);
    setFields(editable(data.ticket));
    setCreating(false);
    setEditing(false);
    setMemo(null);
    setNotes(emptyNotes);
    setRevision("");
    setReason("");
  }
  useEffect(() => {
    if (!available) return;
    void run(async () => {
      setOptions(await api<Options>("/v1/cases/options"));
      await list();
    });
  }, []);
  function field<K extends keyof Fields>(key: K, value: Fields[K]) {
    setFields((old) => (old ? { ...old, [key]: value } : old));
  }
  function newCase() {
    setCreating(true);
    setEditing(true);
    setDetail(null);
    setMemo(null);
    setNewId("");
    setContractId(options.contracts[0]?.id || "");
    setError("");
    setNotice("");
    setFields({
      title: "",
      severity: "P1",
      status: "open",
      assignee_id: actorId,
      reported_at: new Date().toISOString(),
      first_response_at: null,
      incident_started_at: null,
      incident_ended_at: null,
      exclusions: null,
      eligibility_confirmed: null,
      prior_credit_amount: null,
      billing_period_start: null,
      billing_period_end: null,
      evidence_note: "",
    });
  }
  async function saveCase(event: React.FormEvent) {
    event.preventDefault();
    await run(async () => {
      const data = creating
        ? await api<Ticket>("/v1/cases", "POST", {
            ...fields,
            id: newId,
            contract_id: contractId,
          })
        : await api<Ticket>(`/v1/cases/${detail!.ticket.id}`, "PATCH", {
            ...fields,
            expected_version: detail!.ticket.version,
            change_reason: reason,
          });
      await list();
      await load(data.id);
      setNotice(t("Case saved.", "案例已保存。"));
    });
  }
  async function saveMemo() {
    await run(async () => {
      const current = detail!;
      const saved = memo
        ? await api<Memo>(`/v1/memos/${memo.id}`, "PATCH", {
            ...notes,
            expected_version: memo.version,
            expected_ticket_version: current.ticket.version,
          })
        : await api<Memo>(`/v1/cases/${current.ticket.id}/memos`, "POST", {
            ...notes,
            expected_ticket_version: current.ticket.version,
            language: lang,
          });
      const refreshed = await api<Detail>(`/v1/cases/${current.ticket.id}`);
      setDetail(refreshed);
      const stored = await api<Memo>(`/v1/memos/${saved.id}`);
      setMemo(stored);
      setNotes(stored.notes);
      setNotice(
        t(
          "Private memo saved. Nothing sent or issued.",
          "私人备忘录已保存。未发送回复或发放抵扣款。",
        ),
      );
    });
  }
  async function download() {
    await run(async () => {
      const artifact = await api<{ download_url: string; filename: string }>(
        `/v1/memos/${memo!.id}/exports`,
        "POST",
        { expected_version: memo!.version, format },
      );
      const response = await fetch(artifact.download_url, {
        signal: controller.current.signal,
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok)
        throw Error(
          t(
            "Evidence changed. Refresh the memo before exporting.",
            "证据已改变，请刷新备忘录后导出。",
          ),
        );
      const blob = await response.blob();
      if (controller.current.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.filename;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  const fieldLabels: [
    keyof Pick<
      Fields,
      | "reported_at"
      | "first_response_at"
      | "incident_started_at"
      | "incident_ended_at"
      | "billing_period_start"
      | "billing_period_end"
    >,
    string,
  ][] = [
    ["reported_at", t("Reported at (UTC)", "报告时间（UTC）")],
    ["first_response_at", t("First response (UTC)", "首次响应（UTC）")],
    ["incident_started_at", t("Outage start (UTC)", "故障开始（UTC）")],
    ["incident_ended_at", t("Outage end (UTC)", "故障结束（UTC）")],
    [
      "billing_period_start",
      t("Billing period start (UTC)", "计费周期开始（UTC）"),
    ],
    [
      "billing_period_end",
      t("Billing period end (UTC)", "计费周期结束（UTC）"),
    ],
  ];
  if (!available)
    return (
      <section className="case-panel">
        <h1>{t("SLA cases", "SLA 案例")}</h1>
        <p>
          {t(
            "Start the PostgreSQL workspace to use saved cases and memos.",
            "请启动 PostgreSQL 工作空间以使用案例和备忘录。",
          )}
        </p>
      </section>
    );
  return (
    <section className="case-panel" aria-busy={busy}>
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            {t("INVESTIGATE · CALCULATE · DRAFT", "调查 · 计算 · 起草")}
          </div>
          <h1>{t("SLA cases", "SLA 案例")}</h1>
          <p>
            {t(
              "Fictional customer cases. Approved credits are recorded in the local sandbox; customer messages remain drafts.",
              "虚构客户案例。已批准的抵扣款记录在本地沙盒中；客户回复仍为草稿。",
            )}
          </p>
        </div>
        <button className="primary-button" disabled={busy} onClick={newCase}>
          {t("New case", "新建案例")}
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="case-notice" role="status">
          {notice}
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
          {t("Find a case", "查找案例")}
          <input
            value={query}
            maxLength={200}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("Case ID or title", "案例编号或标题")}
          />
        </label>
        <label>
          {t("Customer", "客户")}
          <select value={account} onChange={(e) => setAccount(e.target.value)}>
            <option value="">{t("All customers", "所有客户")}</option>
            {options.accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("Case status", "案例状态")}
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">{t("All statuses", "全部状态")}</option>
            <option value="open">{t("Open", "待处理")}</option>
            <option value="investigating">
              {t("Investigating", "调查中")}
            </option>
          </select>
        </label>
        <button className="secondary-button" disabled={busy}>
          {t("Filter cases", "筛选案例")}
        </button>
      </form>
      <div className="case-table-wrap">
        <table className="case-table">
          <thead>
            <tr>
              <th>{t("Case", "案例")}</th>
              <th>{t("Severity", "等级")}</th>
              <th>{t("Status", "状态")}</th>
              <th>{t("Assignee", "负责人")}</th>
              <th>{t("Revision", "版本")}</th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((ticket) => (
              <tr
                key={ticket.id}
                className={detail?.ticket.id === ticket.id ? "selected" : ""}
              >
                <td>
                  <button
                    disabled={busy}
                    onClick={() => void run(() => load(ticket.id))}
                  >
                    <strong>{ticket.id}</strong>
                    <span>{ticket.title}</span>
                  </button>
                </td>
                <td>{ticket.severity}</td>
                <td>
                  {ticket.status === "open"
                    ? t("Open", "待处理")
                    : t("Investigating", "调查中")}
                </td>
                <td>
                  {options.assignees.find((a) => a.id === ticket.assignee_id)
                    ?.name[lang] || ticket.assignee_id}
                </td>
                <td>v{ticket.version}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!tickets.length && !busy && (
        <p>{t("No cases match these filters.", "没有符合筛选条件的案例。")}</p>
      )}
      {detail && !editing && (
        <article className="case-detail">
          <div className="case-section-heading">
            <div>
              <h2>
                {detail.ticket.id} · {detail.account.name}
              </h2>
              <p>
                {detail.ticket.title} · v{detail.ticket.version}
              </p>
            </div>
            <div className="case-actions">
              <button
                className="secondary-button"
                disabled={busy}
                onClick={() => void run(() => load(detail.ticket.id))}
              >
                {t("Reload case", "刷新案例")}
              </button>
              <button
                className="secondary-button"
                disabled={busy || !detail.can_edit}
                onClick={() => setEditing(true)}
              >
                {t("Edit incident facts", "编辑事件事实")}
              </button>
              <button
                className="primary-button"
                disabled={busy}
                onClick={() =>
                  ask(
                    t(
                      `Investigate ${detail.ticket.id}, calculate SLA impact using the case tools, and explain confirmed inputs and missing evidence.`,
                      `请使用案例工具调查 ${detail.ticket.id} 并计算 SLA 影响，说明已确认输入和缺失证据。`,
                    ),
                  )
                }
              >
                {t("Ask about this case", "询问此案例")}
              </button>
            </div>
          </div>
          {!detail.can_edit && (
            <p>
              {t(
                "Only the current assignee can edit incident facts. Your memos are private.",
                "只有当前负责人可以编辑事件事实。你的备忘录仅自己可见。",
              )}
            </p>
          )}
          <div className="case-summary-grid">
            <div>
              <h3>{t("Contract & policy", "合同与政策")}</h3>
              <p>
                {detail.contract.id} · v{detail.contract.version} ·{" "}
                {detail.contract.signed
                  ? t("Demo signed terms", "演示已签条款")
                  : t("Unsigned", "未签署")}
              </p>
              <p>
                {detail.contract.currency} {detail.contract.monthly_fee} /{" "}
                {t("month", "月")}
              </p>
              <p>
                {detail.policy.id} · v{detail.policy.version}
              </p>
              <p>
                {t("Rate per eligible minute", "每有效分钟费率")}:{" "}
                {detail.policy.credit_rate_per_minute}
                <br />
                {t("Monthly cap fraction", "月度上限比例")}:{" "}
                {detail.policy.monthly_cap_fraction}
              </p>
              <small>
                {t(
                  "24/7 elapsed time. Use UTC calendar months and confirmed prior credits.",
                  "全天候计时。使用 UTC 自然月及已确认的已抵扣金额。",
                )}
              </small>
            </div>
            <div className="calculation-card">
              <h3>{t("Credit proposal", "抵扣款建议")}</h3>
              <strong className="proposal-amount">
                {detail.calculation.proposal_amount === null
                  ? t("More evidence needed", "需要更多证据")
                  : `${detail.calculation.currency} ${detail.calculation.proposal_amount}`}
              </strong>
              <p>{t("Not approved · Not issued", "未批准 · 未发放")}</p>
              {detail.calculation.missing_inputs.length > 0 ? (
                <ul>
                  {detail.calculation.missing_inputs.map((input) => (
                    <li key={input}>{input.replaceAll("_", " ")}</li>
                  ))}
                </ul>
              ) : (
                <p>
                  {t("Eligible minutes", "有效分钟")}:{" "}
                  {detail.calculation.eligible_minutes}
                  <br />
                  {t("Excluded minutes", "排除分钟")}:{" "}
                  {detail.calculation.excluded_minutes}
                  <br />
                  {t("Remaining cap", "剩余上限")}:{" "}
                  {detail.calculation.remaining_cap}
                </p>
              )}
              <small>
                {detail.calculation.formula_version} ·{" "}
                {t(
                  "Decimal; half-up rounding to 2 places",
                  "十进制定点计算；四舍五入至两位",
                )}
              </small>
            </div>
            <div>
              <h3>{t("Response target", "响应目标")}</h3>
              <p>
                {detail.calculation.response_deadline
                  ? new Date(detail.calculation.response_deadline).toISOString()
                  : t("Unknown", "未知")}
              </p>
              <p>{detail.calculation.response_status.replaceAll("_", " ")}</p>
              <h3>{t("Recorded evidence note", "记录的证据备注")}</h3>
              <p>
                {detail.ticket.evidence_note ||
                  t("No supporting note yet.", "尚无支持性备注。")}
              </p>
              <small>
                {t(
                  "Human-entered facts; not independently verified. Root cause is unknown unless separately established.",
                  "人工输入事实，尚未独立验证。根因需另行确认。",
                )}
              </small>
            </div>
          </div>
          <details>
            <summary>
              {t(
                "Inspect exact formula and source versions",
                "查看精确公式与来源版本",
              )}
            </summary>
            <pre>{JSON.stringify(detail.calculation, null, 2)}</pre>
          </details>
        </article>
      )}
      {editing && fields && (
        <form
          className="case-detail case-form"
          onSubmit={(e) => void saveCase(e)}
        >
          <h2>
            {creating
              ? t("Create an SLA case", "创建 SLA 案例")
              : `${t("Edit", "编辑")} ${detail!.ticket.id} · v${detail!.ticket.version}`}
          </h2>
          <p>
            {t(
              "Enter confirmed facts only. Blank values stay unknown. All timestamps below use UTC.",
              "仅输入已确认事实，留空代表未知。以下所有时间均为 UTC。",
            )}
          </p>
          <fieldset disabled={busy}>
            <div className="case-form-grid">
              {creating && (
                <>
                  <label>
                    {t("Case ID", "案例编号")}
                    <input
                      required
                      pattern="[A-Za-z0-9_-]+"
                      maxLength={100}
                      value={newId}
                      onChange={(e) => setNewId(e.target.value)}
                    />
                  </label>
                  <label>
                    {t("Contract", "合同")}
                    <select
                      required
                      value={contractId}
                      onChange={(e) => setContractId(e.target.value)}
                    >
                      {options.contracts.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.id}
                        </option>
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
                  value={fields.title}
                  onChange={(e) => field("title", e.target.value)}
                />
              </label>
              <label>
                {t("Severity", "等级")}
                <select
                  value={fields.severity}
                  onChange={(e) => field("severity", e.target.value)}
                >
                  {["P1", "P2", "P3"].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label>
                {t("Status", "状态")}
                <select
                  value={fields.status}
                  onChange={(e) => field("status", e.target.value)}
                >
                  <option value="open">{t("Open", "待处理")}</option>
                  <option value="investigating">
                    {t("Investigating", "调查中")}
                  </option>
                </select>
              </label>
              <label>
                {t("Assignee", "负责人")}
                <select
                  value={fields.assignee_id}
                  disabled={creating}
                  onChange={(e) => field("assignee_id", e.target.value)}
                >
                  {options.assignees.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name[lang]}
                    </option>
                  ))}
                </select>
              </label>
              {fieldLabels.map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    type="datetime-local"
                    step="1"
                    required={key === "reported_at"}
                    value={dateValue(fields[key])}
                    onChange={(e) =>
                      field(key, dateInput(e.target.value) as string)
                    }
                  />
                </label>
              ))}
              <label>
                {t("Eligibility confirmed", "资格已确认")}
                <select
                  value={
                    fields.eligibility_confirmed === null
                      ? "unknown"
                      : String(fields.eligibility_confirmed)
                  }
                  onChange={(e) =>
                    field(
                      "eligibility_confirmed",
                      e.target.value === "unknown"
                        ? null
                        : e.target.value === "true",
                    )
                  }
                >
                  <option value="unknown">{t("Unknown", "未知")}</option>
                  <option value="true">
                    {t("Confirmed eligible", "已确认符合资格")}
                  </option>
                  <option value="false">
                    {t("Confirmed ineligible", "已确认不符合资格")}
                  </option>
                </select>
              </label>
              <label>
                {t(
                  "Prior credits in this billing period",
                  "本计费周期已抵扣金额",
                )}
                <input
                  type="number"
                  min="0"
                  max="999999999"
                  step="0.01"
                  value={fields.prior_credit_amount ?? ""}
                  onChange={(e) =>
                    field("prior_credit_amount", e.target.value || null)
                  }
                />
              </label>
            </div>
            <label className="case-check">
              <input
                type="checkbox"
                checked={fields.exclusions !== null}
                onChange={(e) =>
                  field("exclusions", e.target.checked ? [] : null)
                }
              />
              {t(
                "Exclusions verified (empty list means none)",
                "已核实排除时段（空列表代表无排除）",
              )}
            </label>
            {fields.exclusions !== null && (
              <div className="exclusion-list">
                {fields.exclusions.map((window, index) => (
                  <div className="case-form-grid" key={index}>
                    {(["start", "end"] as const).map((key) => (
                      <label key={key}>
                        {t(
                          `Exclusion ${index + 1} ${key} (UTC)`,
                          `排除时段 ${index + 1} ${key === "start" ? "开始" : "结束"}（UTC）`,
                        )}
                        <input
                          type="datetime-local"
                          step="1"
                          required
                          value={dateValue(window[key])}
                          onChange={(e) =>
                            field(
                              "exclusions",
                              fields.exclusions!.map((w, i) =>
                                i === index
                                  ? {
                                      ...w,
                                      [key]: dateInput(e.target.value) || "",
                                    }
                                  : w,
                              ),
                            )
                          }
                        />
                      </label>
                    ))}
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() =>
                        field(
                          "exclusions",
                          fields.exclusions!.filter((_, i) => i !== index),
                        )
                      }
                    >
                      {t("Remove interval", "移除时段")}
                    </button>
                  </div>
                ))}
                <button
                  className="secondary-button"
                  type="button"
                  disabled={fields.exclusions.length >= 100}
                  onClick={() =>
                    field("exclusions", [
                      ...fields.exclusions!,
                      { start: "", end: "" },
                    ])
                  }
                >
                  {t("Add exclusion interval", "添加排除时段")}
                </button>
              </div>
            )}
            <label>
              {t("Supporting evidence / attestation", "支持证据 / 确认说明")}
              <textarea
                maxLength={3000}
                value={fields.evidence_note}
                onChange={(e) => field("evidence_note", e.target.value)}
              />
            </label>
            {!creating && (
              <label>
                {t("Reason for change", "修改原因")}
                <input
                  required
                  maxLength={1000}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
            )}
            <div className="case-actions">
              <button className="primary-button">
                {t("Save case", "保存案例")}
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setEditing(false);
                  setCreating(false);
                  if (detail) setFields(editable(detail.ticket));
                }}
              >
                {t("Cancel", "取消")}
              </button>
            </div>
          </fieldset>
        </form>
      )}
      {detail && !editing && (
        <WorkflowPanel
          key={`${token}:${detail.ticket.id}:${lang}`}
          token={token}
          lang={lang}
          actorId={actorId}
          ticketId={detail.ticket.id}
          ticketVersion={detail.ticket.version}
          people={options.assignees}
        />
      )}
      {detail && !editing && (
        <section className="case-detail memo-editor">
          <div className="case-section-heading">
            <div>
              <h2>{t("Private incident memo", "私人事件备忘录")}</h2>
              <p>
                {t(
                  "Facts and calculations come from the case. Editable notes below are labeled unverified.",
                  "事实和计算取自案例。以下可编辑备注明确标为未验证。",
                )}
              </p>
            </div>
            <label>
              {t("Saved memos", "已保存的备忘录")}
              <select
                disabled={busy}
                value={memo?.id || ""}
                onChange={(e) => {
                  const id = e.target.value;
                  if (!id) {
                    setMemo(null);
                    setNotes(emptyNotes);
                    return;
                  }
                  void run(async () => {
                    const row = await api<Memo>(`/v1/memos/${id}`);
                    setMemo(row);
                    setNotes(row.notes);
                    setRevision("");
                  });
                }}
              >
                <option value="">
                  {t("New private memo", "新建私人备忘录")}
                </option>
                {detail.memos.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id.slice(0, 8)} · v{m.version}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {memo?.stale && (
            <p className="error-banner" role="alert">
              {t(
                "Source facts changed. Reload the case, reopen this memo and save a new revision before exporting.",
                "来源事实已改变。请刷新案例，重新打开此备忘录并保存新版本后再导出。",
              )}
            </p>
          )}
          <fieldset disabled={busy}>
            {(
              [
                ["hypotheses", t("Hypotheses — unverified", "假设 — 未验证")],
                ["next_steps", t("Proposed next steps", "建议下一步")],
                [
                  "customer_response_draft",
                  t(
                    "Customer response draft — not sent",
                    "客户回复草稿 — 未发送",
                  ),
                ],
              ] as [keyof Notes, string][]
            ).map(([key, label]) => (
              <label key={key}>
                {label}
                <textarea
                  aria-label={label}
                  value={notes[key]}
                  maxLength={key === "customer_response_draft" ? 4000 : 3000}
                  onChange={(e) =>
                    setNotes({ ...notes, [key]: e.target.value })
                  }
                />
              </label>
            ))}
            <div className="case-actions">
              <button
                className="primary-button"
                onClick={() => void saveMemo()}
              >
                {memo
                  ? t("Save memo revision", "保存备忘录版本")
                  : t("Save private memo", "保存私人备忘录")}
              </button>
              {memo && (
                <>
                  <label>
                    {t("Export format", "导出格式")}
                    <select
                      value={format}
                      onChange={(e) => setFormat(e.target.value)}
                    >
                      {["docx", "pdf", "md", "json"].map((f) => (
                        <option key={f}>{f}</option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="secondary-button"
                    disabled={
                      memo.stale ||
                      JSON.stringify(notes) !== JSON.stringify(memo.notes)
                    }
                    onClick={() => void download()}
                  >
                    {t("Download saved memo", "下载已保存备忘录")} · v
                    {memo.version}
                  </button>
                </>
              )}
            </div>
            {memo && (
              <div className="case-actions">
                <label>
                  {t("Previous revision", "历史版本")}
                  <select
                    value={revision}
                    onChange={(e) => setRevision(e.target.value)}
                  >
                    <option value="">{t("Choose revision", "选择版本")}</option>
                    {(memo.revisions || [])
                      .filter((v) => v < memo.version)
                      .map((v) => (
                        <option key={v} value={v}>
                          v{v}
                        </option>
                      ))}
                  </select>
                </label>
                <button
                  className="secondary-button"
                  disabled={!revision}
                  onClick={() =>
                    void run(async () => {
                      const row = await api<Memo>(
                        `/v1/memos/${memo.id}/restore`,
                        "POST",
                        {
                          expected_version: memo.version,
                          expected_ticket_version: detail.ticket.version,
                          revision: Number(revision),
                        },
                      );
                      setMemo(await api<Memo>(`/v1/memos/${row.id}`));
                      setNotes(row.notes);
                      setRevision("");
                      setDetail(
                        await api<Detail>(`/v1/cases/${detail.ticket.id}`),
                      );
                    })
                  }
                >
                  {t("Restore notes as new revision", "将备注恢复为新版本")}
                </button>
              </div>
            )}
          </fieldset>
          {memo && (
            <details>
              <summary>
                {t(
                  "Inspect saved memo and source snapshot",
                  "查看已保存备忘录及来源快照",
                )}
              </summary>
              <pre>{memo.content}</pre>
            </details>
          )}
        </section>
      )}
    </section>
  );
}
