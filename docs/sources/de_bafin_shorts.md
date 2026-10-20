# `de_bafin_shorts`: Germany, BaFin, net short positions in the Federal Gazette

| | |
|---|---|
| Dataset | `net_short_positions` |
| Authority | Bundesanstalt für Finanzdienstleistungsaufsicht (BaFin). Germany publishes net short positions in the Federal Gazette (Bundesanzeiger), the central website for this purpose |
| Publication | [Bundesanzeiger, net short positions](https://www.bundesanzeiger.de/pub/en/nlp) |
| Endpoint | `GET /pub/en/nlp` (search page), `POST` its search form with `isHistorical=true`, then `GET` the result page's CSV link (`...top~csv~form~panel-form-csv~resource~link`) |
| Format | CSV, `,`-separated, quoted, UTF-8 with BOM; decimal comma (`"0,50"`) |
| Coverage | With "historical" ticked: every published position since 2012 (current ones included), about 62,000 rows |
| Update | Daily, as notifications arrive |
| Terms | No reuse licence is stated for the search results; publication is required by law (SSR Art. 9(4)). robots.txt disallows `/nlp` (with the comment that NLP pages should not be indexed); the connector uses `/pub/en/nlp`, which the rule does not match, downloads one CSV per run and does not index pages. Attribute "Bundesanzeiger" |

Public disclosure follows SSR Article 6 ([Regulation (EU) No 236/2012](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32012R0236)): a net short position is published when it reaches or falls below 0.5% of the issued share capital "and each 0,1 % above that" (Art. 6(2)), no later than 15:30 on the following trading day (Art. 9(2)), on a central website operated or supervised by the competent authority (Art. 9(4)).

## Columns
`Positionsinhaber; Emittent; ISIN; Position; Datum` (holder, issuer, ISIN, position %, position date).

## Quirks
- **Stateful page.** The site is an Apache Wicket application: form and link URLs carry a page version
  (`?0-1.-...`, `?2--...`) and the session lives in a cookie. The connector follows the links the page returns;
  `PoliteClient.post` was added for the form and the client keeps cookies across the three requests.
- **Without "historical",** the CSV holds only the current positions (about 500 rows).
- **Below 0.5%:** the final disclosure carries its size (e.g. 0.49); `below_threshold` is set.
- **Typos at the source:** a few position dates in 2003-2011 and a few implausible sizes (73.00, 70.75, probably
  0.73 and 0.7075) are kept as published; the 2003-2011 dates fall outside `--start 2012-11-01`.
- No LEIs.

## Observed (full history, ingested 20 October 2026)
62,152 positions from 1 November 2012 to 19 October 2026; 8,375 below-threshold disclosures; 8 rows from 3 holders
classified as natural persons (pseudonymized).
