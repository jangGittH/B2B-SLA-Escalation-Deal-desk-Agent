import { test, expect } from "@playwright/test";

test("progress reconnects with a cursor and bearer authentication, then observes cancellation", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  const session = await page.request.post("/v1/demo/session", {
    data: { employee_id: "mei" },
  });
  const headers = { Authorization: `Bearer ${(await session.json()).token}` };
  const original = (
    await (await page.request.get("/v1/cases/INC-104", { headers })).json()
  ).ticket;
  const { id, organization_id, version, updated_at, ...fields } = original;
  const caseId = `STREAM-${Date.now()}`;
  expect(
    (
      await page.request.post("/v1/cases", {
        headers,
        data: { ...fields, id: caseId, title: "Progress recovery check" },
      })
    ).status(),
  ).toBe(201);
  const started = await page.request.post("/v1/sla-workflows", {
    headers,
    data: {
      ticket_id: caseId,
      expected_ticket_version: 1,
      request_key: crypto.randomUUID(),
      language: "en",
    },
  });
  expect(started.status()).toBe(202);
  const workflow = await started.json();
  const saved = await (
    await page.request.get(`/v1/sla-workflows/${workflow.id}`, { headers })
  ).json();
  const firstEvent = saved.events[0];
  const seen: { cursor: number; auth: string; keys: string[] }[] = [];
  await page.route(
    `**/v1/sla-workflows/${workflow.id}/events?*`,
    async (route) => {
      const url = new URL(route.request().url());
      seen.push({
        cursor: Number(url.searchParams.get("after")),
        auth: route.request().headers()["authorization"],
        keys: [...url.searchParams.keys()],
      });
      if (seen.length === 1) {
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: `id: ${firstEvent.id}\nevent: progress\ndata: ${JSON.stringify(firstEvent)}\n\n`,
        });
      } else await route.continue();
    },
  );
  await page.locator("nav .nav-item").filter({ hasText: "SLA cases" }).click();
  await page.getByRole("button", { name: new RegExp(caseId) }).click();
  const panel = page.getByRole("region", { name: "SLA workflow", exact: true });
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Needs case information",
  );
  await expect.poll(() => seen.length).toBeGreaterThanOrEqual(2);
  expect(seen[1].cursor).toBe(firstEvent.id);
  expect(
    seen.every(
      (r) => r.auth.startsWith("Bearer ") && r.keys.join() === "after",
    ),
  ).toBe(true);
  await expect(panel).toContainText("Live progress");
  await page.screenshot({
    path: "/tmp/northstar-unit8-progress.png",
    fullPage: true,
  });
  await panel
    .getByRole("button", { name: "Cancel workflow", exact: true })
    .click();
  await expect(panel.locator(".workflow-status")).toHaveText("Cancelled");
});

test("operations reports scoped outcomes and unknown cost, with Chinese mobile layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page.locator("nav .nav-item").filter({ hasText: "Operations" }).click();
  await expect(
    page.getByText("Your requested or assigned workflows only."),
  ).toBeVisible();
  await expect(page.getByText(/Inference cost: unknown/)).toBeVisible();
  await page.getByLabel("Reporting window").selectOption("1");
  await expect(
    page.getByRole("heading", { name: "Workflow outcomes" }),
  ).toBeVisible();
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Lin Zhao/ })
    .click();
  await expect(page.getByText(/Tenant-wide metrics/)).toBeVisible();
  await page.screenshot({
    path: "/tmp/northstar-unit8-operations.png",
    fullPage: true,
  });
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Sam Lee/ })
    .click();
  await expect(page.getByText("No workflows in this window.")).toBeVisible();
  await expect(page.locator(".ops-trace")).toHaveCount(0);
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "运行概览", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page
        .locator(".sidebar")
        .evaluate((el) => el.getBoundingClientRect().right),
    )
    .toBeLessThanOrEqual(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "/tmp/northstar-unit8-operations-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
