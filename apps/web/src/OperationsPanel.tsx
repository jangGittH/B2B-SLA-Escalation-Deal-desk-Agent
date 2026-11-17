import { useEffect, useState } from "react";
type Summary = {
  scope: string;
  window_days: number;
  as_of: string;
  outcomes: { kind: string; status: string; count: number }[];
  queue: Record<string, number>;
  limits: Record<string, number>;
  timing: {
    covered_workflows: number;
    system_elapsed_p50_ms: number | null;
    system_elapsed_p95_ms: number | null;
    human_wait_total_ms: number;
    system_elapsed_total_ms: number;
    delivery_attempts: number;
    queue_wait_p95_ms: number | null;
    worker_active_total_ms: number;
  };
  model: {
    calls: number;
    failed: number;
    known_tokens: { prompt: number; output: number; total: number };
    unknown_usage_calls: number;
    cost_usd: string | null;
    pricing_version: string | null;
    unknown_cost_calls?: number;
  };
  agents?: { plan_revisions: number; invalidations: number; stopped: number; claim_checks: number; supported_checks: number; rejected_drafts: number };
  business?: { approved_decisions: number; modified_approvals: number; draft_ready_samples: number; draft_ready_p50_ms: number | null };
  alerts: {
    dead_deliveries: number;
    expired_reviews: number;
    oldest_pending_seconds: number;
    worker_seen_recently: boolean;
  };
  recent: {
    id: string;
    ticket_id: string | null;
    deal_id: string | null;
    status: string;
    conversation_id: string;
    origin_run_id: string | null;
    approval_id: string | null;
    operation_id: string | null;
    delivery_status: string;
    last_error: string | null;
  }[];
};
export function OperationsPanel({
  token,
  lang,
  available,
}: {
  token: string;
  lang: "en" | "zh";
  available: boolean;
}) {
  const t = (en: string, zh: string) => (lang === "zh" ? zh : en);
  const [data, setData] = useState<Summary | null>(null),
    [error, setError] = useState(""),
    [days, setDays] = useState("7");
  useEffect(() => {
    if (!available) return;
    const ac = new AbortController();
    let running = false;
    async function load() {
      if (running) return;
      running = true;
      try {
        const r = await fetch(`/v1/operations?days=${days}`, {
          signal: ac.signal,
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok)
          throw new Error(
            t("Operating summary unavailable", "运行概览暂不可用"),
          );
        setData(await r.json());
        setError("");
      } catch (e) {
        if (!ac.signal.aborted) setError(String(e));
      } finally {
        running = false;
      }
    }
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => {
      ac.abort();
      clearInterval(timer);
    };
  }, [token, lang, days, available]);
  const duration = (ms: number | null) =>
    ms === null
      ? t("Not measured", "未测量")
      : `${(Number(ms) / 1000).toFixed(2)} s`;
  if (!available)
    return (
      <section className="case-panel">
        <h1>{t("Operations", "运行概览")}</h1>
        <p>{t("PostgreSQL mode is required.", "需要 PostgreSQL 模式。")}</p>
      </section>
    );
  return (
    <section className="case-panel">
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            {t("DELIVERY · RECOVERY · OUTCOMES", "派发 · 恢复 · 结果")}
          </div>
          <h1>{t("Operations", "运行概览")}</h1>
          <p>
            {t(
              "Measured SLA and deal workflow outcomes. Human review time is separate from system elapsed time.",
              "SLA 与交易工作流的实际运行结果。人工审核等待时间与系统耗时分开统计。",
            )}
          </p>
        </div>
        <label>
          {t("Reporting window", "统计窗口")}
          <select value={days} onChange={(e) => setDays(e.target.value)}>
            <option value="1">{t("1 day", "1 天")}</option>
            <option value="7">{t("7 days", "7 天")}</option>
            <option value="30">{t("30 days", "30 天")}</option>
          </select>
        </label>
      </div>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {data && (
        <>
          <p className="case-notice">
            {data.scope === "tenant"
              ? t(
                  "Tenant-wide metrics · recent traces include only your own or assigned reviews.",
                  "组织级指标 · 最近记录仅含本人或分配给本人的审核。",
                )
              : t(
                  "Your requested or assigned workflows only.",
                  "仅统计本人申请或负责审核的工作流。",
                )}
          </p>
          <div className="ops-cards">
            <article className="case-detail">
              <h3>
                {t(
                  "Ready / running / stopped deliveries",
                  "待派发 / 执行中 / 已停止",
                )}
              </h3>
              <strong>
                {data.queue.pending || 0} / {data.queue.leased || 0} /{" "}
                {data.queue.dead || 0}
              </strong>
              <p>
                {data.alerts.worker_seen_recently
                  ? t("Worker heartbeat healthy", "工作进程心跳正常")
                  : t("No recent worker heartbeat", "暂无近期工作进程心跳")}
              </p>
              <small>
                {t("Oldest queued", "最早排队任务")}:{" "}
                {Number(data.alerts.oldest_pending_seconds).toFixed(1)} s
              </small>
            </article>
            <article className="case-detail">
              <h3>{t("System elapsed p50 / p95", "系统耗时 p50 / p95")}</h3>
              <strong>
                {duration(data.timing.system_elapsed_p50_ms)} /{" "}
                {duration(data.timing.system_elapsed_p95_ms)}
              </strong>
              <p>
                {t(
                  "Includes queue and retry time; excludes human waits.",
                  "包含排队和重试耗时；不包含人工等待。",
                )}
              </p>
              <small>
                {t("Timing coverage", "计时覆盖")}:{" "}
                {data.timing.covered_workflows}
              </small>
            </article>
            <article className="case-detail">
              <h3>
                {t(
                  "Human wait / active worker time",
                  "人工等待 / 工作进程执行时间",
                )}
              </h3>
              <strong>
                {duration(data.timing.human_wait_total_ms)} /{" "}
                {duration(data.timing.worker_active_total_ms)}
              </strong>
              <p>
                {t(
                  "Cumulative across the reporting window.",
                  "统计窗口内工作流的累计值。",
                )}
              </p>
              <small>
                {t("Queue wait p95", "排队等待 p95")}:{" "}
                {duration(data.timing.queue_wait_p95_ms)}
              </small>
            </article>
          </div>
          {(data.alerts.dead_deliveries > 0 ||
            data.alerts.expired_reviews > 0) && (
            <p role="alert" className="error-banner">
              {t("Needs attention", "需要处理")}: {data.alerts.dead_deliveries}{" "}
              {t("stopped deliveries", "已停止派发")},{" "}
              {data.alerts.expired_reviews} {t("expired reviews", "已过期审核")}
            </p>
          )}
          <section className="case-detail">
            <h2>{t("Workflow outcomes", "工作流结果")}</h2>
            <p>
              {t(
                "All statuses are included, including waiting, cancelled, failed and partial. These sandbox results are not customer ROI.",
                "包含等待、取消、失败和部分完成等全部状态。沙盒结果不代表真实客户收益。",
              )}
            </p>
            <table className="case-table ops-outcomes">
              <thead>
                <tr>
                  <th>{t("Workflow", "工作流")}</th>
                  <th>{t("Status", "状态")}</th>
                  <th>{t("Count", "数量")}</th>
                </tr>
              </thead>
              <tbody>
                {data.outcomes.map((o) => (
                  <tr key={o.kind + o.status}>
                    <td>{o.kind === "sla" ? "SLA" : t("Deal", "交易")}</td>
                    <td>{o.status.replaceAll("_", " ")}</td>
                    <td>{o.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.outcomes.length && (
              <p>{t("No workflows in this window.", "此窗口内暂无工作流。")}</p>
            )}
          </section>
          <section className="case-detail">
            <h2>
              {t("Saved conversation model usage", "已保存对话的模型用量")}
            </h2>
            <p>
              {t("Model calls / failures", "模型调用 / 失败")}:{" "}
              {data.model.calls} / {data.model.failed}
            </p>
            <p>
              {t("Reported tokens (total)", "已报告 Token（总计）")}:{" "}
              {data.model.known_tokens.total} ·{" "}
              {t("Calls with unknown usage", "用量未知的调用")}:{" "}
              {data.model.unknown_usage_calls}
            </p>
            <p>
              {data.model.cost_usd === null
                ? t("Inference cost: unknown — usage or current pricing is incomplete.", "推理成本：未知 — 用量或当前价格信息不完整。")
                : `${t("Estimated paid list cost", "按付费标价估算")}: USD ${data.model.cost_usd} · ${data.model.pricing_version}`}
            </p>
            {data.agents && <p>{t("Plan revisions / replans", "计划修订 / 重新规划")}: {data.agents.plan_revisions} / {data.agents.invalidations} · {t("Supported / checked claims", "有支持 / 已检查的陈述")}: {data.agents.supported_checks} / {data.agents.claim_checks} · {t("Rejected drafts", "退回草稿")}: {data.agents.rejected_drafts}</p>}
            {data.business && <p>{t("Edited / approved decisions", "修改后批准 / 批准的决策")}: {data.business.modified_approvals} / {data.business.approved_decisions} · {t("Draft-ready p50", "草稿就绪 p50")}: {duration(data.business.draft_ready_p50_ms)} ({data.business.draft_ready_samples} {t("observations, including revisions", "次观测，含修订")})</p>}
          </section>
          <section className="case-detail">
            <h2>{t("Recent workflow traces", "最近工作流记录")}</h2>
            {data.recent.map((r) => (
              <details className="ops-trace" key={r.id}>
                <summary>
                  {r.ticket_id || r.deal_id} · {r.status.replaceAll("_", " ")} ·{" "}
                  {r.id.slice(0, 8)}
                </summary>
                <dl>
                  <dt>{t("Workflow", "工作流")}</dt>
                  <dd>{r.id}</dd>
                  <dt>
                    {t("Conversation / originating run", "对话 / 发起运行")}
                  </dt>
                  <dd>
                    {r.conversation_id} / {r.origin_run_id || "—"}
                  </dd>
                  <dt>{t("Approval / operation", "审批 / 操作")}</dt>
                  <dd>
                    {r.approval_id || "—"} / {r.operation_id || "—"}
                  </dd>
                  <dt>{t("Delivery", "派发")}</dt>
                  <dd>
                    {r.delivery_status || "—"} {r.last_error || ""}
                  </dd>
                </dl>
              </details>
            ))}
          </section>
          <details className="case-detail">
            <summary>{t("Current operating limits", "当前运行限制")}</summary>
            <p>
              {t(
                "Active workflows per tenant / total",
                "每个组织 / 全部活跃工作流",
              )}
              : {data.limits.active_per_tenant} / {data.limits.active_total}
            </p>
            <p>
              {t(
                "Worker slots per tenant / total",
                "每个组织 / 全部工作进程槽位",
              )}
              : {data.limits.workers_per_tenant} / {data.limits.workers_total}
            </p>
            <p>
              {t(
                "Model requests per minute per tenant / total",
                "每个组织 / 全部模型每分钟请求",
              )}
              : {data.limits.provider_tenant_rpm} / {data.limits.provider_rpm}
            </p>
          </details>
        </>
      )}
    </section>
  );
}
