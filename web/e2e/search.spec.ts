import { expect, test, type Page } from "@playwright/test";

import { ROBOTICS_LEI } from "./helpers";

/** Ctrl+K works once the header has hydrated; retry the shortcut until the palette opens. */
async function openPalette(page: Page) {
  await expect(async () => {
    await page.keyboard.press("Control+K");
    await expect(page.getByRole("combobox", { name: "Search" })).toBeFocused({ timeout: 1000 });
  }).toPass();
}

test("Ctrl+K finds an issuer and opens its page with the keyboard", async ({ page }) => {
  await page.goto("/en");
  await openPalette(page);
  const input = page.getByRole("combobox", { name: "Search" });
  await expect(input).toBeFocused();
  await input.fill("robotics");
  const option = page.getByRole("option", { name: /Example Robotics AB/ });
  await expect(option).toBeVisible();
  await expect(option).toHaveAttribute("aria-selected", "true");
  await expect(input).toHaveAttribute("aria-activedescendant", /.+/);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/en/company/${ROBOTICS_LEI}$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Example Robotics AB (publ)");
});

test("the palette lists pages, moves with arrows and closes with Escape", async ({ page }) => {
  await page.goto("/en/filings");
  await page.getByRole("button", { name: "Search companies" }).click();
  const dialog = page.getByRole("dialog", { name: "Search companies and pages" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("option")).toHaveCount(4);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(dialog.getByRole("option", { name: "Explore" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("an unknown name says so", async ({ page }) => {
  await page.goto("/en");
  await openPalette(page);
  await page.getByRole("combobox", { name: "Search" }).fill("zzzz-nothing");
  await expect(page.getByText("No company matches")).toBeVisible();
});
