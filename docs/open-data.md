# Open data releases

The gold tables are published as versioned [GitHub Releases](https://github.com/Manuelma4/eu-disclosure-atlas/releases)
tagged `data-<version>`. Each release is one DuckLake snapshot, so the same release always returns the same
rows and every figure computed from it can cite the snapshot id.

## Query a release in one line

With [DuckDB](https://duckdb.org/docs/installation/) 1.1 or later (the `httpfs` extension loads itself for
`https://` paths). Replace `<tag>` with a release tag such as `data-2026.10.20-s412`, or use the latest release:

```bash
duckdb -c "SELECT source, count(*) FROM 'https://github.com/Manuelma4/eu-disclosure-atlas/releases/latest/download/insider_transactions.parquet' GROUP BY 1"
```

The whole database in one file, with the reuse terms inside:

```bash
duckdb -c "ATTACH 'https://github.com/Manuelma4/eu-disclosure-atlas/releases/download/<tag>/eu-disclosure-atlas.duckdb' AS atlas (READ_ONLY); SELECT * FROM atlas.sources"
```

From Python, `duckdb.sql("SELECT ... FROM 'https://.../net_short_positions.parquet'")` works the same way, and
pandas or Polars read the Parquet files directly. For repeated queries, download the files first and verify them:

```bash
gh release download <tag> --repo Manuelma4/eu-disclosure-atlas && shasum -a 256 -c SHA256SUMS
```

## What a release contains

| File | Content |
|---|---|
| `insider_transactions.parquet` | Gold managers' transactions (MAR Article 19), Parquet with zstd compression |
| `net_short_positions.parquet` | Gold public net short positions (SSR Article 6) |
| `eu-disclosure-atlas.duckdb` | Both tables in one DuckDB database, plus `sources` (reuse terms per source) and `release` (version, snapshot id) |
| `datapackage.json` | [Frictionless Data Package v2](https://datapackage.org/standard/data-package/): table schemas derived from the Pydantic models (types, enums, descriptions from [data-model.md](data-model.md)), sources and licences, SHA-256 of each file, `atlas:snapshotId` |
| `quality.md`, `quality.json` | Data-quality report: rows per source, freshness per source (stale after 7 days without a publication), ISIN and LEI presence and check-digit validity, duplicate record ids, rows sharing a business key, privacy checks |
| `badge.json` | Freshness badge in the [shields.io endpoint](https://shields.io/badges/endpoint-badge) format (green when every source is fresh) |
| `SHA256SUMS` | SHA-256 of every other file, for `shasum -a 256 -c` or `sha256sum -c` |

Schemas: [data-model.md](data-model.md). Gold rows are the latest version of each record, without superseded
ones, with derived columns (`first_seen_at`, `is_open_market`, `filing_lag_days`, `amount_eur`).

## Privacy

Releases contain the gold layer only. Managers and other natural persons appear as keyed per-issuer pseudonyms
(`p_...`); legal entities keep their names ([ADR 0002](decisions/0002-pseudonymize-natural-persons.md)). Before
writing anything, `publish` runs privacy checks on the snapshot (a `notifier_name` on a natural person, a
natural-person short holder without a pseudonym) and refuses to release if any row fails. Bronze, the raw
regulator files, is never read by `publish` and never published.

## Licensing per source

The Atlas code is under Apache-2.0; the data stays subject to each authority's terms, recorded in the source
notes and copied into every release (`datapackage.json` `sources` and `licenses`, and the `sources` table).
When you reuse a release, cite each source you use with its attribution and the release date or tag.

| Source | Terms | Attribution | Commercial reuse |
|---|---|---|---|
| [`se_fi_pdmr`](sources/se_fi_pdmr.md), [`se_fi_shorts`](sources/se_fi_shorts.md) | [FI open data](https://www.fi.se/en/about-fi/about-fi.se/open-data/): free reuse without special permission | "State the source, FI and date" | Allowed |
| [`fr_amf_shorts`](sources/fr_amf_shorts.md) | [Licence Ouverte / Open Licence v2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/) (Etalab 2.0) | "AMF, data.gouv.fr" and the date | Allowed |
| [`fr_amf_pdmr`](sources/fr_amf_pdmr.md) | [BDIF legal notice](https://bdif.amf-france.org/fr/mentions-legales): reproduction with source and date, data not altered in a misleading way | "©Autorité des marchés financiers – droits réservés" and the date | Excluded: needs the AMF's permission |
| [`de_bafin_shorts`](sources/de_bafin_shorts.md) | No reuse licence stated; publication required by SSR Art. 9(4) | "Bundesanzeiger" | Not stated |
| [`nl_afm_shorts`](sources/nl_afm_shorts.md) | No reuse licence stated ("© AFM, all rights reserved"); publication required by SSR Art. 9(4) | "AFM" | Not stated |
| [`ie_cbi_shorts`](sources/ie_cbi_shorts.md) | No reuse licence stated; publication required by SSR Art. 9(4) | "Central Bank of Ireland" | Not stated |
| [`es_cnmv_shorts`](sources/es_cnmv_shorts.md) | No reuse licence stated in the [legal notice](https://www.cnmv.es/portal/utilidades/NotaLegal.aspx); publication required by SSR Art. 9(4) | "CNMV" | Not stated |
| ECB euro reference rates (for `amount_eur`) | [ECB copyright notice](https://www.ecb.europa.eu/home/disclaimer/html/index.en.html): free use, the ECB cited as the source, changes stated explicitly | "Source: European Central Bank"; `amount_eur` is a conversion made by the Atlas | Allowed |

"Not stated" means the authority publishes the data because the law requires it but grants no explicit reuse
licence: research and journalism are the intended uses here; ask the authority before commercial reuse. The
table above follows the source notes; when a note changes, update `src/disclosure_atlas/publish/sources.py`
too (a test checks that every source listed there has a Terms row in its note). Nothing in a release is
investment advice.

## Making a release (owner only)

Releases are created by the owner from their machine, so the owner is the author of the tag and the release.
There is no scheduled workflow and no bot. `data-*` tags trigger no workflow (images build on `v*` tags only),
so a release costs no CI minutes.

```bash
scripts/release-data.sh --dry-run          # build dist/open-data/<timestamp>, verify checksums, print the gh command
scripts/release-data.sh                    # same, then gh release create data-<version> with every file
```

Options: `--lake DIR`, `--out DIR`, `--as-of SNAPSHOT` (release an earlier snapshot), `--draft`. The script
checks that `gh` is logged in as the owner, runs `disclosure-atlas publish --out`, verifies `SHA256SUMS`, and
uses the quality report as release notes. Each release is marked latest, so the `releases/latest/download/...`
URLs above always serve the newest data. To build a release folder without the script:

```bash
disclosure-atlas publish --out dist/open-data/manual [--as-of 412]
```

`disclosure-atlas publish` without `--out` keeps its other role: publishing new gold rows to Kafka (see
[architecture.md](architecture.md)).
