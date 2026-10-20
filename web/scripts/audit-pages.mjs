// Responsive and console audit of the website (development tool, not part of CI).
//   node scripts/audit-pages.mjs <base-url> <out-dir> [company-lei] [--locales=en,fr,de] [--shots]
// For every page, viewport and locale: horizontal overflow (with the widest offending elements), console
// errors and warnings, failed requests and page errors. With --shots, screenshots at 390 and 1440 px (en).
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "@playwright/test";

const [base, out, lei = "549300EXAMPLE0000073"] = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const locales = (process.argv.find((a) => a.startsWith("--locales="))?.split("=")[1] ?? "en,fr,de").split(",");
const shots = process.argv.includes("--shots");
const VIEWPORTS = [
  [360, 740],
  [390, 844],
  [768, 1024],
  [1024, 768],
  [1440, 900],
];
const PAGES = {
  overview: "",
  filings: "/filings",
  explore: "/explore",
  company: `/company/${lei}`,
  architecture: "/architecture",
  watchlist: "/account/watchlist",
  notfound: "/no-such-page",
};

mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const report = [];
for (const locale of locales) {
  for (const [width, height] of VIEWPORTS) {
    const context = await browser.newContext({ viewport: { width, height }, isMobile: width < 768, hasTouch: width < 1024 });
    for (const [name, path] of Object.entries(PAGES)) {
      const page = await context.newPage();
      const issues = [];
      page.on("console", (m) => {
        // The not-found page is served with status 404, which the browser logs for the document itself.
        if (name === "notfound" && m.text().includes("status of 404")) return;
        if (["error", "warning"].includes(m.type())) issues.push(`console.${m.type()}: ${m.text().slice(0, 240)}`);
      });
      page.on("pageerror", (e) => issues.push(`pageerror: ${e.message.slice(0, 240)}`));
      page.on("requestfailed", (r) => !r.url().includes("/v1/stream") && issues.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`));
      page.on("response", (r) => r.status() >= 400 && !(name === "notfound" && r.request().resourceType() === "document") && issues.push(`http ${r.status()}: ${r.url()}`));
      await page.goto(`${base}/${locale}${path}`, { waitUntil: "load" });
      await page.waitForTimeout(name === "explore" ? 6000 : 1500);
      const overflow = await page.evaluate(() => {
        const vw = document.documentElement.clientWidth;
        const sw = document.documentElement.scrollWidth;
        const culprits = [...document.querySelectorAll("body *")]
          .filter((el) => {
            const r = el.getBoundingClientRect();
            if (r.right <= vw + 1 || r.width === 0) return false;
            // ignore content inside a horizontally scrollable ancestor
            for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
              const s = getComputedStyle(p);
              if (["auto", "scroll", "hidden", "clip"].includes(s.overflowX) && p.getBoundingClientRect().right <= vw + 1) return false;
            }
            return true;
          })
          .slice(0, 6)
          .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 50)} right=${Math.round(el.getBoundingClientRect().right)}`);
        return { vw, sw, culprits };
      });
      const small = await page.evaluate(() =>
        [...document.querySelectorAll("a[href], button, select, input, [role=button], [tabindex='0']")]
          .filter((el) => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (r.height < 24 || r.width < 24) && getComputedStyle(el).visibility !== "hidden";
          })
          .slice(0, 5)
          .map((el) => `${el.tagName.toLowerCase()} "${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 30)}" ${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`),
      );
      report.push({ locale, viewport: `${width}x${height}`, page: name, overflow: overflow.sw > overflow.vw ? overflow : null, issues: [...new Set(issues)], smallTargets: width < 768 ? small : [] });
      if (shots && locale === "en" && (width === 390 || width === 1440)) {
        await page.screenshot({ path: join(out, `${name}-${width}.jpg`), type: "jpeg", quality: 70, fullPage: true });
      }
      await page.close();
    }
    await context.close();
  }
}
await browser.close();
writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 2));
const bad = report.filter((r) => r.overflow || r.issues.length);
for (const r of bad) {
  console.log(`${r.locale} ${r.viewport} ${r.page}: ${r.overflow ? `overflow ${r.overflow.sw}>${r.overflow.vw} [${r.overflow.culprits.join("; ")}]` : ""} ${r.issues.join(" | ")}`);
}
console.log(`${report.length} page views, ${report.filter((r) => r.overflow).length} with horizontal overflow, ${report.filter((r) => r.issues.length).length} with console or network issues`);
