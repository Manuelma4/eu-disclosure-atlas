// Starts the website against a fixture API, for Playwright and Lighthouse:
//   1. writes a synthetic lake (e2e/fixture_lake.py; FIXTURE_ROWS rows, 4,000 by default),
//   2. serves it with the real FastAPI app on :8790 (synthetic prices, CORS for :3190),
//   3. builds the site into .next-e2e (skip with --no-build) and serves it on :3190.
// Python: $ATLAS_PYTHON, else ../.venv/bin/python, else python3 (needs `pip install -e '.[api]'`).
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(web, "..");
const python =
  process.env.ATLAS_PYTHON ?? (existsSync(join(repo, ".venv", "bin", "python")) ? join(repo, ".venv", "bin", "python") : "python3");
const API_PORT = 8790;
const WEB_PORT = 3190;
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
  NEXT_PUBLIC_SITE_URL: `http://127.0.0.1:${WEB_PORT}`,
};

const run = (cmd, args, opts = {}) => {
  const result = spawnSync(cmd, args, { stdio: "inherit", env, cwd: web, ...opts });
  if (result.status !== 0) process.exit(result.status ?? 1);
};

run(python, [join("e2e", "fixture_lake.py"), lake, "--rows", process.env.FIXTURE_ROWS ?? "4000"]);
if (!process.argv.includes("--no-build")) run("npx", ["next", "build"]);

const children = [
  spawn(python, ["-m", "disclosure_atlas.cli", "--lake", lake, "serve", "--port", String(API_PORT)], {
    stdio: "inherit",
    env,
    cwd: web,
  }),
  spawn("npx", ["next", "start", "--port", String(WEB_PORT), "--hostname", "127.0.0.1"], { stdio: "inherit", env, cwd: web }),
];
const stop = () => {
  children.forEach((child) => child.kill("SIGTERM"));
  process.exit(0);
};
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
children.forEach((child) => child.on("exit", (code) => code && process.exit(code)));

// Report readiness once both answer (Lighthouse CI waits for this line).
for (let attempt = 0; attempt < 120; attempt++) {
  try {
    const [a, w] = await Promise.all([fetch(`${api}/v1/health`), fetch(`http://127.0.0.1:${WEB_PORT}/en`)]);
    if (a.ok && w.ok) {
      console.log(`fixture site ready on http://127.0.0.1:${WEB_PORT} (API ${api})`);
      break;
    }
  } catch {}
  await new Promise((resolve) => setTimeout(resolve, 500));
}
