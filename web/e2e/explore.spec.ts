import { expect, test, type Page } from "@playwright/test";

import { expectAccessible } from "./helpers";

const selected = (page: Page) => page.getByTestId("explore-selected");
const count = async (page: Page) => {
  const text = (await selected(page).textContent()) ?? "";
  const [a, b] = text.match(/[\d,]+/g)!.map((n) => Number(n.replaceAll(",", "")));
  return { selected: a, total: b };
};

/** The selection count once brushing has finished: the summary updates live during a drag, so wait until two
 * reads 400 ms apart agree. */
async function settled(page: Page) {
  let previous = -1;
  await expect
    .poll(async () => {
      const { selected: now } = await count(page);
      const stable = now === previous;
      previous = now;
      return stable;
    }, { intervals: [400] })
    .toBe(true);
  return previous;
}

async function ready(page: Page) {
  await page.goto("/en/explore");
  await expect(page.getByTestId("explore-status")).toContainText(/filings loaded in/, { timeout: 30_000 });
  await expect(selected(page)).toContainText("4,000 of 4,000");
}

test("loads every filing into DuckDB-WASM from our own origin only", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => {
    const host = new URL(request.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) external.push(request.url());
  });
  await ready(page);
  await expect(page.locator(".explorer-plot svg")).toHaveCount(3);
  expect(external).toEqual([]);
});

test("brushing the weekly chart cross-filters the other views", async ({ page }) => {
  await ready(page);
  const plot = page.locator(".explorer-plot svg").first();
  const box = (await plot.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.5, { steps: 8 });
  await page.mouse.up();
  const brushed = await settled(page);
  expect(brushed).toBeGreaterThan(0);
  expect(brushed).toBeLessThan(4000);

  await page.getByRole("button", { name: "Show table" }).nth(1).click();
  const roleTotals = page.getByRole("table", { name: "By role" }).locator("tbody tr td:last-child");
  await expect
    .poll(async () => (await roleTotals.allInnerTexts()).reduce((sum, n) => sum + Number(n.replaceAll(",", "")), 0))
    .toBe(brushed);

  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(selected(page)).toContainText("4,000 of 4,000");
});

test("roles filter with the keyboard", async ({ page }) => {
  await ready(page);
  const ceo = page.getByRole("button", { name: "CEO", exact: true });
  await ceo.focus();
  await page.keyboard.press("Enter");
  await expect(ceo).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => (await count(page)).selected).toBeLessThan(1000);
  await page.keyboard.press("Enter");
  await expect(selected(page)).toContainText("4,000 of 4,000");
});

test("explore page is accessible", async ({ page }) => {
  await ready(page);
  await expectAccessible(page);
});
