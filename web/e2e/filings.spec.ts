import { expect, test } from "@playwright/test";

import { expectAccessible } from "./helpers";

test("filters filings by nature and paginates", async ({ page }) => {
  await page.goto("/en/filings");
  await expect(page.getByRole("heading", { name: "Filings", level: 1 })).toBeVisible();
  await page.getByLabel("Nature").selectOption("sell");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/nature=sell/);
  const natures = page.locator("tbody tr td:nth-child(5)");
  await expect(natures.first()).toContainText("Sell");
  expect(new Set(await natures.allInnerTexts())).toEqual(new Set(["Sell"]));
  await page.getByRole("link", { name: /Next 50/ }).click();
  await expect(page).toHaveURL(/cursor=/);
  await expect(natures.first()).toContainText("Sell");
});

test("issuer search narrows the list", async ({ page }) => {
  await page.goto("/en/filings?q=Robotics");
  const issuers = page.locator("tbody tr td:nth-child(3) a");
  await expect(issuers.first()).toHaveText("Example Robotics AB (publ)");
  expect(new Set(await issuers.allInnerTexts())).toEqual(new Set(["Example Robotics AB (publ)"]));
});

test("filings page is accessible", async ({ page }) => {
  await page.goto("/en/filings");
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await expectAccessible(page);
});

test("filings are translated", async ({ page }) => {
  await page.goto("/de/filings?nature=buy");
  await expect(page.locator("html")).toHaveAttribute("lang", "de");
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await expect(page.locator("thead")).not.toContainText("Published");
});
