# `ie_cbi_shorts`: Ireland, Central Bank of Ireland, public net short positions

| | |
|---|---|
| Dataset | `net_short_positions` |
| Authority | Central Bank of Ireland (CBI) |
| Publication | [Public Net Short Positions](https://www.centralbank.ie/regulation/industry-market-sectors/securities-markets/short-selling-regulation/public-net-short-positions) |
| Endpoint | `GET https://www.centralbank.ie/docs/default-source/regulation/industry-market-sectors/securities-markets/short-selling-regulation/public-net-short-positions/table-of-significant-net-short-positions-in-shares.xlsx` (the page adds a cache-busting `?sfvrsn=`) |
| Format | Excel (`.xlsx`); needs the optional extra `pip install -e '.[shorts]'` (`openpyxl`) |
| Coverage | Sheets `Current` and `Historical`; the historical table currently starts on 23 January 2020 |
| Update | When notifications arrive (the file shows "Last Updated"; 2 June 2026 at the last ingest) |
| Terms | No reuse licence is stated; "Position holders are responsible for the accuracy and completeness of data" (CBI). Publication is required by law (SSR Art. 9(4)); attribute "Central Bank of Ireland" |

Public disclosure follows SSR Article 6 ([Regulation (EU) No 236/2012](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32012R0236)): a net short position is published when it reaches or falls below 0.5% of the issued share capital "and each 0,1 % above that" (Art. 6(2)), no later than 15:30 on the following trading day (Art. 9(2)), on a central website operated or supervised by the competent authority (Art. 9(4)).

## Columns
Column A holds the title and the "Last Updated" date; data is in columns B-G: `Position Holder:; Name of the Issuer:;
ISIN:; Net short position %:; Position Date:; Comments:`.

## Quirks
- **Percentages are fractions** (0.0105 is 1.05%).
- **Below 0.5% is published as 0** in the Historical table; `position_pct` is null and `below_threshold` is set.
  A position that falls below the threshold stays in the Current table "for one business day" and in the
  Historical table "indefinitely" (CBI).
- **Shallow history:** despite "indefinitely", the file holds no position before 2020; earlier publications are
  not online.
- **Issuer names vary in case** ("RYANAIR HOLDINGS PLC", "Ryanair Holdings PLC"); `record_id` uses the ISIN.
- No LEIs.

## Observed (full history, ingested 20 October 2026)
551 positions from 23 January 2020 to 29 May 2026; 105 below-threshold disclosures; no holder classified as a natural
person.
