import { test, expect } from "@playwright/test";

async function cases(page: any) {
  await page.goto("/");
  await expect(page.locator(".profile")).toContainText("Mei Chen");
  await page.locator("nav .nav-item").filter({ hasText: "SLA cases" }).click();
  await expect(
    page.getByRole("button", { name: /INC-105 Sandbox/ }),
  ).toBeEnabled();
}

test("SLA calculation, private memo edits, revisions and authenticated export survive reload", async ({
  page,
}) => {
  await cases(page);
  await page.getByRole("button", { name: /INC-105 Sandbox/ }).click();
  await expect(page.locator(".proposal-amount")).toHaveText("USD 45.00");
  await expect(page.locator(".calculation-card")).toContainText(
    "Not approved · Not issued",
  );
  await page
    .getByRole("textbox", { name: "Hypotheses — unverified", exact: true })
    .fill("Maintenance correlation remains a hypothesis.");
  await page
    .getByRole("button", { name: "Save private memo", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("Private memo saved");
  const id = await page
    .getByRole("combobox", { name: "Saved memos", exact: true })
    .inputValue();
  await page
    .getByRole("textbox", { name: "Hypotheses — unverified", exact: true })
    .fill("Human edited hypothesis.");
  await page
    .getByRole("button", { name: "Save memo revision", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /Download saved memo/ }),
  ).toContainText("v2");
  await page
    .getByRole("combobox", { name: "Previous revision", exact: true })
    .selectOption("1");
  await page
    .getByRole("button", { name: "Restore notes as new revision", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Hypotheses — unverified", exact: true }),
  ).toHaveValue("Maintenance correlation remains a hypothesis.");
  await expect(
    page.getByRole("button", { name: /Download saved memo/ }),
  ).toContainText("v3");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /Download saved memo/ }).click();
  expect((await download).suggestedFilename()).toBe("INC-105-memo-v3.docx");
  await page.reload();
  await page.locator("nav .nav-item").filter({ hasText: "SLA cases" }).click();
  await page.getByRole("button", { name: /INC-105 Sandbox/ }).click();
  await page
    .getByRole("combobox", { name: "Saved memos", exact: true })
    .selectOption(id);
  await expect(
    page.getByRole("textbox", { name: "Hypotheses — unverified", exact: true }),
  ).toHaveValue("Maintenance correlation remains a hypothesis.");
  await page.screenshot({
    path: "/tmp/northstar-unit5-cases.png",
    fullPage: true,
  });
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Alex Wang/ })
    .click();
  await page.getByRole("button", { name: /INC-105 Sandbox/ }).click();
  await expect(
    page
      .getByRole("combobox", { name: "Saved memos", exact: true })
      .locator(`option[value="${id}"]`),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Edit incident facts", exact: true }),
  ).toBeDisabled();
});

test("SLA intake and audited edits persist; unknown input is visible", async ({
  page,
}) => {
  await cases(page);
  await page.getByRole("button", { name: /INC-104 Intermittent/ }).click();
  await expect(page.locator(".proposal-amount")).toHaveText(
    "More evidence needed",
  );
  await expect(page.locator(".calculation-card")).toContainText(
    "eligibility confirmed",
  );
  await page.getByRole("button", { name: "New case", exact: true }).click();
  const id = `UI-${Date.now()}`;
  await page.getByLabel("Case ID", { exact: true }).fill(id);
  await page
    .getByLabel("Title", { exact: true })
    .fill("Browser-created investigation");
  await page
    .getByLabel("Reported at (UTC)", { exact: true })
    .fill("2026-11-15T12:00");
  await page.getByRole("button", { name: "Save case", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Case saved.");
  await page
    .getByRole("button", { name: "Edit incident facts", exact: true })
    .click();
  await page
    .getByLabel("Title", { exact: true })
    .fill("Browser-edited investigation");
  await page
    .getByLabel("Reason for change", { exact: true })
    .fill("Support clarified the incident title");
  await page.getByRole("button", { name: "Save case", exact: true }).click();
  await expect(page.locator(".case-detail").first()).toContainText(
    "Browser-edited investigation · v2",
  );
  await page.reload();
  await page.locator("nav .nav-item").filter({ hasText: "SLA cases" }).click();
  await page.getByLabel("Find a case", { exact: true }).fill(id);
  await page.getByRole("button", { name: "Filter cases", exact: true }).click();
  await expect(page.locator(".case-table tbody tr")).toHaveCount(1);
  await expect(page.locator(".case-table")).toContainText(
    "Browser-edited investigation",
  );
});

test("case tenant isolation, Chinese interface and mobile editor", async ({
  page,
}) => {
  await cases(page);
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Sam Lee/ })
    .click();
  await expect(page.locator(".case-table")).toContainText("OTHER-1");
  await expect(page.locator(".case-table")).not.toContainText("INC-105");
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "SLA 案例", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: /OTHER-1/ }).click();
  await page.getByRole("button", { name: "编辑事件事实", exact: true }).click();
  await expect(
    page.getByLabel("报告时间（UTC）", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "/tmp/northstar-unit5-mobile.png",
    fullPage: true,
  });
});
