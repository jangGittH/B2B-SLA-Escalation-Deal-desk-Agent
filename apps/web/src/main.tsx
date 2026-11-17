import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUp,
  ArrowUpRight,
  Search,
  Sparkles,
  BookOpen,
  FileText,
  ChevronDown,
  ChevronRight,
  Plus,
  X,
  Check,
  CheckCheck,
  Globe2,
  ShieldCheck,
  PanelLeftClose,
  PanelLeftOpen,
  Layers,
  LockKeyhole,
  LoaderCircle,
  AlertCircle,
  Copy,
  ArrowRight,
} from "lucide-react";
import "./style.css";
import { MemoryPanel } from "./MemoryPanel";
import { KnowledgeControls } from "./KnowledgeControls";
import { OperationsPanel } from "./OperationsPanel";
import { DealsPanel } from "./DealsPanel";
import { CasesPanel } from "./CasesPanel";

type Lang = "en" | "zh";
type Page = "assistant" | "knowledge" | "cases" | "deals" | "operations";
type Doc = {
  id: string;
  title: string;
  content: string;
  version: number;
  updated_at: string;
  project_id: string | null;
  source_url: string;
};
type Trace = { tool: string; outcome: string; document_ids: string[]; role?: string; node_id?: string; plan_revision?: number };
type Artifact = { id: string; filename: string; size_bytes: number };
type Answer = {
  files?: Artifact[];
  status: string;
  answer: string;
  sources: Doc[];
  tool_trace: Trace[];
  model_calls: number;
  tool_calls: number;
  agent_mode?: string;
  plan?: { revision: number; nodes: { id: string; role: string; dependencies: string[]; status: string }[] };
  verification?: { checked_claims: number; supported_claims: number; repairs: number };
  budget?: { replans: number; elapsed_ms: number };
};
type SavedConversation = { id: string; title: string };
type SavedRun = {
  id: string;
  status: string;
  request: { question: string };
  result: Answer | null;
  error?: string;
  files?: Artifact[];
};
type Entry = {
  id: number | string;
  question: string;
  result?: Answer;
  error?: string;
  files?: Artifact[];
};
type Employee = {
  id: string;
  name: { en: string; zh: string };
  organization_id: string;
};
type Workspace = {
  identity: { id: string; name: string; organization_id: string };
  organization: string;
  documents: Doc[];
  projects: { id: string; name: string }[];
  agent_available: boolean;
  persistence_available?: boolean;
  cases_available?: boolean;
};

function App() {
  const [lang, setLang] = useState<Lang>(() =>
    localStorage.getItem("northstar-language") === "zh" ? "zh" : "en",
  );
  const t = (en: string, zh: string) => (lang === "zh" ? zh : en);
  const [page, setPage] = useState<Page>("assistant"),
    [nav, setNav] = useState(false);
  const [config, setConfig] = useState<{
    demo_mode: boolean;
    employees: Employee[];
  } | null>(null);
  const [authVersion, setAuthVersion] = useState(0);
  const [token, setToken] = useState(""),
    [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [question, setQuestion] = useState(""),
    [messages, setMessages] = useState<Entry[]>([]),
    [busy, setBusy] = useState(false);
  const [query, setQuery] = useState(""),
    [hits, setHits] = useState<Doc[] | null>(null),
    [searching, setSearching] = useState(false),
    [project, setProject] = useState("all");
  const [document, setDocument] = useState<Doc | null>(null),
    [login, setLogin] = useState(false),
    [inputToken, setInputToken] = useState("");
  const [conversations, setConversations] = useState<SavedConversation[]>([]);
  const [historyReady, setHistoryReady] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [conversationId, setConversationId] = useState("");
  const historyVersion = useRef(0);
  const pendingTurn = useRef<{
    question: string;
    conversationId: string;
    mode: string;
    key: string;
  } | null>(null);
  const [copied, setCopied] = useState(false);
  const [agentMode, setAgentMode] = useState<"supervisor" | "single">("supervisor");
  const searchVersion = useRef(0),
    documentVersion = useRef(0);
  const epoch = useRef(0),
    requests = useRef(new Set<AbortController>()),
    end = useRef<HTMLDivElement>(null);
  const docs = workspace?.documents || [];
  const filteredDocs = (hits || docs).filter(
    (d) =>
      project === "all" ||
      (project === "company" ? !d.project_id : d.project_id === project),
  );
  const title = {
    assistant: t("Case assistant", "案例助手"),
    cases: t("SLA cases", "SLA 案例"),
    deals: t("Deal desk", "交易审核"),
    operations: t("Operations", "运行概览"),
    knowledge: t("Policies & evidence", "政策与证据"),
  }[page];
  function reset() {
    epoch.current++;
    for (const r of requests.current) r.abort();
    requests.current.clear();
    setWorkspace(null);
    setDocument(null);
    setMessages([]);
    setConversationId("");
    setConversations([]);
    setHistoryReady(false);
    setHistoryLoading(false);
    historyVersion.current++;
    pendingTurn.current = null;
    setHits(null);
    setQuery("");
    setProject("all");
    setError("");
    setBusy(false);
    setSearching(false);
    setQuestion("");
  }
  async function api<T>(
    path: string,
    options: RequestInit = {},
    credential = token,
    timeoutMs = 100000,
  ): Promise<T> {
    const controller = new AbortController();
    requests.current.add(controller);
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(path, {
        ...options,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...(credential ? { Authorization: `Bearer ${credential}` } : {}),
        },
      });
      const body = await response.json();
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? t(
                "Your session expired. Choose an employee or reconnect.",
                "会话已过期，请重新选择员工或连接。",
              )
            : response.status === 429
              ? t(
                  "The assistant is busy. Please try again shortly.",
                  "助手当前繁忙，请稍后重试。",
                )
              : response.status === 503
                ? t(
                    "The assistant is unavailable. Knowledge search is still available; check the server configuration.",
                    "助手暂不可用，仍可使用知识搜索；请检查服务配置。",
                  )
                : body.detail ||
                  t(
                    "Something went wrong. Please try again.",
                    "请求失败，请重试。",
                  ),
        );
      return body;
    } finally {
      clearTimeout(timer);
      requests.current.delete(controller);
    }
  }
  const failure = (err: unknown) =>
    err instanceof DOMException && err.name === "AbortError"
      ? t("The request timed out. Please try again.", "请求超时，请重试。")
      : err instanceof Error
        ? err.message
        : t("Unable to connect.", "无法连接。");
  async function chooseEmployee(id: string) {
    reset();
    setToken("");
    setLoading(true);
    const current = epoch.current;
    try {
      const session = await api<{ token: string }>(
        "/v1/demo/session",
        { method: "POST", body: JSON.stringify({ employee_id: id }) },
        "",
      );
      if (current === epoch.current) setToken(session.token);
    } catch (err) {
      if (current === epoch.current) {
        setError(failure(err));
        setLoading(false);
      }
    }
  }
  useEffect(() => {
    let mounted = true;
    fetch("/v1/ui/config")
      .then((r) => {
        if (!r.ok) throw Error("Server unavailable");
        return r.json();
      })
      .then((data) => {
        if (!mounted) return;
        setConfig(data);
        if (data.demo_mode && data.employees.length)
          void chooseEmployee(data.employees[0].id);
        else {
          setLogin(true);
          setLoading(false);
        }
      })
      .catch(() => {
        if (mounted) {
          setError(
            t(
              "Cannot reach the workspace. Start the demo server and refresh.",
              "无法连接工作空间，请启动演示服务并刷新。",
            ),
          );
          setLoading(false);
        }
      });
    return () => {
      mounted = false;
      for (const r of requests.current) r.abort();
    };
  }, []);
  useEffect(() => {
    localStorage.setItem("northstar-language", lang);
    window.document.documentElement.lang = lang;
    if (!token) return;
    reset();
    setLoading(true);
    const current = epoch.current;
    api<Workspace>(`/v1/workspace?language=${lang}`)
      .then((data) => {
        if (current === epoch.current) setWorkspace(data);
      })
      .catch((err) => {
        if (current === epoch.current) setError(failure(err));
      })
      .finally(() => {
        if (current === epoch.current) setLoading(false);
      });
  }, [token, lang, authVersion]);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, busy]);
  async function loadConversation(id: string) {
    const current = epoch.current;
    const version = ++historyVersion.current;
    setConversationId(id);
    setMessages([]);
    pendingTurn.current = null;
    setHistoryLoading(false);
    if (!id) return;
    setHistoryLoading(true);
    try {
      const data = await api<{ runs: SavedRun[] }>(`/v1/conversations/${id}`, {}, token, 15000);
      if (current === epoch.current && version === historyVersion.current)
        setMessages(
          data.runs.map((run) => ({
            id: run.id,
            question: run.request.question,
            result: run.result || undefined,
            files: run.files,
            error: run.result
              ? undefined
              : run.error ||
                t(
                  `Run ${run.status}. Refresh history to check.`,
                  `运行状态：${run.status}，请刷新历史记录。`,
                ),
          })),
        );
    } catch (err) {
      if (current === epoch.current && version === historyVersion.current) setError(failure(err));
    } finally {
      if (current === epoch.current && version === historyVersion.current)
        setHistoryLoading(false);
    }
  }
  async function refreshHistory(openLatest = false) {
    const current = epoch.current;
    const data = await api<{ conversations: SavedConversation[] }>(
      "/v1/conversations",
      {}, token, 15000,
    );
    if (current !== epoch.current) return;
    setConversations(data.conversations);
    setHistoryReady(true);
    if (openLatest && data.conversations[0])
      await loadConversation(data.conversations[0].id);
  }
  useEffect(() => {
    if (!workspace) return;
    const current = epoch.current;
    if (workspace.persistence_available) {
      void refreshHistory(true)
        .catch((err) => {
          if (current === epoch.current) setError(failure(err));
        })
        .finally(() => {
          if (current === epoch.current) setHistoryReady(true);
        });
    } else setHistoryReady(true);
  }, [workspace?.identity.id, workspace?.persistence_available]);
  function go(next: Page) {
    setPage(next);
    setNav(false);
    setError("");
  }
  async function ask(value = question) {
    if (
      !value.trim() ||
      busy ||
      historyLoading ||
      !historyReady ||
      !workspace ||
      !workspace.agent_available
    )
      return;
    const q = value.trim();
    const current = epoch.current,
      id = Date.now();
    setQuestion("");
    go("assistant");
    setBusy(true);
    setMessages((old) => [...old, { id, question: q }]);
    try {
      let result: Answer;
      if (workspace.persistence_available) {
        let selected = conversationId;
        if (!selected) {
          const created = await api<SavedConversation>("/v1/conversations", {
            method: "POST",
            body: JSON.stringify({ title: q.slice(0, 120), language: lang }),
          });
          if (current !== epoch.current) return;
          selected = created.id;
          setConversations((old) => [created, ...old]);
          setConversationId(selected);
        }
        if (
          !pendingTurn.current ||
          pendingTurn.current.question !== q ||
          pendingTurn.current.mode !== agentMode ||
          pendingTurn.current.conversationId !== selected
        )
          pendingTurn.current = {
            question: q,
            mode: agentMode,
            conversationId: selected,
            key: crypto.randomUUID(),
          };
        const run = await api<SavedRun>(
          `/v1/conversations/${selected}/messages`,
          {
            method: "POST",
            body: JSON.stringify({
              question: q,
              language: lang,
              mode: agentMode,
              request_key: pendingTurn.current.key,
            }),
          },
        );
        if (current !== epoch.current) return;
        pendingTurn.current = null;
        if (!run.result)
          throw Error(
            run.error ||
              t(
                `Run ${run.status}. Refresh history to check.`,
                `运行状态：${run.status}，请刷新历史记录。`,
              ),
          );
        result = run.result;
        await refreshHistory();
      } else {
        result = await api<Answer>("/v1/agent/ask", {
          method: "POST",
          body: JSON.stringify({ question: q, language: lang, mode: agentMode }),
        });
      }
      if (current === epoch.current)
        setMessages((old) =>
          old.map((m) => (m.id === id ? { ...m, result } : m)),
        );
    } catch (err) {
      if (current === epoch.current)
        setMessages((old) =>
          old.map((m) => (m.id === id ? { ...m, error: failure(err) } : m)),
        );
    } finally {
      if (current === epoch.current) setBusy(false);
    }
  }
  async function downloadFile(file: Artifact) {
    const current = epoch.current;
    const controller = new AbortController();
    requests.current.add(controller);
    try {
      const response = await fetch(`/v1/files/${encodeURIComponent(file.id)}`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      });
      if (!response.ok)
        throw Error(t("File download failed.", "文件下载失败。"));
      const blob = await response.blob();
      if (current !== epoch.current) return;
      const url = URL.createObjectURL(blob);
      const link = window.document.createElement("a");
      link.href = url;
      link.download = file.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      if (current === epoch.current) setError(failure(err));
    } finally {
      requests.current.delete(controller);
    }
  }
  async function search(value = query) {
    const version = ++searchVersion.current;
    setQuery(value);
    go("knowledge");
    if (!value.trim()) {
      setHits(null);
      return;
    }
    const current = epoch.current;
    setSearching(true);
    try {
      const result = await api<{ sources?: Doc[]; hits: { document: Doc }[] }>(
        "/v1/knowledge/search",
        {
          method: "POST",
          body: JSON.stringify({ query: value, language: lang, limit: 20 }),
        },
      );
      if (current === epoch.current && version === searchVersion.current)
        setHits(result.hits.map((h) => h.document));
    } catch (err) {
      if (current === epoch.current) setError(failure(err));
    } finally {
      if (current === epoch.current && version === searchVersion.current)
        setSearching(false);
    }
  }
  async function openDoc(id: string) {
    const version = ++documentVersion.current;
    const current = epoch.current;
    try {
      const d = await api<Doc>(
        `/v1/documents/${encodeURIComponent(id)}?language=${lang}`,
      );
      if (current === epoch.current && version === documentVersion.current)
        setDocument(d);
    } catch (err) {
      if (current === epoch.current) setError(failure(err));
    }
  }
  const prompts = [
    {
      icon: BookOpen,
      color: "blue",
      title: t("Investigate an escalation", "调查升级事件"),
      description: t(
        "Read the SLA and incident evidence.",
        "查阅 SLA 与事件证据。",
      ),
      q: t(
        "Investigate Acme INC-104 using the available SLA and incident evidence. What is confirmed and what information is missing?",
        "请根据现有 SLA 和事件证据调查 Acme INC-104。哪些信息已确认，哪些仍缺失？",
      ),
    },
    {
      icon: Layers,
      color: "purple",
      title: t("Review a deal request", "评审商务申请"),
      description: t(
        "Distinguish proposed terms from approvals.",
        "区分建议条款和已批准内容。",
      ),
      q: t(
        "Review the Acme renewal discount request. What evidence and approvals are still needed?",
        "请评审 Acme 续约折扣申请。还需要哪些证据和审批？",
      ),
    },
    {
      icon: FileText,
      color: "green",
      title: t("Draft a case memo", "起草案例备忘录"),
      description: t(
        "Create a private document for review.",
        "生成供审核的私人文档。",
      ),
      q: t(
        "Create a Word incident memo for Acme INC-104 from the documents I can access. Label unknowns and proposed next steps clearly.",
        "请根据我有权访问的文档，为 Acme INC-104 创建 Word 事件备忘录。清楚标注未知事项和建议步骤。",
      ),
    },
  ];
  const composer = (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void ask();
      }}
    >
      <textarea
        aria-label={t("Ask your assistant", "向助手提问")}
        placeholder={t(
          "Ask about an escalation, SLA, or deal request…",
          "询问升级事件、SLA 或商务申请…",
        )}
        maxLength={4000}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void ask();
          }
        }}
      />
      <div className="composer-footer">
        <select aria-label={t("Investigation mode", "调查模式")} value={agentMode} disabled={busy}
          onChange={(e) => setAgentMode(e.target.value as "supervisor" | "single")}>
          <option value="supervisor">{t("Specialist team", "专家协作")}</option>
          <option value="single">{t("Single assistant", "单助手")}</option>
        </select>
        <span>
          <ShieldCheck size={15} />
          {t("Your permitted workspace knowledge", "仅使用你有权访问的知识")}
        </span>
        <button
          className="send"
          type="submit"
          disabled={
            busy ||
            historyLoading ||
            !historyReady ||
            !question.trim() ||
            !workspace ||
            !workspace.agent_available
          }
          aria-label={t("Send question", "发送问题")}
        >
          {busy ? (
            <LoaderCircle className="spin" size={18} />
          ) : (
            <ArrowUp size={20} />
          )}
        </button>
      </div>
    </form>
  );
  const docCard = (d: Doc) => (
    <button key={d.id} className="doc-card" onClick={() => void openDoc(d.id)}>
      <span className="file-icon">
        <FileText size={21} />
      </span>
      <div>
        <strong>{d.title}</strong>
        <p>{d.content}</p>
        <span className="doc-meta">
          {d.project_id
            ? d.project_id.replace("other-", "").toUpperCase()
            : t("Operating policy", "操作政策")}
          <i />v{d.version}
        </span>
      </div>
      <ArrowUpRight size={17} className="muted" />
    </button>
  );
  return (
    <div className="app-shell">
      <aside className={`sidebar ${nav ? "open" : ""}`}>
        <a className="brand" href="/" aria-label="B2B SLA Escalation/Deal desk Agent home">
          <span className="brand-mark">
            <Sparkles size={23} />
          </span>
          <span>
            B2B SLA<span className="brand-sub">Escalation/Deal desk Agent</span>
          </span>
        </a>
        <button className="workspace-switch" onClick={() => setLogin(true)}>
          <span className="org-avatar">
            {workspace?.identity.organization_id === "otherco" ? "O" : "N"}
          </span>
          <span>
            <strong>
              {workspace?.identity.organization_id === "otherco"
                ? t("Other Company", "另一家公司")
                : t("Northstar Labs", "北辰实验室")}
            </strong>
            <small>{t("Local demo workspace", "本地演示工作空间")}</small>
          </span>
          <ChevronDown size={15} />
        </button>
        <div className="nav-label">{t("WORKSPACE", "工作空间")}</div>
        <nav>
          {(
            [
              { id: "assistant", icon: Sparkles },
              { id: "cases", icon: Layers },
              { id: "deals", icon: Layers },
              { id: "operations", icon: Layers },
              { id: "knowledge", icon: BookOpen },
            ] as { id: Page; icon: typeof Sparkles }[]
          ).map(({ id, icon: Icon }) => (
            <button
              key={id}
              className={`nav-item ${page === id ? "active" : ""}`}
              onClick={() => go(id)}
            >
              <Icon size={18} />
              <span>
                {
                  {
                    assistant: t("Case assistant", "案例助手"),
                    cases: t("SLA cases", "SLA 案例"),
                    deals: t("Deal desk", "交易审核"),
                    operations: t("Operations", "运行概览"),
                    knowledge: t("Policies & evidence", "政策与证据"),
                  }[id]
                }
              </span>
              {id === "assistant" && <span className="small-tag">AI</span>}
              {id === "knowledge" && (
                <span className="count">{docs.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="demo-note">
            <span className="status-dot" />
            {t("Demo data. Real capabilities.", "虚构数据，真实能力。")}
          </div>
          <button className="profile" onClick={() => setLogin(true)}>
            <span className="avatar">
              {workspace?.identity.name.charAt(0) || "N"}
            </span>
            <span>
              <strong>
                {workspace?.identity.name ||
                  t("Connect workspace", "连接工作空间")}
              </strong>
              <small>{t("Switch demo employee", "切换演示员工")}</small>
            </span>
            <ChevronDown size={15} />
          </button>
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Toggle navigation"
              onClick={() => setNav(!nav)}
            >
              {nav ? <PanelLeftClose size={19} /> : <PanelLeftOpen size={19} />}
            </button>
            <span>{t("Workspace", "工作空间")}</span>
            <ChevronRight size={13} />
            <strong>{title}</strong>
          </div>
          <div className="top-actions">
            <form
              className="global-search"
              onSubmit={(e) => {
                e.preventDefault();
                void search(query);
              }}
            >
              <Search size={16} />
              <input
                aria-label={t("Search workspace", "搜索工作空间")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("Search your workspace", "搜索你的工作空间")}
              />
              <span>↵</span>
            </form>
            <button
              className="language-button"
              onClick={() => setLang(lang === "en" ? "zh" : "en")}
            >
              <Globe2 size={16} />
              {lang === "en" ? "中文" : "EN"}
            </button>
            <span className="avatar small">
              {workspace?.identity.name.charAt(0) || "N"}
            </span>
          </div>
        </header>
        <main
          className={`content ${page === "assistant" ? "chat-layout" : ""}`}
        >
          {error && (
            <div role="alert" className="error-banner">
              <AlertCircle size={18} />
              {error}
              <button
                className="icon-button"
                onClick={() => setError("")}
                aria-label="Dismiss error"
              >
                <X size={16} />
              </button>
            </div>
          )}
          {loading ? (
            <div className="loading-state">
              <LoaderCircle size={25} className="spin" />
              <h2>{t("Opening your workspace…", "正在打开你的工作空间…")}</h2>
              <p>
                {t(
                  "Loading the knowledge you have access to.",
                  "正在加载你有权访问的知识。",
                )}
              </p>
            </div>
          ) : !workspace ? (
            <div className="empty-state">
              <LockKeyhole size={32} />
              <h2>{t("Your workspace starts here", "连接你的工作空间")}</h2>
              <p>
                {t(
                  "Choose a demo employee or connect with your access token.",
                  "选择演示员工，或使用访问令牌连接。",
                )}
              </p>
              <button className="primary-button" onClick={() => setLogin(true)}>
                {t("Connect workspace", "连接工作空间")}
              </button>
            </div>
          ) : (
            <>
              {page === "assistant" && (
                <>
                  {workspace.persistence_available && (
                    <div className="history-toolbar">
                      <label>
                        {t("Saved conversations", "已保存的对话")}
                        <select
                          aria-label={t("Saved conversations", "已保存的对话")}
                          value={conversationId}
                          disabled={busy || !historyReady}
                          onChange={(e) =>
                            void loadConversation(e.target.value)
                          }
                        >
                          <option value="">
                            {t("New conversation", "新建对话")}
                          </option>
                          {conversations.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.title}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        className="secondary-button"
                        disabled={busy}
                        onClick={() => {
                          void refreshHistory()
                            .then(() => loadConversation(conversationId))
                            .catch((err) => setError(failure(err)));
                        }}
                      >
                        {t("Refresh history", "刷新历史")}
                      </button>
                    </div>
                  )}
                  <div className="chat-header">
                    <div>
                      <h1>
                        <Sparkles size={24} />
                        {t("B2B SLA Escalation/Deal desk Agent", "B2B SLA 升级 / 商务审批助手")}
                      </h1>
                      <p>
                        {t(
                          workspace.persistence_available
                            ? "Investigate policies and draft case documents. Saved conversations retain context."
                            : "Investigate policies and draft case documents. Each request starts fresh.",
                          workspace.persistence_available
                            ? "调查政策并起草案例文档。已保存的对话保留上下文。"
                            : "调查政策并起草案例文档。每次请求独立开始。",
                        )}
                      </p>
                    </div>
                    <button
                      className="secondary-button"
                      disabled={busy || !historyReady}
                      onClick={() => {
                        historyVersion.current++;
                        setMessages([]);
                        setConversationId("");
                        setHistoryLoading(false);
                        pendingTurn.current = null;
                      }}
                    >
                      <Plus size={16} />
                      {t("New conversation", "新建对话")}
                    </button>
                  </div>
                  <div className="conversation" aria-live="polite">
                    {historyLoading && <p role="status">{t("Loading saved conversation… You can start a new conversation while it loads.", "正在加载已保存的对话…你也可以先新建对话。")}</p>}
                    {messages.length === 0 ? (
                      <div className="chat-welcome">
                        <span className="big-spark">
                          <Sparkles size={32} />
                        </span>
                        <h2>
                          {t(
                            "Which customer case needs attention?",
                            "哪个客户案例需要处理？",
                          )}
                        </h2>
                        <p>
                          {t(
                            "Investigate SLA cases and deal renewals, calculate exact proposals, and draft review memos. Open SLA cases or Deal desk for independent approval and verified sandbox receipts.",
                            "调查 SLA 案例与交易续约、计算准确建议并起草审核备忘录。在 SLA 案例或交易审核中进行独立审批，并查看已核验的沙盒回执。",
                          )}
                        </p>
                        <div className="suggestions">
                          {prompts.map((p) => (
                            <button
                              key={p.color}
                              onClick={() => setQuestion(p.q)}
                            >
                              <p.icon size={17} />
                              {p.title}
                              <ArrowUpRight size={15} />
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : (
                      messages.map((m) => (
                        <div className="exchange" key={m.id}>
                          <div className="user-message">{m.question}</div>
                          <div className="assistant-message">
                            <span className="answer-avatar">
                              <Sparkles size={18} />
                            </span>
                            <div className="answer-body">
                              <strong className="answer-name">
                                B2B SLA Escalation/Deal desk Agent <span>AI</span>
                              </strong>
                              {!m.result && !!m.files?.length && (
                                <div className="source-chips">
                                  {m.files.map((file) => (
                                    <button
                                      key={file.id}
                                      onClick={() => void downloadFile(file)}
                                    >
                                      <FileText size={14} />
                                      {t("Download", "下载")} {file.filename}
                                    </button>
                                  ))}
                                </div>
                              )}
                              {m.result ? (
                                <>
                                  <div className="answer-text">
                                    {m.result.answer}
                                  </div>
                                  {m.result.status !== "answered" && (
                                    <span className="result-status">
                                      {m.result.status.replaceAll("_", " ")}
                                    </span>
                                  )}
                                  {!!m.result.files?.length && (
                                    <div className="source-chips">
                                      {m.result.files.map((file) => (
                                        <button
                                          key={file.id}
                                          onClick={() =>
                                            void downloadFile(file)
                                          }
                                        >
                                          <FileText size={14} />
                                          {t("Download", "下载")}{" "}
                                          {file.filename}
                                          <small>
                                            {Math.max(
                                              1,
                                              Math.ceil(file.size_bytes / 1024),
                                            )}{" "}
                                            KB
                                          </small>
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                  {m.result.sources.length > 0 && (
                                    <div className="source-chips">
                                      {m.result.sources.map((d, i) => (
                                        <button
                                          key={d.id}
                                          onClick={() => void openDoc(d.id)}
                                        >
                                          <span>{i + 1}</span>
                                          <FileText size={14} />
                                          {d.title}
                                          <ArrowUpRight size={13} />
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                  <details className="trace">
                                    <summary>
                                      <CheckCheck size={14} />
                                      {m.result.tool_calls}{" "}
                                      {t("tool calls", "次工具调用")}
                                      <span>·</span>
                                      {m.result.model_calls}{" "}
                                      {t("model turns", "轮模型调用")}
                                      <ChevronDown size={13} />
                                    </summary>
                                    {m.result.plan && <div className="agent-plan">
                                      <strong>{t("Investigation plan", "调查计划")} · {t("Revision", "修订")} {m.result.plan.revision}</strong>
                                      {m.result.plan.nodes.map((node) => <p key={node.id}>
                                        {node.role} · {node.id} · {node.status}
                                        {node.dependencies.length > 0 && ` ← ${node.dependencies.join(", ")}`}
                                      </p>)}
                                      {m.result.verification && <p>{t("Supported / checked claims", "有支持 / 已检查的陈述")}: {m.result.verification.supported_claims} / {m.result.verification.checked_claims} · {t("Repairs", "修正")}: {m.result.verification.repairs}</p>}
                                    </div>}
                                    {m.result.tool_trace.map((step, i) => (
                                      <div className="trace-step" key={i}>
                                        <span
                                          className={
                                            step.outcome === "ok"
                                              ? "success"
                                              : "warning"
                                          }
                                        >
                                          {step.outcome === "ok" ? (
                                            <Check size={13} />
                                          ) : (
                                            <AlertCircle size={13} />
                                          )}
                                        </span>
                                        <code>{step.role ? `${step.role} / ` : ""}{step.tool}</code>
                                        <span>{step.outcome}</span>
                                        {step.document_ids.map((id) => (
                                          <button
                                            key={id}
                                            onClick={() => void openDoc(id)}
                                          >
                                            {docs.find((d) => d.id === id)
                                              ?.title || id}
                                          </button>
                                        ))}
                                      </div>
                                    ))}
                                  </details>
                                </>
                              ) : m.error ? (
                                <div role="alert" className="answer-error">
                                  <AlertCircle size={18} />
                                  <p>{m.error}</p>
                                  <button
                                    className="text-button"
                                    disabled={busy}
                                    onClick={() => void ask(m.question)}
                                  >
                                    {t("Try again", "重试")}
                                  </button>
                                </div>
                              ) : (
                                <div className="thinking">
                                  <LoaderCircle className="spin" size={16} />
                                  {t(
                                    "Investigating your available knowledge…",
                                    "正在检索你有权访问的知识…",
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      ))
                    )}
                    <div ref={end} />
                  </div>
                  <div className="chat-composer">
                    {composer}
                    <div className="composer-hint">
                      {t(
                        "AI can make mistakes. Open the sources to verify important details.",
                        "AI 可能出错。重要信息请查阅原始来源。",
                      )}
                    </div>
                  </div>
                </>
              )}
              {page === "cases" && (
                <CasesPanel
                  key={`${authVersion}:${token}:${lang}`}
                  token={token}
                  lang={lang}
                  actorId={workspace.identity.id}
                  available={!!workspace.cases_available}
                  ask={(q) => {
                    setQuestion(q);
                    go("assistant");
                  }}
                />
              )}
              {page === "deals" && (
                <DealsPanel
                  key={`${authVersion}:${token}:${lang}`}
                  token={token}
                  lang={lang}
                  actorId={workspace.identity.id}
                  available={!!workspace.cases_available}
                  people={config?.employees || []}
                  ask={(q) => {
                    setQuestion(q);
                    go("assistant");
                  }}
                />
              )}
              {page === "operations" && (
                <OperationsPanel
                  key={`${authVersion}:${token}:${lang}`}
                  token={token}
                  lang={lang}
                  available={!!workspace.cases_available}
                />
              )}
              {page === "knowledge" && (
                <>
                  <div className="page-heading">
                    <div>
                      <div className="eyebrow">
                        {t("POLICIES AND CASE EVIDENCE", "政策与案例证据")}
                      </div>
                      <h1>{title}</h1>
                      <p>
                        {t(
                          "Authorized SLA terms, incident records, and commercial policies.",
                          "你有权访问的 SLA 条款、事件记录和商务政策。",
                        )}
                      </p>
                    </div>
                    <span className="live-pill">
                      <ShieldCheck size={14} />
                      {t("Access-aware", "基于访问权限")}
                    </span>
                  </div>
                  {workspace.persistence_available && (
                    <>
                      <KnowledgeControls
                        key={`uploads:${authVersion}:${token}:${lang}`}
                        token={token}
                        lang={lang}
                        onChange={() => {
                          const e = epoch.current;
                          void api<Workspace>(`/v1/workspace?language=${lang}`)
                            .then((data) => {
                              if (e === epoch.current) {
                                setWorkspace(data);
                                setHits(null);
                              }
                            })
                            .catch((err) => setError(failure(err)));
                        }}
                      />
                      <MemoryPanel
                        key={`memory:${authVersion}:${token}:${lang}`}
                        token={token}
                        lang={lang}
                      />
                    </>
                  )}
                  <div className="library-toolbar">
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void search();
                      }}
                    >
                      <Search size={19} />
                      <input
                        aria-label={t("Search documents", "搜索文档")}
                        placeholder={t(
                          "Search policies and evidence…",
                          "搜索政策与证据…",
                        )}
                        value={query}
                        onChange={(e) => {
                          setQuery(e.target.value);
                          if (!e.target.value) setHits(null);
                        }}
                      />
                      <button className="primary-button" disabled={searching}>
                        {searching ? (
                          <LoaderCircle className="spin" size={16} />
                        ) : (
                          t("Search", "搜索")
                        )}
                      </button>
                    </form>
                    <select
                      aria-label={t("Filter evidence", "筛选证据")}
                      value={project}
                      onChange={(e) => setProject(e.target.value)}
                    >
                      <option value="all">
                        {t("All evidence", "全部证据")}
                      </option>
                      {workspace.projects.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                      <option value="company">
                        {t("Operating policies", "操作政策")}
                      </option>
                    </select>
                  </div>
                  <div className="library-count">
                    {t("AVAILABLE TO YOU", "你可访问的内容")}
                    <span>
                      {filteredDocs.length} {t("documents", "份文档")}
                    </span>
                  </div>
                  <div className="library-grid">
                    {filteredDocs.map(docCard)}
                  </div>
                  {!filteredDocs.length && (
                    <div className="empty-state">
                      <Search size={32} />
                      <h2>{t("No documents found", "未找到文档")}</h2>
                      <p>
                        {t(
                          "Try other keywords. Results only include permitted evidence.",
                          "请尝试其他关键词。结果仅包含有权访问的证据。",
                        )}
                      </p>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </main>
      </div>
      {document && (
        <DocumentDialog
          doc={document}
          lang={lang}
          close={() => setDocument(null)}
          ask={() => {
            setQuestion(
              t(
                `What does the document "${document.title}" say? Summarize its key points.`,
                `请总结文档「${document.title}」的要点。`,
              ),
            );
            setDocument(null);
            go("assistant");
          }}
          copied={copied}
          copy={async () => {
            try {
              await navigator.clipboard.writeText(document.content);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            } catch {
              setError(
                t(
                  "Clipboard unavailable. Select the text to copy it.",
                  "无法使用剪贴板，请选中文本复制。",
                ),
              );
            }
          }}
        />
      )}
      {login && (
        <IdentityDialog close={() => setLogin(false)}>
          <section className="identity-modal">
            <div className="section-title">
              <h2>{t("Your view of the workspace", "选择你的工作视角")}</h2>
              <button
                className="icon-button"
                aria-label="Close"
                onClick={() => setLogin(false)}
              >
                <X size={19} />
              </button>
            </div>
            <p>
              {config?.demo_mode
                ? t(
                    "Choose a fictional employee to explore how access changes. Switching clears this session’s answers and sources.",
                    "选择虚构员工，体验不同的访问权限。切换身份将清除当前会话的答案和来源。",
                  )
                : t(
                    "Connect using the access token provided by your workspace.",
                    "使用工作空间提供的访问令牌连接。",
                  )}
            </p>
            {config?.demo_mode && (
              <div className="employee-list">
                {config.employees.map((employee) => (
                  <button
                    key={employee.id}
                    onClick={() => {
                      setLogin(false);
                      void chooseEmployee(employee.id);
                    }}
                  >
                    <span className="avatar">
                      {employee.name[lang].charAt(0)}
                    </span>
                    <span>
                      <strong>{employee.name[lang]}</strong>
                      <small>
                        {employee.organization_id === "otherco"
                          ? t(
                              "Other Company · isolated workspace",
                              "另一家公司 · 独立工作空间",
                            )
                          : employee.id === "mei"
                            ? t(
                                "Engineering · Northstar Labs",
                                "研发 · 北辰实验室",
                              )
                            : employee.id === "alex"
                              ? t("Sales · Northstar Labs", "销售 · 北辰实验室")
                              : t(
                                  "Operations · Northstar Labs",
                                  "运营 · 北辰实验室",
                                )}
                      </small>
                    </span>
                    {employee.id === workspace?.identity.id ? (
                      <Check size={17} />
                    ) : (
                      <ArrowRight size={16} />
                    )}
                  </button>
                ))}
              </div>
            )}
            <details>
              <summary>
                {t("Connect with an access token", "使用访问令牌连接")}
              </summary>
              <form
                className="token-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  reset();
                  setToken(inputToken.trim());
                  setAuthVersion((v) => v + 1);
                  setInputToken("");
                  setLogin(false);
                }}
              >
                <input
                  type="password"
                  required
                  aria-label="Access token"
                  autoComplete="off"
                  placeholder={t("Paste access token", "粘贴访问令牌")}
                  value={inputToken}
                  onChange={(e) => setInputToken(e.target.value)}
                />
                <button className="primary-button">
                  {t("Connect", "连接")}
                </button>
              </form>
            </details>
            <div className="demo-footnote">
              <LockKeyhole size={14} />
              {t(
                "Local demo only. Credentials stay in browser memory.",
                "仅供本地演示。凭据仅保留在浏览器内存中。",
              )}
            </div>
          </section>
        </IdentityDialog>
      )}
    </div>
  );
}

function IdentityDialog({
  children,
  close,
}: {
  children: React.ReactNode;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className="identity-shell"
      aria-label="Choose workspace identity"
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === ref.current) close();
      }}
    >
      {children}
    </dialog>
  );
}

function DocumentDialog({
  doc,
  lang,
  close,
  ask,
  copy,
  copied,
}: {
  doc: Doc;
  lang: Lang;
  close: () => void;
  ask: () => void;
  copy: () => void;
  copied: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  const t = (a: string, b: string) => (lang === "zh" ? b : a);
  return (
    <dialog
      className="document-dialog"
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === ref.current) close();
      }}
    >
      <div className="document-top">
        <span>
          <FileText size={18} />
          {t("Source document", "来源文档")}
        </span>
        <button
          className="icon-button"
          onClick={close}
          aria-label={t("Close document", "关闭文档")}
        >
          <X size={20} />
        </button>
      </div>
      <div className="document-body">
        <span className="file-icon large">
          <FileText size={30} />
        </span>
        <h1>{doc.title}</h1>
        <div className="document-metadata">
          <span>
            {doc.project_id?.toUpperCase() ||
              t("Company knowledge", "公司知识")}
          </span>
          <span>v{doc.version}</span>
          <span>
            {new Date(doc.updated_at).toLocaleDateString(
              lang === "zh" ? "zh-CN" : "en-US",
              { year: "numeric", month: "short", day: "numeric" },
            )}
          </span>
        </div>
        <div className="document-content">{doc.content}</div>
        <div className="source-verified">
          <ShieldCheck size={16} />
          {t(
            "Access checked for your current employee identity.",
            "已根据当前员工身份检查访问权限。",
          )}
        </div>
      </div>
      <div className="document-footer">
        <button className="secondary-button" onClick={copy}>
          {copied ? <Check size={16} /> : <Copy size={16} />}{" "}
          {copied ? t("Copied", "已复制") : t("Copy text", "复制文本")}
        </button>
        <button className="primary-button" onClick={ask}>
          <Sparkles size={16} />
          {t("Ask about this document", "询问此文档")}
        </button>
      </div>
    </dialog>
  );
}
createRoot(window.document.getElementById("root")!).render(<App />);
