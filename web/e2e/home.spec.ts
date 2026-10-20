import { expect, test } from "@playwright/test";

import { expectAccessible } from "./helpers";

test("redirects to the visitor's language and renders the overview", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/en$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("insider trades");
  await expect(page.getByTestId("buy-30d")).toContainText("€");
  await expect(page.getByRole("heading", { name: "Insider flow, weekly" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Legend" }).first()).toContainText("Insider buys");
});

test("weekly flow chart has a tooltip on focus and a table view", async ({ page }) => {
  await page.goto("/en");
  const chart = page.getByRole("group", { name: /Insider buying and selling/ });
  await chart.locator("rect[tabindex='0']").last().focus();
  await expect(page.getByRole("status").filter({ hasText: "Net" })).toBeVisible();
  await page.getByRole("button", { name: "Show table" }).first().click();
  await expect(page.getByRole("columnheader", { name: "Buy trades" })).toBeVisible();
});

test("live tape connects to the stream", async ({ page }) => {
  await page.goto("/en");
  await expect(page.getByTestId("tape-status")).toContainText("Live", { timeout: 15_000 });
});

test("language switch keeps the page and translates it", async ({ page }) => {
  await page.goto("/en/filings");
  await page.getByLabel("Language").selectOption("fr");
  await expect(page).toHaveURL(/\/fr\/filings$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "fr");
  await expect(page.getByRole("navigation", { name: /./ }).first()).not.toContainText("Overview");
});

for (const locale of ["fr", "es", "de"]) {
  test(`overview renders in ${locale}`, async ({ page }) => {
    await page.goto(`/${locale}`);
    await expect(page.locator("html")).toHaveAttribute("lang", locale);
    await expect(page.getByTestId("buy-30d")).toContainText("€");
    await expect(page.locator("body")).not.toContainText("Insider flow, weekly");
  });
}

test("unknown pages render the localized not-found page", async ({ page }) => {
  const response = await page.goto("/en/does-not-exist");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "Not in the Atlas" })).toBeVisible();
});

test("overview is accessible", async ({ page }) => {
  await page.goto("/en");
  await expect(page.getByTestId("tape-status")).toBeVisible();
  await expectAccessible(page);
});

test("skip link moves focus to the content", async ({ page }) => {
  await page.goto("/en");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
});
