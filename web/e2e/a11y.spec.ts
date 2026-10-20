import { expect, test } from "@playwright/test";

import { expectAccessible, ROBOTICS_LEI } from "./helpers";

const PAGES = ["/en", "/en/filings", `/en/company/${ROBOTICS_LEI}`, "/en/architecture", "/fr", "/de/explore"];

test.describe("dark mode", () => {
  test.use({ colorScheme: "dark" });
  for (const path of PAGES) {
    test(`${path} has no serious accessibility issue in dark mode`, async ({ page }) => {
      await page.goto(path);
      await expect(page.locator("html")).toHaveCSS("color-scheme", "dark");
      await expect(page.locator("main")).toBeVisible();
      await page.waitForTimeout(500); // client components (charts) settle; SSE keeps the network busy
      await expectAccessible(page);
    });
  }
});

test.describe("reduced motion", () => {
  test.use({ contextOptions: { reducedMotion: "reduce" } });
  test("animations are switched off", async ({ page }) => {
    await page.goto("/en/explore");
    const pulse = page.locator(".animate-pulse").first();
    await expect(pulse).toBeAttached();
    const duration = await pulse.evaluate((el) => parseFloat(getComputedStyle(el).animationDuration));
    expect(duration).toBeLessThan(0.01);
  });
});

test("every interactive element shows a focus ring", async ({ page }) => {
  await page.goto("/en");
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    const outline = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      const style = getComputedStyle(el);
      return { width: parseFloat(style.outlineWidth), style: style.outlineStyle, tag: el.tagName };
    });
    expect(outline.style, `${outline.tag} focus outline`).not.toBe("none");
    expect(outline.width).toBeGreaterThanOrEqual(2);
  }
});
