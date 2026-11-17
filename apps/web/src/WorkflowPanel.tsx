import { useEffect, useRef, useState } from "react";

type Lang = "en" | "zh";
type Terms = {
  sandbox_period_total?: string;
  monthly_fee?: string;
  ends_at?: string;
  version?: number;
};
type Payload = {
  maximum_discount?: string;
  margin_fraction?: string;
  amount: string;
  maximum_amount: string;
  currency: string;
  action: string;
  before: Terms;
  after: Terms;
  sources: { type: string; id: string; version: number }[];
};
type Workflow = {
  id: string;
  ticket_id: string;
  employee_id: string;
  reviewer_id: string | null;
  conversation_id: string;
  version: number;
  status: string;
  proposal_revision: number;
  missing_inputs: string[];
  error: { category: string; message: string } | null;
  retry_at: string | null;
  memo: { id: string; version: number; content: string } | null;
  approval: {
    id: string;
    version: number;
    status: string;
    stale: boolean;
    reviewer_id: string;
    expires_at: string;
    payload: Payload | null;
    approved_payload: Payload | null;
    decision: string | null;
    reason: string | null;
  } | null;
  receipt: {
    operation_id: string;
    result: {
      amount: string;
      currency: string;
      effect: string;
      renewal_end?: string;
      contract_version?: number;
    };
  } | null;
  outcome: {
    status: string;
    verified?: boolean;
    amount?: string;
    currency?: string;
    effect?: string;
    reason?: string;
    note?: string;
  } | null;
  delivery?: {
    status: string;
    deliveries: number;
    last_error: string | null;
  } | null;
  events: {
    id: number;
    kind: string;
    created_at: string;
    payload: Record<string, unknown>;
  }[];
};
type Preview = {
  amount: string;
  currency: string;
  credit_fraction?: string;
  discount_fraction?: string;
  before: Terms;
  after: Terms;
  approval_version: number;
};
const terminal = new Set([
  "completed",
  "rejected",
  "cancelled",
  "failed",
  "partial",
]);
const running = new Set([
  "queued",
  "running",
  "approved",
  "verifying",
  "retry_scheduled",
]);

export function WorkflowPanel({
  token,
  lang,
  actorId,
  ticketId,
  ticketVersion,
  people,
  kind = "sla",
  canSubmit = true,
}: {
  token: string;
  kind?: "sla" | "deal";
  canSubmit?: boolean;
  lang: Lang;
  actorId: string;
  ticketId: string;
  ticketVersion: number;
  people: { id: string; name: { en: string; zh: string } }[];
}) {
  const t = (en: string, zh: string) => (lang === "zh" ? zh : en);
  const deal = kind === "deal";
  const base = deal ? "/v1/deal-workflows" : "/v1/sla-workflows";
  const subjectKey = deal ? "deal_id" : "ticket_id";
  const modificationKey = deal ? "discount_fraction" : "credit_fraction";
  const workflowTitle = deal
    ? t("Deal workflow", "交易工作流")
    : t("SLA workflow", "SLA 工作流");
  const changeValue = (terms: Terms) =>
    deal ? terms.monthly_fee : terms.sandbox_period_total;
  const [rows, setRows] = useState<Workflow[]>([]);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [percent, setPercent] = useState("100");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [live, setLive] = useState(false);
  const [ready, setReady] = useState(false);
  const controller = useRef(new AbortController());
  const poll = useRef(0);
  const startKey = useRef(crypto.randomUUID());
  const decisionKey = useRef<{ signature: string; key: string } | null>(null);
  const workflow = rows.find((w) => w.id === selected) || rows[0];
  const fraction = Number.isFinite(Number(percent))
    ? (Number(percent) / 100).toFixed(4)
    : "";
  const name = (id: string | null) =>
    people.find((p) => p.id === id)?.name[lang] ||
    id ||
    t("Reviewer needed", "需要审核人");
  const status = (value: string) =>
    ({
      queued: t("Queued", "已排队"),
      running: t("Investigating", "调查中"),
      waiting_for_input: t("Needs case information", "需要案例信息"),
      waiting_for_approval: t("Waiting for review", "等待审核"),
      approved: t(
        "Approved; recording sandbox result",
        "已批准；正在记录沙盒结果",
      ),
      verifying: t("Verifying receipt", "核验回执中"),
      completed: t("Completed and verified", "已完成并核验"),
      rejected: t("Rejected", "已拒绝"),
      cancelled: t("Cancelled", "已取消"),
      failed: t("Stopped", "已停止"),
      partial: t("Needs reconciliation", "需要核对"),
      retry_scheduled: t("Retry scheduled", "等待重试"),
    })[value] || value;

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
      throw Error(
        typeof data.detail === "string"
          ? data.detail
          : t("Check the decision fields.", "请检查审核字段。"),
      );
    }
    return response.json();
  }
  async function refresh() {
    const generation = ++poll.current;
    const result = await api<{ workflows: Workflow[] }>(
      `${base}?${subjectKey}=${encodeURIComponent(ticketId)}`,
    );
    if (generation === poll.current) {
      setRows(result.workflows);
      setReady(true);
    }
  }
  async function run(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await operation();
      await refresh();
    } catch (err) {
      if (!controller.current.signal.aborted) {
        setError(err instanceof Error ? err.message : String(err));
        await refresh().catch(() => {});
      }
    } finally {
      if (!controller.current.signal.aborted) setBusy(false);
    }
  }
  useEffect(() => {
    void refresh().catch((err) => {
      if (!controller.current.signal.aborted) setError(String(err));
    });
    const timer = setInterval(() => {
      void refresh().catch(() => {});
    }, 10000);
    return () => {
      clearInterval(timer);
      controller.current.abort();
    };
  }, []);
  useEffect(() => {
    setPreview(null);
    setReason("");
    setPercent(
      deal
        ? String(
            Number(workflow?.approval?.payload?.maximum_discount || 0) * 100,
          )
        : "100",
    );
    decisionKey.current = null;
  }, [workflow?.id, workflow?.approval?.id]);
  useEffect(() => {
    if (!workflow?.id) return;
    const ac = new AbortController();
    let cursor = 0;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    const changed = () => {
      if (!refreshTimer)
        refreshTimer = setTimeout(() => {
          refreshTimer = undefined;
          void refresh().catch(() => {});
        }, 100);
    };
    const watch = async () => {
      while (!ac.signal.aborted) {
        try {
          const response = await fetch(
            `${base}/${workflow.id}/events?after=${cursor}`,
            {
              headers: { Authorization: `Bearer ${token}` },
              signal: ac.signal,
            },
          );
          if ([401, 403, 404].includes(response.status)) {
            setRows([]);
            setLive(false);
            return;
          }
          if (!response.ok || !response.body)
            throw new Error("Progress unavailable");
          setLive(true);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          try {
            while (!ac.signal.aborted) {
              const { done, value } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              let end;
              while ((end = buffer.indexOf("\n\n")) >= 0) {
                const message = buffer.slice(0, end);
                buffer = buffer.slice(end + 2);
                const id = message.match(/^id: (\d+)$/m);
                if (id) cursor = Number(id[1]);
                if (message.includes("event: revoked")) {
                  setLive(false);
                  setRows([]);
                  return;
                }
                if (message.includes("event: progress")) changed();
                if (message.includes("event: closed")) {
                  changed();
                  setLive(false);
                  return;
                }
              }
            }
          } finally {
            await reader.cancel().catch(() => {});
          }
        } catch {
          if (ac.signal.aborted) return;
        }
        setLive(false);
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    };
    void watch();
    return () => {
      ac.abort();
      setLive(false);
      if (refreshTimer) clearTimeout(refreshTimer);
    };
  }, [workflow?.id]);
  async function start() {
    await run(async () => {
      const row = await api<Workflow>(base, "POST", {
        [subjectKey]: ticketId,
        [deal ? "expected_deal_version" : "expected_ticket_version"]:
          ticketVersion,
        request_key: startKey.current,
        language: lang,
      });
      setSelected(row.id);
      startKey.current = crypto.randomUUID();
    });
  }
  async function decide(decision: "approve" | "modify" | "reject") {
    if (!workflow?.approval) return;
    const body = {
      approval_id: workflow.approval.id,
      expected_version: workflow.approval.version,
      decision,
      reason,
      ...(decision === "modify" ? { [modificationKey]: fraction } : {}),
    };
    const signature = JSON.stringify(body) + workflow.id;
    if (decisionKey.current?.signature !== signature)
      decisionKey.current = { signature, key: crypto.randomUUID() };
    await run(async () => {
      await api(`${base}/${workflow.id}/decision`, "POST", {
        ...body,
        request_key: decisionKey.current!.key,
      });
      setPreview(null);
    });
  }
  const canStart =
    ready &&
    canSubmit &&
    !rows.some((w) => !terminal.has(w.status)) &&
    !rows.some(
      (w) =>
        w.receipt &&
        ["sandbox_credit_recorded", "sandbox_contract_updated"].includes(
          w.receipt.result.effect,
        ),
    );
  const canReview =
    workflow?.status === "waiting_for_approval" &&
    workflow.reviewer_id === actorId &&
    workflow.employee_id !== actorId;
  const stale = workflow?.approval?.stale ?? false;
  // Decimal response text may preserve trailing zeros; compare the fraction numerically (never money).
  const previewMatches =
    preview &&
    Number(preview[modificationKey]) === Number(fraction) &&
    preview.approval_version === workflow?.approval?.version;
  return (
    <section className="case-detail workflow-panel" aria-label={workflowTitle}>
      <div className="case-section-heading">
        <div>
          <div className="eyebrow">
            {t("REVIEW & VERIFIED CLOSURE", "审核与核验闭环")}
          </div>
          <h2>{workflowTitle}</h2>
          <p>
            {t(
              deal
                ? "Submit the renewal for independent review. Approved terms update only the sandbox contract."
                : "Submit the case for independent review. Approved credits are recorded in the local sandbox ledger.",
              deal
                ? "提交续约供独立审核。批准的条款仅更新沙盒合同。"
                : "提交案例供独立审核。已批准的抵扣款将记录在本地沙盒账本中。",
            )}
          </p>
        </div>
        <button
          className="primary-button"
          disabled={busy || !canStart}
          onClick={() => void start()}
        >
          {deal
            ? t("Start deal review", "启动交易审核")
            : t("Start SLA review", "启动 SLA 审核")}
        </button>
      </div>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {!workflow && ready && (
        <p>
          {t(
            "No workflow started for this case yet.",
            "此案例尚未启动工作流。",
          )}
        </p>
      )}
      {workflow && (
        <>
          <p className="progress-connection">
            {live
              ? t("Live progress connected", "实时进度已连接")
              : t(
                  "Saved progress · reconnects automatically",
                  "已保存进度 · 自动重连",
                )}
          </p>
          <div className="workflow-heading">
            <span
              className={`workflow-status ${workflow.status}`}
              role="status"
            >
              {status(workflow.status)}
            </span>
            <label>
              {t("Saved workflows", "已保存的工作流")}
              <select
                aria-label={t("Saved workflows", "已保存的工作流")}
                value={workflow.id}
                disabled={busy}
                onChange={(e) => setSelected(e.target.value)}
              >
                {rows.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.id.slice(0, 8)} · {status(w.status)}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="secondary-button"
              disabled={busy}
              onClick={() => void run(refresh)}
            >
              {t("Refresh progress", "刷新进度")}
            </button>
          </div>
          <p>
            {t("Requester", "申请人")}: {name(workflow.employee_id)} ·{" "}
            {t("Reviewer", "审核人")}: {name(workflow.reviewer_id)} ·{" "}
            {t("Proposal", "方案")}: {workflow.proposal_revision}
          </p>
          {workflow.status === "waiting_for_input" && (
            <div className="workflow-callout">
              <h3>
                {t("Information needed before review", "审核前需要补充信息")}
              </h3>
              <ul>
                {workflow.missing_inputs.map((x) => (
                  <li key={x}>{x.replaceAll("_", " ")}</li>
                ))}
              </ul>
              <p>
                {t(
                  deal
                    ? "Update the deal request or ask Operations to attest missing policy costs, then recheck. Policy exceptions require corrected terms and fresh review."
                    : "Update the incident facts above, reload the case, then recheck this workflow. Changed proposals require a new review.",
                  deal
                    ? "更新交易请求，或请运营人员确认缺失成本，然后重新检查。超出策略的条款必须修改并重新审核。"
                    : "请在上方更新事件事实并刷新案例，然后重新检查此工作流。方案变更后需要重新审核。",
                )}
              </p>
            </div>
          )}
          {workflow.delivery?.status === "dead" && (
            <p role="alert" className="error-banner">
              {t(
                "Delivery stopped after repeated worker failures. The requester can recover this workflow after the cause is fixed; recorded effects are preserved.",
                "工作进程连续失败，任务派发已停止。修复后申请人可恢复工作流；已记录的操作将保留。",
              )}
            </p>
          )}
          {workflow.error && (
            <p className="workflow-callout">{workflow.error.message}</p>
          )}
          {workflow.approval && (
            <div className="approval-card">
              <h3>
                {deal
                  ? t("Sandbox contract approval", "沙盒合同审批")
                  : t("Sandbox credit approval", "沙盒抵扣款审批")}
              </h3>
              {workflow.approval.payload ? (
                <>
                  <div className="proposal-amount">
                    {workflow.approval.payload.currency}{" "}
                    {workflow.approval.payload.amount}
                  </div>
                  <p>
                    {deal
                      ? t("Sandbox monthly fee", "沙盒月费")
                      : t("Monthly sandbox total", "本月沙盒累计金额")}
                    : {changeValue(workflow.approval.payload.before)} →{" "}
                    {changeValue(workflow.approval.payload.after)}
                  </p>
                  {deal && (
                    <p>
                      {t("Renewal end", "续约到期日")}:{" "}
                      {workflow.approval.payload.before.ends_at?.slice(0, 10)} →{" "}
                      {workflow.approval.payload.after.ends_at?.slice(0, 10)} ·{" "}
                      {t("Margin", "毛利率")}:{" "}
                      {Number(workflow.approval.payload.margin_fraction) * 100}%
                    </p>
                  )}
                  <p>
                    {t("Valid until", "有效期至")}:{" "}
                    {new Date(workflow.approval.expires_at).toLocaleString(
                      lang === "zh" ? "zh-CN" : "en-US",
                    )}
                  </p>
                  <div className="source-chips">
                    {workflow.approval.payload.sources.map((s) => (
                      <span key={s.type}>
                        {s.type}: {s.id} · v{s.version}
                      </span>
                    ))}
                  </div>
                </>
              ) : (
                <p>
                  {t(
                    terminal.has(workflow.status)
                      ? "Review archived. The recorded decision and receipt remain below."
                      : "The source snapshot has changed. Review the current case before preparing another proposal.",
                    terminal.has(workflow.status)
                      ? "审核已归档。以下保留已记录的决定与回执。"
                      : "来源快照已改变。请检查当前案例后再准备新方案。",
                  )}
                </p>
              )}
              {stale && workflow.status === "waiting_for_approval" && (
                <p className="error-banner">
                  {t(
                    "This approval is stale or expired. Recheck the workflow to prepare a current proposal.",
                    "此审批已过期或来源已变更。请重新检查工作流以准备新方案。",
                  )}
                </p>
              )}
              {canReview && (
                <fieldset disabled={busy}>
                  <label>
                    {t("Review reason or feedback", "审核原因或反馈")}
                    <textarea
                      aria-label={t(
                        "Review reason or feedback",
                        "审核原因或反馈",
                      )}
                      maxLength={2000}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder={t(
                        "Required for modification or rejection",
                        "修改或拒绝时必填",
                      )}
                    />
                  </label>
                  <div className="case-actions">
                    <button
                      className="primary-button"
                      disabled={stale}
                      onClick={() => void decide("approve")}
                    >
                      {deal
                        ? t("Approve sandbox contract", "批准沙盒合同")
                        : t("Approve sandbox credit", "批准沙盒抵扣款")}
                    </button>
                    <button
                      className="secondary-button"
                      disabled={!reason.trim()}
                      onClick={() => void decide("reject")}
                    >
                      {t("Reject with reason", "拒绝并说明原因")}
                    </button>
                  </div>
                  <div className="approval-modify">
                    <label>
                      {t(
                        deal
                          ? "Discount percentage to approve"
                          : "Credit percentage to approve",
                        deal ? "批准折扣比例（%）" : "批准抵扣款比例（%）",
                      )}
                      <input
                        aria-label={t(
                          deal
                            ? "Discount percentage to approve"
                            : "Credit percentage to approve",
                          deal ? "批准折扣比例（%）" : "批准抵扣款比例（%）",
                        )}
                        type="number"
                        min="0"
                        max="100"
                        step="0.01"
                        value={percent}
                        onChange={(e) => {
                          setPercent(e.target.value);
                          setPreview(null);
                        }}
                      />
                    </label>
                    <button
                      className="secondary-button"
                      disabled={
                        stale ||
                        percent === "" ||
                        Number(percent) < 0 ||
                        Number(percent) > 100
                      }
                      onClick={() =>
                        void run(async () =>
                          setPreview(
                            await api<Preview>(
                              `${base}/${workflow.id}/preview`,
                              "POST",
                              {
                                approval_id: workflow.approval!.id,
                                expected_version: workflow.approval!.version,
                                [modificationKey]: fraction,
                              },
                            ),
                          ),
                        )
                      }
                    >
                      {deal
                        ? t("Preview modified terms", "预览修改后的条款")
                        : t("Preview modified credit", "预览修改后的抵扣款")}
                    </button>
                    {preview && (
                      <p className="credit-preview">
                        {deal
                          ? t("Modified monthly fee", "修改后的月费")
                          : t("Modified credit", "修改后的抵扣款")}
                        :{" "}
                        <strong>
                          {preview.currency} {preview.amount}
                        </strong>{" "}
                        ·{" "}
                        {deal
                          ? t("Monthly fee", "月费")
                          : t("Monthly total", "本月累计")}
                        : {changeValue(preview.before)} →{" "}
                        {changeValue(preview.after)}
                      </p>
                    )}
                    <button
                      className="primary-button"
                      disabled={stale || !previewMatches || !reason.trim()}
                      onClick={() => void decide("modify")}
                    >
                      {t("Modify and approve", "修改并批准")}
                    </button>
                  </div>
                </fieldset>
              )}
              {workflow.approval.reason && (
                <p className="review-feedback">
                  {t("Reviewer feedback", "审核人反馈")}:{" "}
                  {workflow.approval.reason}
                </p>
              )}
              {workflow.approval.decision && (
                <p>
                  {t("Decision", "决定")}: {workflow.approval.decision}
                </p>
              )}
            </div>
          )}
          {workflow.memo && (
            <details>
              <summary>
                {t("Read the review memo", "阅读审核备忘录")} · v
                {workflow.memo.version}
              </summary>
              <pre>{workflow.memo.content}</pre>
            </details>
          )}
          {workflow.receipt && (
            <div className="receipt-card">
              <h3>{t("Recorded sandbox receipt", "已记录的沙盒回执")}</h3>
              <strong>
                {workflow.receipt.result.currency}{" "}
                {workflow.receipt.result.amount}
              </strong>
              <p>
                {t("Operation", "操作编号")}: {workflow.receipt.operation_id}
              </p>
              <p>
                {workflow.outcome?.verified
                  ? t(
                      deal
                        ? "Verified against the sandbox contract. Workflow complete."
                        : "Verified in the ledger. Workflow complete.",
                      deal
                        ? "已核验沙盒合同。工作流已完成。"
                        : "已在账本中核验。工作流已完成。",
                    )
                  : t(
                      "Effect recorded; verification is pending or requires reconciliation.",
                      "操作已记录；正在等待核验或需要人工核对。",
                    )}
              </p>
              {deal && (
                <p>
                  {t("Contract version", "合同版本")}:{" "}
                  {workflow.receipt.result.contract_version} ·{" "}
                  {t("Renewal end", "续约到期日")}:{" "}
                  {workflow.receipt.result.renewal_end?.slice(0, 10)}
                </p>
              )}
              <small>
                {t(
                  deal
                    ? "No real CRM update, signed amendment or customer message was sent."
                    : "No customer message was sent. Technical incident resolution is tracked separately.",
                  deal
                    ? "未修改真实 CRM、未签署合同或发送客户消息。"
                    : "未发送客户消息。技术事件的解决情况单独跟踪。",
                )}
              </small>
            </div>
          )}
          {workflow.employee_id === actorId && (
            <div className="case-actions">
              {([
                "waiting_for_input",
                "queued",
                "running",
                "approved",
                "verifying",
                "retry_scheduled",
              ].includes(workflow.status) ||
                (workflow.status === "waiting_for_approval" && stale)) && (
                <button
                  className="primary-button"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(`${base}/${workflow.id}/resume`, "POST", {
                        expected_version: workflow.version,
                      });
                    })
                  }
                >
                  {workflow.status === "waiting_for_input" || stale
                    ? t("Recheck case and continue", "重新检查案例并继续")
                    : t("Recover workflow", "恢复工作流")}
                </button>
              )}
              {!terminal.has(workflow.status) && !workflow.receipt && (
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(`${base}/${workflow.id}/cancel`, "POST", {
                        expected_version: workflow.version,
                      });
                    })
                  }
                >
                  {t("Cancel workflow", "取消工作流")}
                </button>
              )}
            </div>
          )}
          <details
            className="workflow-progress"
            open={running.has(workflow.status)}
          >
            <summary>
              {t("Workflow progress", "工作流进度")} · {workflow.events.length}
            </summary>
            <ol>
              {workflow.events
                .filter((e) => e.kind !== "attempt_finished")
                .map((e) => (
                  <li key={e.id}>
                    <strong>{e.kind.replaceAll("_", " ")}</strong>
                    <time>
                      {new Date(e.created_at).toLocaleTimeString(
                        lang === "zh" ? "zh-CN" : "en-US",
                      )}
                    </time>
                    {e.kind === "rejected" && (
                      <p>{String(e.payload.reason || "")}</p>
                    )}
                  </li>
                ))}
            </ol>
          </details>
        </>
      )}
    </section>
  );
}
