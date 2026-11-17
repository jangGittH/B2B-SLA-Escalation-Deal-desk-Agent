import { test, expect } from "@playwright/test";

// Existing endpoint contract tests deliberately exercise the legacy API path.
// Persistent conversation history is covered against the real persistence API separately.
test.beforeEach(async ({ page }) => {
  await page.route("**/v1/workspace?*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      json: { ...(await response.json()), persistence_available: false },
    });
  });
});

test("assistant landing, authorized docs, source reader, and employee isolation", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "B2B SLA Escalation/Deal desk Agent" }),
  ).toBeVisible();
  await page.screenshot({ path: "/tmp/northstar-home.png", fullPage: true });
  await page
    .getByRole("button", { name: "Policies & evidence", exact: false })
    .filter({ has: page.locator(".count") })
    .click();
  await expect(
    page.getByRole("button", { name: /Acme incident investigation runbook/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Acme incident investigation runbook/ })
    .click();
  await expect(page.getByRole("dialog")).toContainText("AURORA-PRIVATE-742");
  await page.keyboard.press("Escape");
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Alex Wang/ })
    .click();
  await expect(page.locator(".profile")).toContainText("Alex Wang");
  await expect(
    page.getByRole("button", { name: /Acme deal review draft/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Acme incident investigation runbook/ }),
  ).toHaveCount(0);
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Sam Lee/ })
    .click();
  await expect(
    page.getByRole("button", { name: /Other account SLA terms/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Acme deal review draft/ }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("search, Chinese language and mobile layout", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "B2B SLA 升级 / 商务审批助手" }),
  ).toBeVisible();
  await page.locator(".nav-item").filter({ hasText: "政策与证据" }).click();
  await page.getByRole("textbox", { name: "搜索文档" }).fill("响应时限");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /Acme SLA 条款/ }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "搜索文档" })
    .fill("zznomatchingtopic");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByRole("heading", { name: "未找到文档" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await page.getByRole("button", { name: "案例助手 AI", exact: true }).click();
  await page.screenshot({ path: "/tmp/northstar-mobile.png", fullPage: true });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("agent answer, escaped text, sources, activity and session clearing", async ({
  page,
}) => {
  await page.route("**/v1/agent/ask", async (route) => {
    const response = await page.request.get("/v1/workspace", {
      headers: { Authorization: route.request().headers()["authorization"] },
    });
    const workspace = await response.json();
    const doc = workspace.documents.find((d: any) => d.id === "acme-sla");
    await route.fulfill({
      json: {
        status: "answered",
        answer:
          "The initial response target is 60 minutes. <script>window.unsafe=true</script>",
        sources: [doc],
        tool_trace: [
          { tool: "search_knowledge", outcome: "ok", document_ids: [doc.id] },
          { tool: "read_document", outcome: "ok", document_ids: [doc.id] },
        ],
        tool_calls: 2,
        model_calls: 3,
      },
    });
  });
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page
    .getByRole("textbox", { name: "Ask your assistant" })
    .fill("What is the Acme response target?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".answer-text")).toContainText("60 minutes");
  expect(await page.evaluate(() => (window as any).unsafe)).toBeUndefined();
  await page.locator(".source-chips button").click();
  await expect(page.getByRole("dialog")).toContainText("Acme SLA terms");
  await page.keyboard.press("Escape");
  await page.locator(".trace summary").click();
  await expect(page.locator(".trace-step")).toHaveCount(2);

  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Alex Wang/ })
    .click();
  await expect(page.locator(".answer-text")).toHaveCount(0);
});

test("agent failure is visible and navigation stays focused on cases and evidence", async ({
  page,
}) => {
  await page.route("**/v1/agent/ask", (route) =>
    route.fulfill({ status: 503, json: { detail: "Model unavailable" } }),
  );
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page
    .getByRole("textbox", { name: "Ask your assistant" })
    .fill("What is Acme?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "assistant is unavailable",
  );
  await expect(
    page.getByRole("button", { name: "Try again", exact: true }),
  ).toBeEnabled();
  await expect(page.locator("nav .nav-item")).toHaveCount(5);
  await expect(
    page.getByRole("button", { name: "Meetings", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Sheets", exact: true }),
  ).toHaveCount(0);
});

test("switching employees discards a pending answer", async ({ page }) => {
  let started!: () => void;
  const requested = new Promise<void>((resolve) => (started = resolve));
  let finish!: () => void;
  const released = new Promise<void>((resolve) => (finish = resolve));
  await page.route("**/v1/agent/ask", async (route) => {
    started();
    await released;
    try {
      await route.fulfill({
        json: {
          status: "answered",
          answer: "OLD_EMPLOYEE_ONLY",
          sources: [],
          tool_trace: [],
          tool_calls: 0,
          model_calls: 1,
        },
      });
    } catch {
      /* browser aborted the old identity request */
    }
  });
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page
    .getByRole("textbox", { name: "Ask your assistant" })
    .fill("Tell me about Acme");
  await page.getByRole("button", { name: "Send question" }).click();
  await requested;
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Alex Wang/ })
    .click();
  await expect(page.locator(".profile")).toContainText("Alex Wang");
  finish();
  await expect(
    page.getByRole("heading", { name: "Which customer case needs attention?" }),
  ).toBeVisible();
  await expect(page.getByText("OLD_EMPLOYEE_ONLY")).toHaveCount(0);
  await expect(page.locator(".answer-text")).toHaveCount(0);
});

test("generated attachment downloads using employee credentials", async ({
  page,
}) => {
  await page.route("**/v1/workspace?*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      json: {
        ...(await response.json()),
        agent_available: true,
        persistence_available: false,
      },
    });
  });
  await page.route("**/v1/agent/ask", (route) =>
    route.fulfill({
      json: {
        status: "answered",
        answer: "Created your file.",
        sources: [],
        files: [{ id: "test-file", filename: "notes.md", size_bytes: 8 }],
        tool_trace: [{ tool: "write_file", outcome: "ok", document_ids: [] }],
        tool_calls: 1,
        model_calls: 2,
      },
    }),
  );
  let authenticated = false;
  await page.route("**/v1/files/test-file", (route) => {
    authenticated = !!route
      .request()
      .headers()
      ["authorization"]?.startsWith("Bearer ");
    return route.fulfill({ contentType: "text/markdown", body: "# Notes\n" });
  });
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page
    .getByRole("textbox", { name: "Ask your assistant" })
    .fill("Create notes.md");
  await page.getByRole("button", { name: "Send question" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /Download notes.md/ }).click();
  expect((await download).suggestedFilename()).toBe("notes.md");
  expect(authenticated).toBe(true);
});
