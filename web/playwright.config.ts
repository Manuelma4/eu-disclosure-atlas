import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests of the website against a fixture API: the real FastAPI app over a synthetic lake
 * (scripts/serve-fixture.mjs), so the typed client, SSE, Parquet export and price endpoint are all exercised.
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://127.0.0.1:3005",
    locale: "en-GB",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "node scripts/serve-fixture.mjs",
    url: "http://127.0.0.1:3005/en",
    timeout: 300_000,
    reuseExistingServer: !process.env.CI,
    stdout: "ignore",
    stderr: "pipe",
  },
});
