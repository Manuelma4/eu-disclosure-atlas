# `se_fi_pdmr`: Sweden, Finansinspektionen (FI), PDMR transactions register

| | |
|---|---|
| Dataset | `insider_transactions` |
| Authority | Finansinspektionen (FI), Sweden |
| Register | https://www.fi.se/en/our-registers/pdmr-transactions/ |
| Endpoint | `GET https://marknadssok.fi.se/publiceringsklient/en-GB/Search/Search` |
| Parameters | `SearchFunctionType=Insyn`, `Publiceringsdatum.From=YYYY-MM-DD`, `Publiceringsdatum.To=YYYY-MM-DD` (inclusive), `button=export` |
| Format | CSV, UTF-16LE without BOM, `;`-separated, trailing `;`, CRLF |
| Coverage | All PDMR transactions reported since 3 July 2016 are searchable (FI) |
| Terms | FI open data "may be used free of charge" and "may be freely processed and re-used without special permission or agreements"; attribution required: "State the source, FI and date" ([FI open data](https://www.fi.se/en/about-fi/about-fi.se/open-data/)) |

## Columns
`Publication date; Issuer; LEI-code; Notifier; Person discharging managerial responsibilities; Position;
Closely associated; Amendment; Details of amendment; Initial notification; Linked to share option programme;
Nature of transaction; Intrument type; Instrument name; ISIN; Transaction date; Volume; Unit; Price;
Currency; Trading venue; Status`

## Quirks
- **1,000-row cap.** The export silently returns at most 1,000 rows (the most recent ones). The connector
  fetches 7-day windows and splits any window that hits the cap. A single day at the cap is flagged
  `truncated` in the bronze manifest and reported by `ingest`.
- **Status.** `Current` is the latest version. `Revised` (an amendment replaced it) and `Cancelled` rows are
  marked `is_superseded`. Amendments carry `Amendment = Yes` and a Swedish free-text
  `Details of amendment` (e.g. "Volym från 2700,0 till 2750,0.").
- **Typos in the export:** the column `Intrument type`, and the role `Chief Executive Officer
  (CEO)/Managing Directory`.
- **Roles:** non-breaking spaces inside a role, `", "` between roles. The MAR category "Other member of
  the company's administrative, management or supervisory body" contains a comma followed by a non-breaking
  space and is kept whole.
- **Times:** publication timestamps are Stockholm local time and are stored in UTC.
- **Instrument types:** `BTA` (paid subscribed share), `BTU` (paid subscribed unit) and `Interim share`
  are treated as shares; `Subcription warrant` (sic) as a warrant.
- **Identical rows:** genuinely identical rows exist (e.g. split fills); an occurrence index keeps their
  ids distinct and stable.
- **Threshold:** since 4 December 2024 the notification threshold is EUR 20,000 per calendar year (FI).

## Observed (14 days, 6 to 19 October 2026)
324 records, 18 superseded; 0 missing LEIs; 7 share transactions without ISIN; 18 of 306 current records
published more than five calendar days after the transaction.
