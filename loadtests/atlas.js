// k6 load tests for the public API (P7, decision 0014). Profiles: smoke (CI), load, stress.
//   k6 run -e BASE_URL=http://localhost:8000 -e PROFILE=smoke loadtests/atlas.js
// Budgets (thresholds fail the run): REST p95 under 200 ms with a warm server (snapshot-pinned responses are
// cached), revalidations (304) p95 under 50 ms, errors under 1%, live-stream connections that open and
// deliver events. Run against a synthetic lake (loadtests/seed_lake.py) with the rate limit disabled.
import http from "k6/http";
import { check, group, sleep } from "k6";
import { Counter, Rate } from "k6/metrics";
import exec from "k6/execution";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const PROFILE = __ENV.PROFILE || "smoke";

const PROFILES = {
  smoke: { rest: [{ duration: "10s", target: 5 }, { duration: "40s", target: 5 }], sse: 3, sseDuration: "50s" },
  load: { rest: [{ duration: "1m", target: 50 }, { duration: "4m", target: 50 }, { duration: "30s", target: 0 }],
          sse: 100, sseDuration: "5m" },
  stress: { rest: [{ duration: "2m", target: 200 }, { duration: "5m", target: 200 }, { duration: "1m", target: 0 }],
            sse: 500, sseDuration: "8m" },
};
const profile = PROFILES[PROFILE];
if (!profile) throw new Error(`Unknown PROFILE ${PROFILE}; use one of ${Object.keys(PROFILES)}`);

const rest = (exec, weight) => ({ executor: "ramping-vus", stages: profile.rest.map((s) => ({
  duration: s.duration, target: Math.max(1, Math.round(s.target * weight)) })), exec, gracefulRampDown: "5s" });

export const options = {
  scenarios: {
    overview: rest("overview", 0.3),
    filings_pagination: rest("filingsPagination", 0.3),
    issuer_pages: rest("issuerPages", 0.4),
    sse_connections: { executor: "constant-vus", vus: profile.sse, duration: profile.sseDuration, exec: "sse" },
  },
  thresholds: {
    "http_req_duration{kind:rest}": ["p(95)<200"],
    "http_req_duration{kind:revalidate}": ["p(95)<50"],
    "http_req_failed{kind:rest}": ["rate<0.01"],
    "http_req_failed{kind:revalidate}": ["rate<0.01"],
    checks: ["rate>0.99"],
    sse_connections_ok: ["rate>0.99"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

const sseOk = new Rate("sse_connections_ok");
const sseEvents = new Counter("sse_events");
const REST = { tags: { kind: "rest" }, headers: { Accept: "application/json" } };

function getJson(path, name) {
  const res = http.get(`${BASE_URL}${path}`, { ...REST, tags: { ...REST.tags, name } });
  check(res, { [`${name} 200`]: (r) => r.status === 200 });
  return res;
}

export function setup() {
  const res = http.get(`${BASE_URL}/v1/issuers?limit=500`);
  const leis = res.status === 200 ? res.json().map((i) => i.lei) : [];
  if (leis.length === 0) throw new Error(`No issuers at ${BASE_URL}; seed the lake first`);
  // A stream position an hour back, so every connection replays recent filings.
  const since = new Date(Date.now() - 3600 * 1000).toISOString();
  return { leis, since };
}

export function overview() {
  group("overview", () => {
    const res = getJson("/v1/stats/overview", "overview");
    getJson("/v1/stats/flow?bucket=week&days=182", "flow");
    getJson("/v1/sources", "sources");
    const etag = res.headers["Etag"];
    if (etag) {
      const again = http.get(`${BASE_URL}/v1/stats/overview`, {
        headers: { "If-None-Match": etag }, tags: { kind: "revalidate", name: "overview 304" },
      });
      check(again, { "overview 304": (r) => r.status === 304 });
    }
  });
  sleep(1);
}

export function filingsPagination() {
  group("filings pagination", () => {
    let cursor = null;
    for (let page = 0; page < 5; page++) {
      const query = `limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`;
      const res = getJson(`/v1/insider-transactions?${query}`, "filings page");
      if (res.status !== 200) break;
      cursor = res.json("next_cursor");
      if (!cursor) break;
    }
    getJson("/v1/insider-transactions?open_market=true&nature=buy&limit=100", "filings filtered");
  });
  sleep(1);
}

export function issuerPages(data) {
  group("issuer pages", () => {
    const lei = data.leis[Math.floor(Math.random() * data.leis.length)];
    getJson(`/v1/issuers/${lei}`, "issuer detail");
    getJson(`/v1/insider-transactions?issuer_lei=${lei}&limit=20`, "issuer filings");
  });
  sleep(1);
}

// k6 has no native EventSource: hold the stream open for a few seconds, then count the events received.
// The client-side timeout (error 1050) is the expected way this request ends.
export function sse(data) {
  const res = http.get(`${BASE_URL}/v1/stream?since=${encodeURIComponent(`${data.since}|`)}`, {
    timeout: "8s", tags: { kind: "sse", name: "stream" }, headers: { Accept: "text/event-stream" },
  });
  const body = res.body || "";
  const events = (body.match(/^event: filing$/gm) || []).length;
  sseEvents.add(events);
  sseOk.add(body.startsWith("retry:") && (res.status === 200 || res.error_code === 1050));
  if (exec.scenario.iterationInTest === 0) console.log(`first stream connection received ${events} events`);
}
