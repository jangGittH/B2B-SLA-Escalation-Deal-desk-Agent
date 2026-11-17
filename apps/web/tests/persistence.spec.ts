import { test, expect } from "@playwright/test";

test("saved conversation survives reload and stays private", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("combobox", { name: "Saved conversations" }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Ask your assistant" })
    .fill("What is the Acme response target? saved test");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".answer-text")).toContainText("60 minutes");
  const selected = await page
    .getByRole("combobox", { name: "Saved conversations" })
    .inputValue();
  expect(selected).not.toBe("");
  await page.reload();
  await expect(page.locator(".answer-text")).toContainText("60 minutes");
  await expect(
    page.getByRole("combobox", { name: "Saved conversations" }),
  ).toHaveValue(selected);
  await page.screenshot({
    path: "/tmp/northstar-unit4-chat.png",
    fullPage: true,
  });
  await page.locator(".profile").click();
  await page
    .locator(".employee-list")
    .getByRole("button", { name: /Alex Wang/ })
    .click();
  await expect(page.locator(".profile")).toContainText("Alex Wang");
  await expect(page.locator(".answer-text")).toHaveCount(0);
  await expect(
    page
      .getByRole("combobox", { name: "Saved conversations" })
      .locator(`option[value="${selected}"]`),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("slow saved history can be abandoned without locking send or overwriting a new turn", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/v1/conversations', async route => {
    if (route.request().method() !== 'GET') return route.continue();
    await route.fulfill({json:{conversations:[{id:'slow-history',title:'Slow history',language:'en'}]}});
  });
  await page.route('**/v1/conversations/slow-history', async route => {
    await held;
    await route.fulfill({json:{runs:[{id:'old',request:{question:'Old question'},result:{answer:'Old answer',sources:[],tool_trace:[],files:[]}}]}});
  });
  try {
    await page.goto('/');
    await expect(page.getByRole('status')).toContainText('Loading saved conversation');
    await expect(page.locator('.send .spin')).toHaveCount(0);
    await page.getByRole('button',{name:'New conversation',exact:true}).click();
    await page.getByRole('textbox',{name:'Ask your assistant'}).fill('What is the Acme response target?');
    await expect(page.getByRole('button',{name:'Send question'})).toBeEnabled();
    await page.getByRole('button',{name:'Send question'}).click();
    await expect(page.locator('.answer-text')).toContainText('60 minutes');
    release();
    await expect(page.getByText('Old answer',{exact:true})).toHaveCount(0);
    await page.getByRole('textbox',{name:'Ask your assistant'}).fill('Another question');
    await expect(page.getByRole('button',{name:'Send question'})).toBeEnabled();
  } finally { release(); }
});
