/**
 * The explorer's engine: DuckDB-WASM (self-hosted bundles in /duckdb) behind the Mosaic coordinator, with the
 * gold Parquet export registered as a remote file. DuckDB reads it with HTTP range requests, loads only the
 * columns the explorer needs into an in-memory table once per snapshot, and Mosaic pre-aggregates brushes.
 */
import type * as DuckDB from "@duckdb/duckdb-wasm";

import type { ExportFile } from "@/lib/api/data";

const API_URL = process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://127.0.0.1:8000";

export type Labels = {
  buy: string;
  sell: string;
  other: string;
  role: (code: string) => string;
};

export type Loaded = { rows: number; seconds: number; file: ExportFile; url: string };

let engine: Promise<{ db: DuckDB.AsyncDuckDB; connection: DuckDB.AsyncDuckDBConnection }> | null = null;
let loaded: { key: string; result: Promise<Loaded> } | null = null;

const quote = (value: string) => `'${value.replaceAll("'", "''")}'`;
const ROLES = ["ceo", "cfo", "chair", "executive", "board_member", "other", "unknown"];

async function startEngine() {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const origin = window.location.origin;
  const bundle = await duckdb.selectBundle({
    mvp: { mainModule: `${origin}/duckdb/duckdb-mvp.wasm`, mainWorker: `${origin}/duckdb/duckdb-browser-mvp.worker.js` },
    eh: { mainModule: `${origin}/duckdb/duckdb-eh.wasm`, mainWorker: `${origin}/duckdb/duckdb-browser-eh.worker.js` },
  });
  const worker = new Worker(bundle.mainWorker!);
  // A worker that cannot load (missing bundle, CSP) only fires "error": fail instead of waiting forever.
  const workerFailed = new Promise<never>((_, reject) =>
    worker.addEventListener("error", () => reject(new Error(`DuckDB-WASM could not start (${bundle.mainWorker})`)), {
      once: true,
    }),
  );
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
  await Promise.race([db.instantiate(bundle.mainModule, bundle.pthreadWorker), workerFailed]);
  await db.open({ query: { castBigIntToDouble: true } });
  const connection = await db.connect();
  // Extensions (Parquet) come from our own origin, never from a third-party host (scripts/copy-duckdb.mjs).
  await connection.query(`SET custom_extension_repository = '${origin}/duckdb/extensions'`);
  return { db, connection };
}

export function getEngine() {
  if (!engine) {
    engine = startEngine();
    engine.catch(() => {
      engine = null; // let "Try again" start from scratch
    });
  }
  return engine;
}

/** Loads the gold insider table (once per data snapshot and language) into the `filings` table. */
export function loadFilings(labels: Labels, onStage: (stage: "engine" | "download") => void): Promise<Loaded> {
  const key = [labels.buy, labels.sell, labels.other, labels.role("ceo")].join("|");
  if (loaded?.key === key) return loaded.result;
  const result = (async () => {
    const started = performance.now();
    onStage("engine");
    const [{ db, connection }, files] = await Promise.all([
      getEngine(),
      fetch(`${API_URL}/v1/exports`).then((r) => {
        if (!r.ok) throw new Error(`GET /v1/exports answered ${r.status}`);
        return r.json() as Promise<ExportFile[]>;
      }),
    ]);
    const file = files.find((f) => f.dataset === "insider_transactions");
    if (!file) throw new Error("No insider_transactions export");
    const url = `${API_URL}${file.path}`;
    onStage("download");
    const { DuckDBDataProtocol } = await import("@duckdb/duckdb-wasm");
    const name = `insider_transactions-${file.snapshot}.parquet`;
    await db.registerFileURL(name, url, DuckDBDataProtocol.HTTP, false);
    const role = `CASE pdmr_role ${ROLES.map((r) => `WHEN ${quote(r)} THEN ${quote(labels.role(r))}`).join(" ")} ELSE ${quote(labels.role("other"))} END`;
    await connection.query(`
      CREATE OR REPLACE TABLE filings AS
      SELECT
        CAST(published_at AS TIMESTAMP) AS published,
        ${role} AS role,
        CASE WHEN is_open_market AND direction > 0 THEN ${quote(labels.buy)}
             WHEN is_open_market AND direction < 0 THEN ${quote(labels.sell)}
             ELSE ${quote(labels.other)} END AS side,
        amount_eur AS amount
      FROM read_parquet(${quote(name)})
      WHERE published_at IS NOT NULL;
      CREATE OR REPLACE VIEW priced AS SELECT * FROM filings WHERE amount > 0;
    `);
    const count = await connection.query("SELECT count(*) AS n FROM filings");
    const rows = Number(count.getChild("n")?.get(0) ?? 0);
    return { rows, seconds: (performance.now() - started) / 1000, file, url };
  })();
  loaded = { key, result };
  result.catch(() => {
    if (loaded?.result === result) loaded = null;
  });
  return result;
}
