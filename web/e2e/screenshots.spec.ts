import { expect, test } from "@playwright/test";

import { ROBOTICS_LEI } from "./helpers";

/** Screenshots for docs and pull requests (opt-in): SCREENSHOTS=1 npx playwright test screenshots */
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to refresh docs/img/website");
test.use({ viewport: { width: 1280, height: 860 } });
const out = (name: string) => `../docs/img/website/${name}.jpg`;
const shot = { type: "jpeg", quality: 82 } as const;

test("explore", async ({ page }) => {
  await page.goto("/en/explore");
  await expect(page.getByTestId("explore-status")).toContainText(/loaded in/, { timeout: 30_000 });
  const plot = page.locator(".explorer-plot svg").first();
  const box = (await plot.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.86, box.y + box.height * 0.5, { steps: 6 });
  await page.mouse.up();
  await page.getByRole("button", { name: "CEO", exact: true }).click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: out("explore"), ...shot, fullPage: true });
});

test("company", async ({ page }) => {
  await page.goto(`/en/company/${ROBOTICS_LEI}`);
  const chart = page.getByTestId("price-chart");
  await chart.locator("canvas").first().waitFor();
  await chart.focus();
  await page.keyboard.press("End");
  await page.keyboard.press("PageUp");
  await page.keyboard.press("PageUp");
  await page.waitForTimeout(400);
  await page.screenshot({ path: out("company"), ...shot });
});

test("search", async ({ page }) => {
  await page.goto("/en");
  await expect(async () => {
    await page.keyboard.press("Control+K");
    await expect(page.getByRole("combobox", { name: "Search" })).toBeFocused({ timeout: 1000 });
  }).toPass();
  await page.getByRole("combobox", { name: "Search" }).fill("ex");
  await expect(page.getByRole("option").first()).toBeVisible();
  await page.waitForTimeout(300);
  await page.screenshot({ path: out("search"), ...shot });
});

test.describe("dark", () => {
  test.use({ colorScheme: "dark" });
  test("home in French, dark mode", async ({ page }) => {
    await page.goto("/fr");
    await expect(page.getByTestId("buy-30d")).toBeVisible();
    await page.waitForTimeout(500);
    await page.screenshot({ path: out("home-fr-dark"), ...shot });
  });
  test("explore in German, dark mode", async ({ page }) => {
    await page.goto("/de/explore");
    await expect(page.getByTestId("explore-status")).toContainText(/\d/, { timeout: 30_000 });
    await expect(page.locator(".explorer-plot svg")).toHaveCount(3);
    await page.waitForTimeout(500);
    await page.screenshot({ path: out("explore-de-dark"), ...shot });
  });
});
