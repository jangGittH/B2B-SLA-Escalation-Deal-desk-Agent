import { test, expect, type Page } from "@playwright/test";

async function createCase(page: Page, suffix: string, day: number) {
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  const session = await page.request.post("/v1/demo/session", {
    data: { employee_id: "mei" },
  });
  const token = (await session.json()).token;
  const headers = { Authorization: `Bearer ${token}` };
  const response = await page.request.get("/v1/cases/INC-105", { headers });
  const { id, organization_id, version, updated_at, ...original } = (
    await response.json()
  ).ticket;
  const caseId = `FLOW-${suffix}-${Date.now()}`;
  const body = {
    ...original,
    id: caseId,
    title: `Workflow browser ${suffix}`,
    reported_at: `2026-11-${day}T01:00:00Z`,
    first_response_at: null,
    incident_started_at: `2026-11-${day}T01:00:00Z`,
    incident_ended_at: `2026-11-${day}T02:30:00Z`,
    exclusions: [],
  };
  const created = await page.request.post("/v1/cases", { headers, data: body });
  expect(created.status()).toBe(201);
  await page.locator("nav .nav-item").filter({ hasText: "SLA cases" }).click();
  await page.getByRole("button", { name: new RegExp(caseId) }).click();
  return { caseId, headers };
}
async function switchEmployee(page: Page, name: string) {
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: new RegExp(name) })
    .click();
  await expect(page.locator(".profile")).toContainText(name);
}

test("independent reviewer previews modified credit and closes one verified sandbox action", async ({
  page,
}) => {
  const { caseId } = await createCase(page, "approve", 20);
  const panel = page.getByRole("region", { name: "SLA workflow", exact: true });
  await panel
    .getByRole("button", { name: "Start SLA review", exact: true })
    .click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Waiting for review",
  );
  await expect(panel).toContainText("Reviewer: Lin Zhao");
  await expect(
    panel.getByRole("button", { name: "Approve sandbox credit", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await page.locator("nav .nav-item").filter({ hasText: "SLA cases" }).click();
  await page.getByRole("button", { name: new RegExp(caseId) }).click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Waiting for review",
  );
  await switchEmployee(page, "Lin Zhao");
  await page.getByRole("button", { name: new RegExp(caseId) }).click();
  await panel
    .getByRole("textbox", { name: "Review reason or feedback", exact: true })
    .fill("Half credit approved after independent review.");
  await panel
    .getByRole("spinbutton", {
      name: "Credit percentage to approve",
      exact: true,
    })
    .fill("50");
  await panel
    .getByRole("button", { name: "Preview modified credit", exact: true })
    .click();
  await expect(panel.locator(".credit-preview")).toContainText("USD 22.50");
  await page.screenshot({
    path: "/tmp/northstar-unit6-approval.png",
    fullPage: true,
  });
  await panel
    .getByRole("button", { name: "Modify and approve", exact: true })
    .click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Completed and verified",
  );
  await expect(panel.locator(".receipt-card")).toContainText("USD 22.50");
  await expect(panel.locator(".receipt-card")).toContainText(
    "No customer message was sent",
  );
  await page.reload();
  // Reload resets the simulated identity to Mei, who can see her verified result.
  await page.locator("nav .nav-item").filter({ hasText: "SLA cases" }).click();
  await page.getByRole("button", { name: new RegExp(caseId) }).click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Completed and verified",
  );
  await expect(
    panel.getByRole("button", { name: "Start SLA review", exact: true }),
  ).toBeDisabled();
  await page.screenshot({
    path: "/tmp/northstar-unit6-closure.png",
    fullPage: true,
  });
});

test("reviewer rejection persists feedback without recording a receipt", async ({
  page,
}) => {
  const { caseId } = await createCase(page, "reject", 21);
  const panel = page.getByRole("region", { name: "SLA workflow", exact: true });
  await panel
    .getByRole("button", { name: "Start SLA review", exact: true })
    .click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Waiting for review",
  );
  await switchEmployee(page, "Lin Zhao");
  await page.getByRole("button", { name: new RegExp(caseId) }).click();
  await expect(
    panel.getByRole("button", { name: "Reject with reason", exact: true }),
  ).toBeDisabled();
  await panel
    .getByRole("textbox", { name: "Review reason or feedback", exact: true })
    .fill("Provide independent outage evidence first.");
  await panel
    .getByRole("button", { name: "Reject with reason", exact: true })
    .click();
  await expect(panel.locator(".workflow-status")).toHaveText("Rejected");
  await expect(panel.locator(".review-feedback")).toContainText(
    "Provide independent outage evidence first.",
  );
  await expect(panel.locator(".receipt-card")).toHaveCount(0);
});

test("missing case facts persist, Chinese mobile progress and cancellation work", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page.locator("nav .nav-item").filter({ hasText: "SLA cases" }).click();
  await page.getByRole("button", { name: /INC-104 Intermittent/ }).click();
  let panel = page.getByRole("region", { name: "SLA workflow", exact: true });
  await panel
    .getByRole("button", { name: "Start SLA review", exact: true })
    .click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Needs case information",
  );
  await expect(panel).toContainText("incident ended at");
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await page.getByRole("button", { name: /INC-104 Intermittent/ }).click();
  panel = page.getByRole("region", { name: "SLA 工作流", exact: true });
  await expect(panel.locator(".workflow-status")).toHaveText("需要案例信息");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "/tmp/northstar-unit6-mobile.png",
    fullPage: true,
  });
  await panel.getByRole("button", { name: "取消工作流", exact: true }).click();
  await expect(panel.locator(".workflow-status")).toHaveText("已取消");
  await expect(panel.locator(".receipt-card")).toHaveCount(0);
});
