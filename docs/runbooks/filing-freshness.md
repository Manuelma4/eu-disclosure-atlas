# Filing freshness per source

**Alert:** `AtlasFilingFreshnessBudgetBurn` (page, ticket), labelled with `source`.
**SLO:** 95% of filings visible within 15 minutes of publication, per source (counted per filing, so only
the regulator's business hours count). Backfills (visible more than a day late) are excluded. Owner: data.

## What it means
New filings from one regulator take longer than 15 minutes to reach gold: the watch loop or Dagster sensor
is slow or failing, the regulator changed its site, or our polite crawl budget is too small for the volume.

## Confirm
1. Grafana *Ingestion: freshness per source*: on-time ratio and *minutes since last ingestion* for the source.
2. Ingestion logs: `{service_name=~"ingestion|watch"} |= "<source>"`. Look for HTTP 4xx/5xx, parse errors,
   or timeouts.
3. Compare with the regulator's own listing (URL in `docs/sources/<source>.md`). If the regulator itself
   published late, `published_at` is the regulator's timestamp and the lag is real but not ours: note it.

## Mitigate
- Fetch errors: check robots.txt and terms before anything else; never raise the request rate above the
  documented limit. If the endpoint moved, patch the connector and backfill with
  `disclosure-atlas ingest <source> --start <date>`.
- Parser errors on a new format: the deterministic parser fails closed; fix it with a fixture from the new
  format (synthetic names only) and redeploy.
- Watch loop stuck: restart it; Dagster (P2) resumes from the last partition.

## Fix
Add a regression fixture under `tests/sources/`, update the quirks section of the source note.
