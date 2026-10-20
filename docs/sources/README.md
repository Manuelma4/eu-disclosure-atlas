# Sources

One row per authority. Statuses: **live**, planned, researching, blocked (with the reason). Each live source
has a note `docs/sources/<source>.md` covering endpoint, format, coverage, terms and quirks.

| Jurisdiction | Authority | Insider trades (MAR 19) | Net short positions (SSR 6) | Notes |
|---|---|---|---|---|
| SE | Finansinspektionen (FI) | **live**: [`se_fi_pdmr`](se_fi_pdmr.md) | planned | Open data, CSV export, history since 3 July 2016 |
| FR | AMF (BDIF) | planned (WS1) | planned (WS4) | Declarations published as documents, in French and English since 2026 |
| DE | BaFin | planned (WS2) | planned (WS4) | BaFin's managers' transactions database shows only the past year; primary storage is the company register |
| NL | AFM | planned (WS2) | planned (WS4) | Public registers with export |
| IE | Central Bank of Ireland | researching | planned (WS4) | Net short positions file includes a historical table |
| ES | CNMV | researching | planned (WS4) | |
| IT | Consob | researching | planned (WS4) | |
| DK, FI, NO, BE, AT, PL, PT, others | | researching (WS3) | researching (WS4) | |

## Reference data (WS5)
| Source | Use |
|---|---|
| GLEIF API | LEI to legal name, country, status |
| ESMA FIRDS | ISIN to instrument and issuer LEI |
| OpenFIGI | ISIN to exchange tickers |
| ECB euro reference rates | Amounts in EUR |
