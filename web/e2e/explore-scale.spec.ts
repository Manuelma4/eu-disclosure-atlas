import { expect, test } from "@playwright/test";

/**
 * Million-row benchmark of the explorer (opt-in): FIXTURE_ROWS=1200000 npx playwright test explore-scale
 * Budgets: ready within 15 s, each cross-filter update within 1 s.
 */
const rows = Number(process.env.FIXTURE_ROWS ?? 0);
test.skip(rows < 1_000_000, "set FIXTURE_ROWS=1200000 to run the million-row benchmark");
test.setTimeout(120_000);

test("explorer stays interactive on more than a million filings", async ({ page }) => {
  page.on("console", (m) => m.type() === "error" && console.log("CONSOLE", m.text().slice(0, 300)));
  page.on("pageerror", (e) => console.log("PAGEERROR", e.message.slice(0, 300)));
  const started = Date.now();
  await page.goto("/en/explore");
  await expect(page.getByTestId("explore-status")).toContainText(/filings loaded in/, { timeout: 60_000 });
  const loadMs = Date.now() - started;
  const selected = page.getByTestId("explore-selected");
  await expect(selected).toContainText(`${rows.toLocaleString("en-GB")} of`);

  const plot = page.locator(".explorer-plot svg").first();
  const box = (await plot.boundingBox())!;
  // Hovering activates Mosaic's pre-aggregation (it builds data-cube indexes for the brush), as a user would.
  const warmT0 = Date.now();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(250);
  const timings: number[] = [];
  for (const [from, to] of [
    [0.3, 0.5],
    [0.5, 0.7],
    [0.7, 0.95],
  ]) {
    const before = await selected.textContent();
    const t0 = Date.now();
    await page.mouse.move(box.x + box.width * from, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * to, box.y + box.height / 2, { steps: 5 });
    await page.mouse.up();
    await expect(selected).not.toHaveText(before ?? "", { timeout: 60_000 });
    timings.push(Date.now() - t0);
  }
  const roleT0 = Date.now();
  await page.getByRole("button", { name: "CFO", exact: true }).click();
  await expect(page.getByRole("button", { name: "CFO", exact: true })).toHaveAttribute("aria-pressed", "true");
  const beforeRole = await selected.textContent();
  await expect(selected).not.toHaveText(beforeRole ?? "", { timeout: 5000 }).catch(() => {});
  timings.push(Date.now() - roleT0);

  console.log(
    `explorer: ${rows} rows ready in ${loadMs} ms; first brush (incl. index build) ${timings[0]} ms after hover at ${warmT0 - started} ms; ` +
      `cross-filter updates ${timings.join(", ")} ms`,
  );
  test.info().annotations.push({ type: "benchmark", description: `ready ${loadMs} ms; updates ${timings.join("/")} ms` });
  expect(loadMs).toBeLessThan(15_000);
  for (const t of timings) expect(t).toBeLessThan(1_000);
});
