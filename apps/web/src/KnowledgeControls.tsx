import { useState } from "react";
export function KnowledgeControls({
  token,
  lang,
  onChange,
}: {
  token: string;
  lang: "en" | "zh";
  onChange: () => void;
}) {
  const t = (en: string, zh: string) => (lang === "zh" ? zh : en);
  const [title, setTitle] = useState(""),
    [scope, setScope] = useState("user"),
    [file, setFile] = useState<File | null>(null),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [documentId, setDocumentId] = useState(""),
    [version, setVersion] = useState(1);
  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setMessage("");
    try {
      if (file.size > 2000000)
        throw new Error(t("Maximum upload is 2 MB", "文件不能超过 2 MB"));
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (const byte of bytes) binary += String.fromCharCode(byte);
      const response = await fetch(
        documentId
          ? `/v1/knowledge/uploads/${documentId}`
          : "/v1/knowledge/uploads",
        {
          method: documentId ? "PUT" : "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            title,
            filename: file.name,
            content_base64: btoa(binary),
            language: lang,
            scope,
            ...(documentId ? { expected_version: version } : {}),
          }),
        },
      );
      const data = await response.json();
      if (!response.ok)
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : t("Upload rejected", "上传被拒绝"),
        );
      setDocumentId(data.id);
      setVersion(data.version);
      setMessage(
        t(
          `Saved evidence v${data.version}. Original preserved; no automatic translation.`,
          `已保存证据 v${data.version}。保留原文件，不自动翻译。`,
        ),
      );
      onChange();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    setBusy(true);
    try {
      const response = await fetch(
        `/v1/knowledge/uploads/${documentId}?expected_version=${version}`,
        { method: "DELETE", headers: { Authorization: `Bearer ${token}` } },
      );
      if (!response.ok)
        throw new Error(
          t(
            "Delete rejected; reload the current version",
            "删除失败，请重新加载当前版本",
          ),
        );
      setDocumentId("");
      setVersion(1);
      setMessage(
        t(
          "Evidence deleted and search index invalidated.",
          "证据已删除，搜索索引已失效。",
        ),
      );
      onChange();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="case-detail knowledge-controls">
      <summary>{t("Upload or revise evidence", "上传或修订证据")}</summary>
      <p>
        {t(
          "TXT, Markdown, DOCX or text PDF; 2 MB maximum. Uploaded text is evidence, never approval or instructions. Only Operations may publish shared evidence.",
          "支持 TXT、Markdown、DOCX 或文本 PDF，最大 2 MB。上传文本仅为证据，不是审批或系统指令。共享证据须由运营人员发布。",
        )}
      </p>
      <form onSubmit={upload} className="governance-form">
        <label>
          {t("Evidence title", "证据标题")}
          <input
            required
            value={title}
            maxLength={160}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label>
          {t("Evidence file", "证据文件")}
          <input
            type="file"
            required
            accept=".txt,.md,.docx,.pdf"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </label>
        <label>
          {t("Sharing", "共享范围")}
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="user">{t("Private to me", "仅本人")}</option>
            <option value="tenant">
              {t("Organization (Operations only)", "组织（仅运营）")}
            </option>
          </select>
        </label>
        <label>
          {t("Existing upload ID (blank for new)", "已有上传 ID（新建时留空）")}
          <input
            value={documentId}
            onChange={(e) => setDocumentId(e.target.value)}
          />
        </label>
        {documentId && (
          <label>
            {t("Expected document version", "预期文档版本")}
            <input
              type="number"
              min={1}
              value={version}
              onChange={(e) => setVersion(Number(e.target.value))}
            />
          </label>
        )}
        <button className="primary-button" disabled={busy || !file}>
          {documentId
            ? t("Save evidence revision", "保存证据修订")
            : t("Upload evidence", "上传证据")}
        </button>
        {documentId && (
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={() => void remove()}
          >
            {t("Delete uploaded evidence", "删除上传证据")}
          </button>
        )}
      </form>
      {message && <p role="status">{message}</p>}
    </details>
  );
}
