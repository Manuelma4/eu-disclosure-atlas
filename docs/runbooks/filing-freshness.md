# Filing freshness per source

**Alert:** `AtlasFilingFreshnessBudgetBurn` (page, ticket), labelled with `source`.
**SLO:** Over 30 days, 95% of eligible filings should be visible within
15 minutes of publication for each source. Owner: data.
[Filings counted by the SLO](../../deploy/observability/slos/ingestion.yaml)

## What it measures

The API's source metrics use `first_seen_at - published_at` on insider
transactions with both timestamps. Negative differences count as zero.
A delay of up to 15 minutes counts as on time. A delay greater than 15 minutes
and up to 24 hours counts as late. Longer delays enter the separate backfill
series. The burn-rate query divides late arrivals by on-time plus late arrivals.

Every eligible filing contributes, including records arriving outside a
source's usual hours. For a source with no new records, use the
[source-stale runbook](source-stale.md).

## Confirm
1. Grafana *Ingestion: freshness per source*: on-time ratio and *minutes since last ingestion* for the source.
2. Ingestion logs: `{service_name=~"ingestion|watch"} |= "<source>"`. Look for HTTP 4xx/5xx, parse errors,
   or timeouts.
3. Compare the listing in the [source notes](../sources/README.md) with the
   filing's recorded `published_at` and `first_seen_at`. If the timestamps
   differ from the listing, check how the connector derives them before
   attributing the gap to ingestion.

## Mitigate
- Fetch errors: check robots.txt and terms before anything else; never raise the request rate above the
  documented limit. If the endpoint moved, patch the connector and backfill with
  the ingest command with explicit `--start` and `--end` dates for the
  affected source and the intended lake selected via `--lake`.
- Parser errors on a new format: the deterministic parser fails closed; fix it with a fixture from the new
  format (synthetic names only) and redeploy.
- Watch loop stuck: restart it; Dagster (P2) resumes from the last partition.

## Fix
Add a regression fixture under `tests/sources/`, update the quirks section of the source note.
