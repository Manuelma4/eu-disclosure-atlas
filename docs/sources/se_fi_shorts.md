# `se_fi_shorts`: Sweden, Finansinspektionen, net short positions register

| | |
|---|---|
| Dataset | `net_short_positions` |
| Authority | Finansinspektionen (FI), Sweden |
| Register | [Net short positions](https://www.fi.se/en/our-registers/net-short-positions/) ("Blankningsregistret") |
| Endpoint | `GET https://www.fi.se/BlankningsRegister/GetAktuellFile` (current) and `.../GetHistFile` (historic) |
| Format | OpenDocument spreadsheet (`.ods`); read with the standard library (no extra dependency) |
| Coverage | Historic positions from November 2012 (one row dated 2010) |
| Update | Data is "published automatically upon receipt of a notification" and "not reviewed prior to publication" (FI) |
| Terms | FI open data "may be freely processed and re-used without special permission or agreements"; attribution required: "State the source, FI and date" ([FI open data](https://www.fi.se/en/about-fi/about-fi.se/open-data/)) |

Public disclosure follows SSR Article 6 ([Regulation (EU) No 236/2012](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32012R0236)): a net short position is published when it reaches or falls below 0.5% of the issued share capital "and each 0,1 % above that" (Art. 6(2)), no later than 15:30 on the following trading day (Art. 9(2)), on a central website operated or supervised by the competent authority (Art. 9(4)).

## Columns
Five title rows, then `Innehavare av positionen (Position holder); Namn på emittent (Name of the issuer); ISIN;
Position i procent (Position in per cent); Datum för positionen (Position date); Kommentar (Comment)`. Swedish and
English labels are in the same cell, separated by a line break.

## Quirks
- **Below 0.5% is the text `<0,5`**, not a number; `position_pct` is null and `below_threshold` is set.
- **Mixed cell types:** sizes are floats or strings with a decimal comma; dates are strings (`2026-10-19`) in
  recent rows and ODS date cells (`2016-06-28T00:00:00`) in older ones.
- **Trailing cells:** each row ends with an empty cell repeated 16,378 times; the reader caps repetitions.
- **Non-breaking spaces** at the end of some issuer names.
- **Natural persons:** Sweden has the most private individuals among the holders; their names are pseudonymized
  (see `docs/data-model.md`).
- The current and historic files do not overlap.
- No LEIs. An "Aggregate positions" file is announced but not yet published.

## Observed (full history, ingested 20 October 2026)
38,953 positions from 1 November 2012 to 19 October 2026; 5,352 below-threshold disclosures; 128 rows from 21 holders
classified as natural persons (pseudonymized).
