import { expect, test } from "@playwright/test";

import { expectAccessible, ROBOTICS_LEI } from "./helpers";

test.beforeEach(async ({ page }) => {
  await page.goto(`/en/company/${ROBOTICS_LEI}`);
});

test("company page shows the issuer, its price chart and filings", async ({ page }) => {
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Example Robotics AB (publ)");
  await expect(page.getByText(`LEI ${ROBOTICS_LEI}`)).toBeVisible();
  const chart = page.getByTestId("price-chart");
  await expect(chart.locator("canvas").first()).toBeVisible();
  await expect(page.getByRole("list", { name: "Legend" }).first()).toContainText("Insider buying");
  await expect(page.getByText(/prices: synthetic/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Who files" })).toBeVisible();
});

test("price chart is keyboard navigable and announces each day", async ({ page }) => {
  const chart = page.getByTestId("price-chart");
  await chart.locator("canvas").first().waitFor();
  await chart.focus();
  await page.keyboard.press("End");
  const status = chart.getByRole("status");
  await expect(status).toContainText("Close");
  const last = await status.textContent();
  await page.keyboard.press("ArrowLeft");
  await expect(status).not.toHaveText(last ?? "");
  await page.keyboard.press("PageUp");
  await expect(status).toContainText(/Bought|Sold/);
});

test("price chart has a table view of insider days", async ({ page }) => {
  await page.getByRole("button", { name: "Show table" }).first().click();
  const table = page.getByRole("table", { name: "Days with open-market insider trades and the closing price" });
  await expect(table).toBeVisible();
  expect(await table.getByRole("row").count()).toBeGreaterThan(5);
});

test("unknown issuers are a 404 page", async ({ page }) => {
  await page.goto("/en/company/549300XXXXXXXXXXXX00");
  await expect(page.getByRole("heading", { name: "Not in the Atlas" })).toBeVisible();
});

test("company page is accessible", async ({ page }) => {
  await page.getByTestId("price-chart").locator("canvas").first().waitFor();
  await expectAccessible(page);
});
