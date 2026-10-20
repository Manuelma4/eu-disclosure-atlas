#!/usr/bin/env bash
# Backfill the Sweden FI PDMR register into a local lake, one calendar year at a time.
# Resumable: a finished year is recorded in <lake>/_backfill_se_fi_pdmr.done and skipped on re-runs.
# The current year is always re-fetched (new filings and revisions keep arriving).
set -euo pipefail
LAKE="${LAKE:-lake}"
FIRST="${FIRST:-2016-07-03}"   # FI register is searchable from 3 July 2016
TODAY="$(date +%F)"
DONE="$LAKE/_backfill_se_fi_pdmr.done"
mkdir -p "$LAKE" && touch "$DONE"
for year in $(seq "${FIRST:0:4}" "${TODAY:0:4}"); do
  start="$year-01-01"; [[ "$year" == "${FIRST:0:4}" ]] && start="$FIRST"
  end="$year-12-31"; [[ "$year" == "${TODAY:0:4}" ]] && end="$TODAY"
  if grep -qx "$year" "$DONE" && [[ "$year" != "${TODAY:0:4}" ]]; then
    echo "skip $year (done)"; continue
  fi
  disclosure-atlas --lake "$LAKE" ingest se_fi_pdmr --start "$start" --end "$end" | tee -a "$LAKE/_backfill.log"
  if [[ "$year" != "${TODAY:0:4}" ]]; then echo "$year" >> "$DONE"; fi
done
