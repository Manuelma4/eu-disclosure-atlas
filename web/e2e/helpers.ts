import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** LEI of the busiest synthetic issuer ("Example Robotics AB (publ)") in e2e/fixture_lake.py. */
export const ROBOTICS_LEI = "549300EXAMPLE0000073";

/** WCAG 2.2 AA scan; fails on any serious or critical violation. */
export async function expectAccessible(page: Page, exclude: string[] = []) {
  let builder = new AxeBuilder({ page: page as never }) // axe types target a newer playwright-core
   .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]);
  for (const selector of exclude) builder = builder.exclude(selector);
  const { violations } = await builder.analyze();
  const blocking = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(
    blocking.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")})`),
  ).toEqual([]);
}
