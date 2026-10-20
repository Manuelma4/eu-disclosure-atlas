#!/usr/bin/env bash
# Publish an open-data release of the gold tables as a GitHub Release (docs/open-data.md).
#
# Run by the owner, locally, with their own `gh` login: the owner is the author of the tag and the release.
# There is no scheduled workflow and no bot release. Tags are `data-<version>`, which no workflow listens to
# (image builds run on `v*` tags), so a data release costs no CI minutes.
#
# Usage: scripts/release-data.sh [--lake DIR] [--out DIR] [--as-of SNAPSHOT] [--draft] [--dry-run]
#   --lake     lake root (default: ./lake, or $ATLAS_CATALOG / $ATLAS_DATA_PATH as for every command)
#   --out      staging folder (default: dist/open-data/<timestamp>); must not exist yet
#   --as-of    DuckLake snapshot to release (default: the current one)
#   --draft    create the GitHub Release as a draft
#   --dry-run  build and verify the release folder, print the gh command, upload nothing
set -euo pipefail

OWNER="${ATLAS_RELEASE_OWNER:-Manuelma4}"
REPO="${ATLAS_RELEASE_REPO:-Manuelma4/eu-disclosure-atlas}"
LAKE="lake"
OUT=""
AS_OF=()
DRAFT=()
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lake) LAKE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --as-of) AS_OF=(--as-of "$2"); shift 2 ;;
    --draft) DRAFT=(--draft); shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

OUT="${OUT:-dist/open-data/$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ -e "$OUT" ]]; then
  echo "$OUT already exists; pass a new --out folder" >&2
  exit 2
fi

if [[ $DRY_RUN -eq 0 ]]; then
  login="$(gh api user --jq .login)"
  if [[ "$login" != "$OWNER" ]]; then
    echo "gh is logged in as '$login'; releases must be created by the owner ($OWNER)" >&2
    exit 1
  fi
fi

disclosure-atlas --lake "$LAKE" publish --out "$OUT" ${AS_OF[@]+"${AS_OF[@]}"}

(cd "$OUT" && shasum -a 256 -c --quiet SHA256SUMS)

version="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$OUT/datapackage.json")"
snapshot="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["atlas:snapshotId"])' "$OUT/datapackage.json")"
tag="data-$version"

notes="$OUT/RELEASE_NOTES.md"
{
  echo "Open-data release of the EU Disclosure Atlas gold tables, DuckLake snapshot $snapshot."
  echo
  echo "Query it in one line (DuckDB 1.1 or later):"
  echo
  echo '```bash'
  echo "duckdb -c \"SELECT source, count(*) FROM 'https://github.com/$REPO/releases/download/$tag/insider_transactions.parquet' GROUP BY 1\""
  echo '```'
  echo
  echo "Natural persons appear only as pseudonyms. Reuse is subject to each source's terms (\`datapackage.json\`,"
  echo "\`sources\` table, [docs/open-data.md](https://github.com/$REPO/blob/main/docs/open-data.md)): FI open data"
  echo "and the Licence Ouverte allow reuse with attribution; AMF managers' transactions exclude commercial use;"
  echo "other authorities state no reuse licence. Verify downloads with \`shasum -a 256 -c SHA256SUMS\`."
  echo "Not investment advice."
  echo
  sed 's/^#/##/' "$OUT/quality.md"
} > "$notes"

assets=(
  "$OUT/insider_transactions.parquet"
  "$OUT/net_short_positions.parquet"
  "$OUT/eu-disclosure-atlas.duckdb"
  "$OUT/datapackage.json"
  "$OUT/quality.json"
  "$OUT/quality.md"
  "$OUT/badge.json"
  "$OUT/SHA256SUMS"
)
cmd=(gh release create "$tag" --repo "$REPO" --title "Open data $version" --notes-file "$notes" --latest
     ${DRAFT[@]+"${DRAFT[@]}"} "${assets[@]}")

if [[ $DRY_RUN -eq 1 ]]; then
  printf 'Dry run, release folder ready in %s. Would run:\n ' "$OUT"
  printf ' %q' "${cmd[@]}"
  echo
  exit 0
fi
"${cmd[@]}"
