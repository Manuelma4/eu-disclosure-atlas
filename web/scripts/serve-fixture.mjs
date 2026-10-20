// Starts the website against a fixture API, for Playwright and Lighthouse:
//   1. writes a synthetic lake (e2e/fixture_lake.py; FIXTURE_ROWS rows, 4,000 by default),
//   2. serves it with the real FastAPI app on :8005 (synthetic prices, CORS for :3005) and waits until healthy,
//   3. builds the site into .next-e2e with `npm run build` (skip with --no-build) and serves it on :3005.
// Python: $ATLAS_PYTHON, else ../.venv/bin/python, else python3 (needs `pip install -e '.[api]'`).
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(web, "..");
const python =
  process.env.ATLAS_PYTHON ?? (existsSync(join(repo, ".venv", "bin", "python")) ? join(repo, ".venv", "bin", "python") : "python3");
const API_PORT = 8005;
const WEB_PORT = 3005;
const api = `http://127.0.0.1:${API_PORT}`;
const lake = join(web, "e2e", ".lake");
const env = {
  ...process.env,
  NEXT_DIST_DIR: ".next-e2e",
  ATLAS_API_URL: api,
  NEXT_PUBLIC_ATLAS_API_URL: api,
  ATLAS_PRICE_PROVIDER: "synthetic",
  ATLAS_CORS_ORIGINS: `http://127.0.0.1:${WEB_PORT},http://localhost:${WEB_PORT}`,
  NEXT_TELEMETRY_DISABLED: "1",
  // Every test shares one client address; the per-IP rate limit is covered by the API test suite.
  ATLAS_RATE_LIMIT_PER_MINUTE: "0",
  NEXT_PUBLIC_SITE_URL: `http://127.0.0.1:${WEB_PORT}`,
};

const run = (cmd, args, opts = {}) => {
  const result = spawnSync(cmd, args, { stdio: "inherit", env, cwd: web, ...opts });
  if (result.status !== 0) process.exit(result.status ?? 1);
};

const children = [];
const stop = (code = 0) => {
  children.forEach((child) => child.kill("SIGTERM"));
  process.exit(code);
};
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
const serve = (cmd, args) => {
  const child = spawn(cmd, args, { stdio: "inherit", env, cwd: web });
  child.on("exit", (code) => code && stop(code));
  children.push(child);
};
const waitFor = async (url, what) => {
  for (let attempt = 0; attempt < 240; attempt++) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  console.error(`${what} did not become ready at ${url}`);
  stop(1);
};

// 1. Data, then the API, healthy before anything renders a page (no start-order race).
run(python, [join("e2e", "fixture_lake.py"), lake, "--rows", process.env.FIXTURE_ROWS ?? "4000"]);
serve(python, ["-m", "disclosure_atlas.cli", "--lake", lake, "serve", "--port", String(API_PORT)]);
await waitFor(`${api}/v1/health`, "fixture API");

// 2. The site: `npm run build` (not `next build`) so prebuild self-hosts DuckDB-WASM in public/duckdb.
run("node", [join("scripts", "copy-duckdb.mjs")]);
if (!process.argv.includes("--no-build")) run("npm", ["run", "build"]);
serve("npx", ["next", "start", "--port", String(WEB_PORT), "--hostname", "127.0.0.1"]);

// 3. Report readiness (Lighthouse CI waits for this line), with the explorer's engine actually served.
await waitFor(`http://127.0.0.1:${WEB_PORT}/duckdb/duckdb-browser-eh.worker.js`, "DuckDB-WASM bundle");
await waitFor(`http://127.0.0.1:${WEB_PORT}/en`, "website");
console.log(`fixture site ready on http://127.0.0.1:${WEB_PORT} (API ${api})`);
