import { expect, test, type Page } from "@playwright/test";

import { expectAccessible, ROBOTICS_LEI } from "./helpers";

/** Runs in the "phone" and "tablet" projects (playwright.config.ts). */
const PAGES = {
  overview: "/en",
  filings: "/en/filings",
  explore: "/en/explore",
  company: `/en/company/${ROBOTICS_LEI}`,
  architecture: "/en/architecture",
  watchlist: "/en/account/watchlist",
  "not found": "/en/no-such-page",
  "overview (German)": "/de",
  "filings (French)": "/fr/filings",
};

async function expectNoHorizontalScroll(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(scroll, "page is wider than the viewport").toBeLessThanOrEqual(client);
}

for (const [name, path] of Object.entries(PAGES)) {
  test(`${name} has no horizontal scroll`, async ({ page }) => {
    await page.goto(path);
    await expect(page.locator("main")).toBeVisible();
    if (path.endsWith("/explore")) {
      await expect(page.getByTestId("explore-status")).toContainText(/loaded in|geladen|chargées/, { timeout: 30_000 });
      await expect(page.locator(".explorer-plot svg")).toHaveCount(3);
    }
    await expectNoHorizontalScroll(page);
  });
}

test("the menu button opens the navigation, moves focus into it and closes with Escape", async ({ page, isMobile }) => {
  const width = page.viewportSize()!.width;
  test.skip(!isMobile || width >= 768, "the inline navigation is shown from 768 px");
  await page.goto("/en/filings");
  const button = page.getByRole("button", { name: "Open menu" });
  await expect(button).toBeVisible();
  await expect(page.getByRole("link", { name: "Explore" })).toBeHidden();
  await button.click();
  const close = page.getByRole("button", { name: "Close menu" });
  await expect(close).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Search companies" })).toBeFocused();
  await expect(page.getByRole("combobox", { name: "Language" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
  await expect(page.getByRole("link", { name: "Explore" })).toBeHidden();

  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Explore" }).click();
  await expect(page).toHaveURL(/\/en\/explore$/);
  await expect(page.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "false");
});

test("search opens from the menu on phones and from the header on tablets", async ({ page }) => {
  await page.goto("/en");
  if (await page.getByRole("button", { name: "Open menu" }).isVisible()) {
    await page.getByRole("button", { name: "Open menu" }).click();
  }
  await page.getByRole("button", { name: "Search companies" }).click();
  const input = page.getByRole("combobox", { name: "Search" });
  await expect(input).toBeFocused();
  await input.fill("robotics");
  await page.getByRole("option", { name: /Example Robotics AB/ }).click();
  await expect(page).toHaveURL(new RegExp(`/en/company/${ROBOTICS_LEI}$`));
});

test("controls are at least 44 px tall on touch screens", async ({ page }) => {
  await page.goto("/en/filings");
  if (await page.getByRole("button", { name: "Open menu" }).isVisible()) {
    await page.getByRole("button", { name: "Open menu" }).click();
  }
  const small = await page.evaluate(() =>
    [...document.querySelectorAll("button, select, input:not([type=checkbox]), [role=button]")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.height < 44;
      })
      .map((el) => `${el.tagName} ${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 30)}`),
  );
  expect(small).toEqual([]);
});

test("filings stack into rows on phones and stay a table on tablets", async ({ page }) => {
  await page.goto("/en/filings");
  const width = page.viewportSize()!.width;
  if (width < 640) {
    await expect(page.getByTestId("filings-list")).toBeVisible();
    await expect(page.locator("table")).toBeHidden();
  } else {
    await expect(page.locator("table")).toBeVisible();
  }
  await expectNoHorizontalScroll(page);
});

test("chart tooltips open on tap", async ({ page }) => {
  await page.goto("/en");
  const bar = page.getByRole("group", { name: /Insider buying and selling/ }).locator("rect[tabindex='0']").last();
  await bar.tap();
  await expect(page.getByRole("status").filter({ hasText: "Net" })).toBeVisible();
  // It stays open after the finger lifts, and a tap elsewhere closes it.
  await page.waitForTimeout(300);
  await expect(page.getByRole("status").filter({ hasText: "Net" })).toBeVisible();
  await page.getByRole("heading", { level: 1 }).tap();
  await expect(page.getByRole("status").filter({ hasText: "Net" })).toBeHidden();
});

test("the price chart stays inside its card and shows the tapped day", async ({ page }) => {
  await page.goto(`/en/company/${ROBOTICS_LEI}`);
  const chart = page.getByTestId("price-chart");
  const canvas = chart.locator("canvas").first();
  await canvas.waitFor();
  const card = await chart.boundingBox();
  const viewport = page.viewportSize()!;
  expect(card!.x + card!.width).toBeLessThanOrEqual(viewport.width);
  await chart.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  await page.touchscreen.tap(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await expect(chart.getByRole("status")).toContainText("Close");
  await expectNoHorizontalScroll(page);
});

test("overview and filings are accessible on touch screens", async ({ page }) => {
  for (const path of ["/en", "/en/filings"]) {
    await page.goto(path);
    await expect(page.locator("main")).toBeVisible();
    await expectAccessible(page);
  }
});
