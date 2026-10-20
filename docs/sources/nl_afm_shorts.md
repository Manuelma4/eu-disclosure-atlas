# `nl_afm_shorts`: Netherlands, AFM, net short positions register

| | |
|---|---|
| Dataset | `net_short_positions` |
| Authority | Autoriteit Financiële Markten (AFM), Netherlands |
| Registers | [Current](https://www.afm.nl/en/sector/registers/meldingenregisters/netto-shortposities-actueel) and [archive](https://www.afm.nl/en/sector/registers/meldingenregisters/netto-shortposities-historie) |
| Endpoint | `GET https://www.afm.nl/export.aspx?type=<register>&format=csv` with `type=8a46a4ef-f196-4467-a7ab-1ae1cb58f0e7` (current) and `3ca31b3d-23d9-4fa2-b846-29c7e3f0e5ff` (archive); `format=xml` also exists |
| Format | CSV, `;`-separated, quoted, Windows-1252, CRLF; two footer lines (`"Disclaimer CSV"`, `"Datum laatste update <date>"`) |
| Coverage | Archive from November 2012; current register holds the latest position per holder and issuer |
| Update | Daily ("Date last update" on the register page) |
| Terms | No reuse licence is stated ("© AFM, all rights reserved" in the site footer; see the AFM disclaimer). Publication is required by law (SSR Art. 9(4)); attribute "AFM" |

Public disclosure follows SSR Article 6 ([Regulation (EU) No 236/2012](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32012R0236)): a net short position is published when it reaches or falls below 0.5% of the issued share capital "and each 0,1 % above that" (Art. 6(2)), no later than 15:30 on the following trading day (Art. 9(2)), on a central website operated or supervised by the competent authority (Art. 9(4)).

## Columns
`Positie houder; Naam van de emittent; ISIN; Netto Shortpositie; Positiedatum`.

## Quirks
- **Current versus archive.** A position that falls below 0.5% stays in the current register for "one further
  business day" and then moves to the archive (AFM). Both files are fetched; `record_id` (holder, ISIN, date,
  size) makes a position present in both count once in gold.
- **Encoding:** Windows-1252, not UTF-8 (e.g. "Caisse de dépôt et placement du Québec").
- **Dates** carry a time of `00:00:00`.
- **Below 0.5%:** the final disclosure carries its size (e.g. 0.48).
- **Scope:** shares whose principal trading venue is outside the EU are exempt (ESMA list), as everywhere.
- No LEIs.

## Observed (full history, ingested 20 October 2026)
22,446 positions from 1 November 2012 to 16 October 2026; 2,701 below-threshold disclosures; no holder classified as
a natural person.
