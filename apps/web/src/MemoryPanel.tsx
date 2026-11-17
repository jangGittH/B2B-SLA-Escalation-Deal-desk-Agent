import { useEffect, useState } from "react";
type Memory = {
  id: string;
  kind: string;
  key?: string;
  scope: string;
  value?: string;
  status: string;
  version: number;
  stale: boolean;
  usable: boolean;
  expires_at: number;
  confirmed_by?: string;
  sources?: { id: string; version: number }[];
};
export function MemoryPanel({
  token,
  lang,
}: {
  token: string;
  lang: "en" | "zh";
}) {
  const t = (en: string, zh: string) => (lang === "zh" ? zh : en);
  const [rows, setRows] = useState<Memory[]>([]),
    [value, setValue] = useState("overview_first"),
    [scope, setScope] = useState("user"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function load(signal?: AbortSignal) {
    const r = await fetch("/v1/memories", {
      headers: { Authorization: `Bearer ${token}` },
      signal,
    });
    if (!r.ok) throw new Error(t("Memory unavailable", "记忆暂不可用"));
    setRows((await r.json()).memories);
  }
  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal).catch((e) => {
      if (!ac.signal.aborted) setError(String(e));
    });
    return () => ac.abort();
  }, [token]);
  async function act(path: string, method: string, body: unknown) {
    setBusy(true);
    setError("");
    try {
      const r = await fetch(path, {
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok)
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : t("Memory change rejected", "记忆修改被拒绝"),
        );
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const label = (v?: string) =>
    v === "evidence_first"
      ? t("Evidence first", "证据优先")
      : v === "overview_first"
        ? t("Overview first", "概览优先")
        : v;
  return (
    <details className="case-detail memory-panel">
      <summary>{t("Preferences and memory", "偏好与记忆")}</summary>
      <p>
        {t(
          "Confirm a layout preference for future incident memo exports. Memories never change policies, facts or approval. Proposed memories stay inactive until reviewed.",
          "确认后，版式偏好将应用于今后的事件备忘录导出。记忆不会改变政策、事实或审批。提议的记忆须审核后才生效。",
        )}
      </p>
      <div className="governance-form">
        <label>
          {t("Memo layout", "备忘录版式")}
          <select value={value} onChange={(e) => setValue(e.target.value)}>
            <option value="overview_first">
              {t("Overview first", "概览优先")}
            </option>
            <option value="evidence_first">
              {t("Evidence first", "证据优先")}
            </option>
          </select>
        </label>
        <label>
          {t("Memory scope", "记忆范围")}
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="user">{t("My preference", "本人偏好")}</option>
            <option value="tenant">
              {t(
                "Organization default (Operations review)",
                "组织默认（运营审核）",
              )}
            </option>
          </select>
        </label>
        <button
          className="primary-button"
          disabled={busy}
          onClick={() => void act("/v1/memories", "POST", { value, scope })}
        >
          {t("Propose preference", "提议偏好")}
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {rows.length === 0 && <p>{t("No saved memories.", "暂无记忆。")}</p>}
      {rows.map((r) => (
        <article className="memory-card" key={r.id}>
          <strong>
            {r.stale
              ? t(
                  "Expired or source changed — unavailable",
                  "已过期或来源已更改，不可使用",
                )
              : label(r.value)}
          </strong>
          <p>
            {r.scope} · {r.status} · v{r.version} · {t("Expires", "有效期至")}{" "}
            {new Date(r.expires_at * 1000).toLocaleDateString()}
          </p>
          {r.sources?.map((s) => (
            <small key={s.id}>
              {s.id} / v{s.version}
            </small>
          ))}
          <div className="case-actions">
            {!r.stale && r.status === "pending" && (
              <button
                className="primary-button"
                disabled={busy}
                onClick={() =>
                  void act(`/v1/memories/${r.id}`, "PATCH", {
                    expected_version: r.version,
                    action: "confirm",
                  })
                }
              >
                {t("Confirm memory", "确认记忆")}
              </button>
            )}
            {!r.stale && r.kind === "preference" && (
              <button
                className="secondary-button"
                disabled={busy}
                onClick={() =>
                  void act(`/v1/memories/${r.id}`, "PATCH", {
                    expected_version: r.version,
                    action: "edit",
                    value,
                  })
                }
              >
                {t("Replace with selected layout", "改为所选版式")}
              </button>
            )}
            <button
              className="secondary-button"
              disabled={busy}
              onClick={() =>
                void act(`/v1/memories/${r.id}`, "PATCH", {
                  expected_version: r.version,
                  action: "delete",
                })
              }
            >
              {t("Delete memory", "删除记忆")}
            </button>
          </div>
        </article>
      ))}
    </details>
  );
}
