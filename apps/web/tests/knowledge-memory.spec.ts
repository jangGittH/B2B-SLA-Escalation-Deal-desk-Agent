import { test, expect } from "@playwright/test";

test("private evidence upload, revision and deletion survive reload and reject other employees", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page
    .locator("nav .nav-item")
    .filter({ hasText: "Policies & evidence" })
    .click();
  await page.getByText("Upload or revise evidence", { exact: true }).click();
  await page
    .getByLabel("Evidence title", { exact: true })
    .fill("Browser evidence v1");
  await page
    .getByLabel("Evidence file", { exact: true })
    .setInputFiles({
      name: "browser.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "# Browser policy\nZephyrdiscount requires human review.",
      ),
    });
  await page
    .getByRole("button", { name: "Upload evidence", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("Saved evidence v1");
  const ident = await page
    .getByLabel("Existing upload ID (blank for new)", { exact: true })
    .inputValue();
  await page
    .getByLabel("Evidence title", { exact: true })
    .fill("Browser evidence v2");
  await page
    .getByLabel("Evidence file", { exact: true })
    .setInputFiles({
      name: "browser.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "# Browser policy\nZephyrdiscount is a proposal, never an approved price.",
      ),
    });
  await page
    .getByRole("button", { name: "Save evidence revision", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("Saved evidence v2");
  await page.reload();
  await page
    .locator("nav .nav-item")
    .filter({ hasText: "Policies & evidence" })
    .click();
  await expect(
    page.getByRole("button", { name: /Browser evidence v2/ }),
  ).toBeVisible();
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Alex Wang/ })
    .click();
  await expect(
    page.getByRole("button", { name: /Browser evidence v2/ }),
  ).toHaveCount(0);
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Mei Chen/ })
    .click();
  await page.getByText("Upload or revise evidence", { exact: true }).click();
  await page
    .getByLabel("Existing upload ID (blank for new)", { exact: true })
    .fill(ident);
  await page.getByLabel("Expected document version", { exact: true }).fill("2");
  await page
    .getByRole("button", { name: "Delete uploaded evidence", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("Evidence deleted");
  await expect(
    page.getByRole("button", { name: /Browser evidence v2/ }),
  ).toHaveCount(0);
});

test("human confirms, corrects and deletes a persistent preference; Chinese mobile controls fit", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page
    .locator("nav .nav-item")
    .filter({ hasText: "Policies & evidence" })
    .click();
  await page.getByText("Preferences and memory", { exact: true }).click();
  await page
    .getByRole("combobox", { name: "Memo layout", exact: true })
    .selectOption("evidence_first");
  await page
    .getByRole("button", { name: "Propose preference", exact: true })
    .click();
  await expect(page.locator(".memory-card")).toContainText("pending");
  await page
    .getByRole("button", { name: "Confirm memory", exact: true })
    .click();
  await expect(page.locator(".memory-card")).toContainText("active");
  await page.reload();
  await page
    .locator("nav .nav-item")
    .filter({ hasText: "Policies & evidence" })
    .click();
  await page.getByText("Preferences and memory", { exact: true }).click();
  await expect(page.locator(".memory-card")).toContainText("Evidence first");
  await page
    .getByRole("combobox", { name: "Memo layout", exact: true })
    .selectOption("overview_first");
  await page
    .getByRole("button", { name: "Replace with selected layout", exact: true })
    .click();
  await expect(page.locator(".memory-card")).toContainText("pending");
  await page
    .getByRole("button", { name: "Confirm memory", exact: true })
    .click();
  await page.screenshot({
    path: "/tmp/northstar-unit9-memory.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await page.getByText("偏好与记忆", { exact: true }).click();
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
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "/tmp/northstar-unit9-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "删除记忆", exact: true }).click();
  await expect(page.getByText("暂无记忆。", { exact: true })).toBeVisible();
});
