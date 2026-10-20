# Source stale

**Alert:** `AtlasSourceStale` (ticket), labelled with `source`.
**Signal:** no new filing from the source has been ingested for more than 26 hours on a weekday. Owner: data.

## What it means
Either the regulator published nothing (possible for small markets or around public holidays), or the
connector silently stopped. The freshness SLO cannot see this case, because it counts filings that arrive.

## Confirm
1. Open the regulator's listing (URL in `docs/sources/<source>.md`). New filings there but not in the Atlas
   means the connector is broken: follow [filing-freshness.md](filing-freshness.md).
2. Public holiday in that country: acknowledge and close.
3. Check that ingestion runs at all (Dagster run history, or the `watch` process).

## Fix
If a source is legitimately quiet, raise the threshold for it in
`deploy/observability/prometheus/rules/atlas-alerts.yaml` with a comment saying why.
