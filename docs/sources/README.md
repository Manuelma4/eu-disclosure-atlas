# Sources

One row per authority. Statuses: **live**, planned, researching, blocked (with the reason). Each live source
has a note `docs/sources/<source>.md` covering endpoint, format, coverage, terms and quirks.

| Jurisdiction | Authority | Insider trades (MAR 19) | Net short positions (SSR 6) | Notes |
|---|---|---|---|---|
| SE | Finansinspektionen (FI) | **live**: [`se_fi_pdmr`](se_fi_pdmr.md) | **live**: [`se_fi_shorts`](se_fi_shorts.md) | Open data, CSV export, history since 3 July 2016 |
| FR | AMF (BDIF) | planned (WS1) | **live**: [`fr_amf_shorts`](fr_amf_shorts.md) | Declarations published as documents, in French and English since 2026 |
| DE | BaFin | planned (WS2) | **live**: [`de_bafin_shorts`](de_bafin_shorts.md) | BaFin's managers' transactions database shows only the past year; primary storage is the company register |
| NL | AFM | planned (WS2) | **live**: [`nl_afm_shorts`](nl_afm_shorts.md) | Public registers with export |
| IE | Central Bank of Ireland | researching | **live**: [`ie_cbi_shorts`](ie_cbi_shorts.md) | Net short positions file includes a historical table |
| ES | CNMV | researching | **live**: [`es_cnmv_shorts`](es_cnmv_shorts.md) | Short positions: one Excel workbook, current and closed positions since 2010 |
| IT | Consob | researching | blocked: the site sits behind a Radware CAPTCHA for automated clients (October 2026); the project does not bypass CAPTCHAs | Consob offers an Excel file of current and historic positions; needs an agreed access route (e.g. ask Consob) |
| DK, FI, NO, BE, AT, PL, PT, others | | researching (WS3) | researching (WS4) | |

## Reference data (WS5)
| Source | Use |
|---|---|
| GLEIF API | LEI to legal name, country, status |
| ESMA FIRDS | ISIN to instrument and issuer LEI |
| OpenFIGI | ISIN to exchange tickers |
| ECB euro reference rates | Amounts in EUR |
