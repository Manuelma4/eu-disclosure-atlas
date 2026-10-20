// Self-host DuckDB-WASM for the explorer: the bundles from node_modules and the Parquet extension, so the
// browser never calls a third-party host (CSP-friendly, works offline once cached). Runs before `dev` and
// `build`; files land in public/duckdb/ (git-ignored). The extension is downloaded once from DuckDB's
// official repository and cached in node_modules/.cache.
import { copyFileSync, existsSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// DuckDB core version inside @duckdb/duckdb-wasm 1.33.1-dev57.0 (SELECT version() in the browser).
// Keep in sync with src/components/explorer/engine.ts when upgrading the package.
const DUCKDB_VERSION = "v1.5.4";
const PLATFORMS = ["wasm_eh", "wasm_mvp"];
const EXTENSIONS = ["parquet"];

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "node_modules", "@duckdb", "duckdb-wasm", "dist");
const target = join(root, "public", "duckdb");
const cache = join(root, "node_modules", ".cache", "duckdb-extensions");
const FILES = ["duckdb-eh.wasm", "duckdb-browser-eh.worker.js", "duckdb-mvp.wasm", "duckdb-browser-mvp.worker.js"];

const copy = (from, to) => {
  mkdirSync(dirname(to), { recursive: true });
  let fresh = false;
  try {
    fresh = statSync(to).size === statSync(from).size;
  } catch {}
  if (!fresh) copyFileSync(from, to);
};

for (const file of FILES) copy(join(source, file), join(target, file));

for (const platform of PLATFORMS) {
  for (const name of EXTENSIONS) {
    const path = join(DUCKDB_VERSION, platform, `${name}.duckdb_extension.wasm`);
    const cached = join(cache, path);
    if (!existsSync(cached)) {
      const url = `https://extensions.duckdb.org/${path.split("\\").join("/")}`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Could not download ${url}: ${response.status}`);
      mkdirSync(dirname(cached), { recursive: true });
      writeFileSync(cached, Buffer.from(await response.arrayBuffer()));
    }
    copy(cached, join(target, "extensions", path));
  }
}
console.log(`DuckDB-WASM ${DUCKDB_VERSION}: bundles and ${EXTENSIONS.join(", ")} extension ready in public/duckdb`);
