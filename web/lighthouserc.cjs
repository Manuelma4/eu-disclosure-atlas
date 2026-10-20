// Lighthouse CI: budgets for the main pages, measured on the production build against the fixture API.
// Run with `npm run lighthouse` (starts scripts/serve-fixture.mjs, uses Playwright's Chromium).
const { chromium } = require("@playwright/test");

const BASE = "http://127.0.0.1:3190";
const ROBOTICS_LEI = "549300EXAMPLE0000073";

module.exports = {
  ci: {
    collect: {
      startServerCommand: "node scripts/serve-fixture.mjs",
      startServerReadyPattern: "fixture site ready",
      startServerReadyTimeout: 300000,
      url: [`${BASE}/en`, `${BASE}/en/filings`, `${BASE}/en/company/${ROBOTICS_LEI}`, `${BASE}/en/explore`, `${BASE}/fr`],
      numberOfRuns: 1,
      chromePath: process.env.CHROME_PATH || chromium.executablePath(),
      settings: {
        preset: "desktop",
        chromeFlags: "--headless=new --no-sandbox",
        // The live tape keeps an SSE connection open; do not wait for network idle forever.
        maxWaitForLoad: 30000,
      },
    },
    assert: {
      // Budgets: Web Vitals, category scores, bytes per resource type and no third-party requests at all.
      assertMatrix: [
        {
          matchingUrlPattern: ".*",
          assertions: {
            "categories:performance": ["error", { minScore: 0.9 }],
            "categories:accessibility": ["error", { minScore: 1 }],
            "categories:best-practices": ["error", { minScore: 0.95 }],
            "categories:seo": ["error", { minScore: 0.9 }],
            "first-contentful-paint": ["error", { maxNumericValue: 1500 }],
            "largest-contentful-paint": ["error", { maxNumericValue: 2500 }],
            "cumulative-layout-shift": ["error", { maxNumericValue: 0.1 }],
            "total-blocking-time": ["error", { maxNumericValue: 200 }],
            "resource-summary:third-party:count": ["error", { maxNumericValue: 0 }],
            "resource-summary:stylesheet:size": ["error", { maxNumericValue: 40 * 1024 }],
            "resource-summary:font:size": ["error", { maxNumericValue: 120 * 1024 }],
          },
        },
        {
          // DuckDB-WASM (about 9 MB of WebAssembly) loads after first paint on the explorer only.
          matchingUrlPattern: "^(?!.*/explore$).*",
          assertions: {
            "resource-summary:script:size": ["error", { maxNumericValue: 300 * 1024 }],
            "resource-summary:total:size": ["error", { maxNumericValue: 600 * 1024 }],
          },
        },
        {
          matchingUrlPattern: ".*/explore$",
          assertions: { "resource-summary:script:size": ["error", { maxNumericValue: 600 * 1024 }] },
        },
      ],
    },
    upload: { target: "filesystem", outputDir: ".lighthouseci" },
  },
};
