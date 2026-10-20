# Reference data and enrichment (WS5)

Filings name issuers and instruments the way the filer typed them. Reference data adds official names,
classifications and exchange tickers, so issuers can be searched by ticker and joined with other datasets.
Every reference table is a Parquet file in the lake's reference area (`lake/reference/`), refreshed by a CLI
command and read by the gold views at query time.

| Command | Source | File | Key | Adds |
| --- | --- | --- | --- | --- |
| `disclosure-atlas reference fx` | ECB euro reference rates | `ecb_fx.parquet` | date, currency | `amount_eur` (P1) |
| `disclosure-atlas reference lei` | GLEIF API | `gleif_lei.parquet` | LEI | legal name, legal country, entity and registration status |
| `disclosure-atlas reference firds` | ESMA FIRDS full files | `firds_instruments.parquet` | ISIN | full name, CFI, currency, issuer LEI, venue |
| `disclosure-atlas reference figi` | OpenFIGI mapping API | `openfigi_listings.parquet` | ISIN | exchange listings and one primary ticker |

By default each command looks up the identifiers already in the gold views: issuer LEIs from both datasets, and
ISINs from both datasets. Options:

- `--ids A,B,...` looks up the given LEIs or ISINs instead.
- `--refresh` (lei, figi) re-fetches identifiers that are already cached. Without it, only new ones are fetched.
- `--all` (firds) keeps every equity instrument in the files, not only the lake's ISINs.

A cache refresh writes a temporary file and renames it, so readers never see a partial file. The gold views
read the files at query time, but the API keeps an in-memory copy of gold per DuckLake snapshot, so a refreshed
reference file shows up in the API with the next ingest commit (or a restart).

## Sources

### GLEIF: LEI records

- Endpoint: `GET https://api.gleif.org/api/v1/lei-records?filter[lei]=<LEI>,<LEI>,...&page[size]=100`
  (JSON:API). Up to 100 LEIs per request, one request per second.
- Fields kept: `entity.legalName.name`, `entity.legalAddress.country`, `entity.headquartersAddress.country`,
  `entity.jurisdiction`, `entity.status` (ACTIVE, INACTIVE) and `registration.status` (ISSUED, LAPSED,
  RETIRED...).
- Terms: the data is provided under CC0 1.0
  ([GLEIF LEI data terms of use](https://www.gleif.org/en/meta/lei-data-terms-of-use)).
- Quirks: filers sometimes type the wrong LEI. The gold `issuers` view keeps both the reported `name` and the
  GLEIF `legal_name`, so a mismatch (for example a filing that carries the LEI of an LEI issuing organisation)
  is visible rather than silently "corrected".

### ESMA FIRDS: instrument reference data

- Index: `https://registers.esma.europa.eu/solr/esma_registers_firds_files/select` (Solr, `wt=json`),
  filtered on `publication_date`. Each document carries `file_type`, `file_name` and `download_link`.
- Files: ESMA publishes a full snapshot (`FULINS`) every week, split by the first letter of the CFI code and
  into parts (`FULINS_E_20261017_01of02.zip`). The command takes the latest complete set of equity files
  (`FULINS_E_*`) from the last 14 days. Each zip holds one ISO 20022 `auth.017.001.02` XML file; it is parsed as
  a stream (`RefData` elements).
- Fields kept: `FinInstrmGnlAttrbts/Id` (ISIN), `FullNm`, `ShrtNm`, `ClssfctnTp` (CFI), `NtnlCcy`, `Issr`
  (issuer LEI), `TechAttrbts/RlvntTradgVn` (MIC of the most relevant venue) and `RlvntCmptntAuthrty`.
- Quirks: an instrument admitted on several venues appears once per venue; the first record per ISIN is kept
  (name, CFI and issuer are the same). Equity files also contain certificates and other instruments with a
  CFI beginning with `E`; only the lake's ISINs are kept unless `--all` is passed. Debt and derivative files
  are not read.
- Terms: reproduction is authorised provided the source is acknowledged
  ([ESMA legal notice](https://www.esma.europa.eu/legal-notice)). Attribute "Source: ESMA FIRDS".

### OpenFIGI: exchange tickers

- Endpoint: `POST https://api.openfigi.com/v3/mapping` with a JSON array of jobs
  `{"idType": "ID_ISIN", "idValue": "<ISIN>"}`. An optional key in `OPENFIGI_API_KEY` is sent as
  `X-OPENFIGI-APIKEY`.
- Rate limits, from the [API documentation](https://www.openfigi.com/api/documentation): 25 requests per minute
  and 10 jobs per request without a key, 250 requests per minute and 100 jobs per request with one. The client
  sends 10 jobs every 2.5 seconds without a key, or 100 jobs every 0.25 seconds with one. HTTP 429 is retried
  after `Retry-After`.
- Fields kept per listing: `figi`, `compositeFIGI`, `shareClassFIGI`, `ticker`, `exchCode` (Bloomberg exchange
  code), `name`, `securityType` and `marketSector`. An ISIN that OpenFIGI does not know ("No identifier found.")
  is cached as one row without a FIGI, so it is not asked again. Jobs that return an error are not cached.
- Primary listing, in order: the listing on the home market's Bloomberg composite code, read from the ISIN
  country prefix (`FP` France, `SS` Sweden, `GR` Germany, `NA` Netherlands...); otherwise a local venue
  listing (FIGI different from its composite FIGI) that is not on another country's home code, a German venue
  or `XS` (Nordic growth-market shares such as Spotlight listings have no `SS` listing); otherwise the first
  composite equity listing; otherwise the first listing.
- Terms: the FIGI symbology is free of charge, with no licensing or re-use restrictions for access, use or
  redistribution ([OpenFIGI FAQ](https://www.openfigi.com/about/faq)).

## Gold views

The existing views `insider_transactions` and `net_short_positions` are unchanged. Three views are added; if a
reference file is missing, it reads as an empty table, so the views always exist.

- `instruments`: one row per ISIN known to FIRDS or OpenFIGI: `isin`, `full_name`, `short_name`, `cfi`,
  `currency`, `issuer_lei`, `trading_venue`, `figi`, `primary_ticker`, `primary_exchange` and `tickers` (every
  listing as `"TICKER EXCH"`).
- `issuers`: one row per issuer LEI seen in either dataset: `lei`, the reported `name` and `country` (latest
  filing), GLEIF `legal_name`, `legal_country`, `headquarters_country`, `entity_status`,
  `registration_status`, and `primary_isin`, `primary_ticker`, `primary_exchange`, `tickers` (the primary
  listing of each of the issuer's ISINs). The primary ISIN is a share (CFI `ES`, fully classified before
  `ESXXXX` such as interim units) used in the most filings, among ISINs with a listing. An issuer's ISINs are
  those in its filings plus those FIRDS attributes to its LEI.
- `insider_transactions_enriched`: every column of `insider_transactions` (including `amount_eur`), plus
  `issuer_legal_name`, `issuer_legal_country`, `instrument_full_name`, `instrument_cfi`, `primary_ticker` and
  `primary_exchange`. The ticker is the instrument's own primary listing, or the issuer's when the instrument
  has none (options, warrants and other unlisted instruments).

## API

`GET /v1/issuers` and `GET /v1/issuers/{lei}` add `legal_name`, `primary_ticker`, `primary_exchange` and
`tickers` (null and an empty list without reference data). The website shows the primary ticker next to the LEI
on the company page.

## Not covered yet

- The MCP server and Ask the Atlas still allowlist only the two base views.
- `disclosure-atlas export` writes the base views only.
- No orchestration schedule refreshes the reference files; run the commands after ingests (weekly suits FIRDS).
