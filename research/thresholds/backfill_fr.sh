#!/usr/bin/env bash
# Backfill the AMF (France) PDMR register into a local lake, in chunks around the Listing Act change
# (4 December 2024) first. Resumable: finished chunks are recorded in <lake>/_backfill_fr_amf_pdmr.done.
# At 1 request per second, one year of AMF filings (about 5,000 PDFs) takes roughly 1.5 to 2 hours.
set -euo pipefail
LAKE="${LAKE:-lake}"
DONE="$LAKE/_backfill_fr_amf_pdmr.done"
mkdir -p "$LAKE" && touch "$DONE"
for chunk in 2024-06-01:2025-05-31 2025-06-01:2026-10-20 2023-06-01:2024-05-31 2022-06-01:2023-05-31 \
             2021-02-01:2022-05-31; do
  if grep -qx "$chunk" "$DONE"; then echo "skip $chunk (done)"; continue; fi
  disclosure-atlas --lake "$LAKE" ingest fr_amf_pdmr --start "${chunk%%:*}" --end "${chunk##*:}" \
    | tee -a "$LAKE/_backfill.log"
  echo "$chunk" >> "$DONE"
done
