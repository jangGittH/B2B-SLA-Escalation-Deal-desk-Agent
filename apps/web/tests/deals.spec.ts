import { test, expect, type Page } from "@playwright/test";
async function switchEmployee(page: Page, name: string) {
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: new RegExp(name) })
    .click();
  await expect(page.locator(".profile")).toContainText(name);
}
async function desk(page: Page) {
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await switchEmployee(page, "Alex Wang");
  await page.locator("nav .nav-item").filter({ hasText: "Deal desk" }).click();
  await expect(
    page.getByRole("heading", { name: "Deal desk", exact: true }),
  ).toBeVisible();
}

test("new request pauses for facts, owner corrects terms, independent reviewer rejects with feedback", async ({
  page,
}) => {
  await desk(page);
  const id = `DEAL-UI-${Date.now()}`;
  await page.getByRole("button", { name: "New deal", exact: true }).click();
  await page.getByRole("textbox", { name: "Deal ID", exact: true }).fill(id);
  await page
    .getByRole("textbox", { name: "Title", exact: true })
    .fill("Browser renewal request");
  await page
    .getByRole("button", { name: "Save deal request", exact: true })
    .click();
  const panel = page.getByRole("region", {
    name: "Deal workflow",
    exact: true,
  });
  await panel.getByRole("button", { name: "Start deal review" }).click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Needs case information",
  );
  await expect(panel).toContainText("requested discount");
  await page.getByRole("button", { name: "Edit request", exact: true }).click();
  await page
    .getByRole("spinbutton", { name: "Requested discount (%)", exact: true })
    .fill("10");
  await page
    .getByRole("textbox", { name: "Change reason", exact: true })
    .fill("Confirmed commercial terms");
  await page
    .getByRole("button", { name: "Save deal request", exact: true })
    .click();
  await panel
    .getByRole("button", { name: "Recheck case and continue" })
    .click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Waiting for review",
  );
  await expect(
    panel.getByRole("button", { name: "Approve sandbox contract" }),
  ).toHaveCount(0);
  await switchEmployee(page, "Lin Zhao");
  await page.getByRole("button", { name: new RegExp(id) }).click();
  await panel
    .getByRole("textbox", { name: "Review reason or feedback", exact: true })
    .fill("Need independent commercial evidence");
  await panel.getByRole("button", { name: "Reject with reason" }).click();
  await expect(panel.locator(".workflow-status")).toHaveText("Rejected");
  await expect(panel).toContainText("Need independent commercial evidence");
  await expect(panel.locator(".receipt-card")).toHaveCount(0);
});

test("modified renewal previews exact price, updates sandbox once and survives reload", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await desk(page);
  await page.getByRole("button", { name: /^DEAL-101/ }).click();
  const panel = page.getByRole("region", {
    name: "Deal workflow",
    exact: true,
  });
  await panel.getByRole("button", { name: "Start deal review" }).click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Waiting for review",
  );
  await expect(panel).toContainText("USD 900.00");
  await switchEmployee(page, "Lin Zhao");
  await page.getByRole("button", { name: /^DEAL-101/ }).click();
  await panel.getByText("Read the review memo").click();
  await expect(panel.locator("pre")).toContainText("Monthly list price / cost");
  await panel
    .getByRole("spinbutton", {
      name: "Discount percentage to approve",
      exact: true,
    })
    .fill("5");
  await panel
    .getByRole("textbox", { name: "Review reason or feedback", exact: true })
    .fill("Approve a smaller concession");
  await panel.getByRole("button", { name: "Preview modified terms" }).click();
  await expect(panel.locator(".credit-preview")).toContainText("USD 950.00");
  await page.screenshot({
    path: "/tmp/northstar-unit7-review.png",
    fullPage: true,
  });
  await panel.getByRole("button", { name: "Modify and approve" }).click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Completed and verified",
  );
  await expect(panel.locator(".receipt-card")).toContainText("USD 950.00");
  await expect(panel.locator(".receipt-card")).toContainText(
    "Contract version: 2",
  );
  await page.reload();
  await switchEmployee(page, "Alex Wang");
  await page.locator("nav .nav-item").filter({ hasText: "Deal desk" }).click();
  await page.getByRole("button", { name: /^DEAL-101/ }).click();
  await expect(panel.locator(".workflow-status")).toHaveText(
    "Completed and verified",
  );
  await expect(
    panel.getByRole("button", { name: "Start deal review" }),
  ).toBeDisabled();
  await expect(page.locator(".deal-summary")).toContainText("USD 950.00");
  await page.screenshot({
    path: "/tmp/northstar-unit7-closure.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("cost attestation is restricted to Operations, tenant data is isolated and Chinese mobile works", async ({
  page,
}) => {
  await desk(page);
  await page.getByRole("button", { name: /^DEAL-102/ }).click();
  await expect(
    page.getByText("Attest policy cost (Operations)", { exact: true }),
  ).toHaveCount(0);
  await switchEmployee(page, "Lin Zhao");
  await page.getByRole("button", { name: /^DEAL-102/ }).click();
  await page
    .getByText("Attest policy cost (Operations)", { exact: true })
    .click();
  await page
    .getByRole("spinbutton", { name: "Confirmed monthly cost", exact: true })
    .fill("650.00");
  await page
    .getByRole("textbox", { name: "Cost evidence", exact: true })
    .fill("Fictional Operations cost confirmation");
  await page
    .getByRole("textbox", { name: "Cost change reason", exact: true })
    .fill("Reviewed updated synthetic cost");
  await page
    .getByRole("button", { name: "Save cost attestation", exact: true })
    .click();
  await expect(page.locator(".deal-summary")).toContainText(
    "renewal-standard · v2",
  );
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "交易审核", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "/tmp/northstar-unit7-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await switchEmployee(page, "Sam Lee");
  await expect(page.getByRole("button", { name: /^OTHER-DEAL/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^DEAL-10/ })).toHaveCount(0);
});
