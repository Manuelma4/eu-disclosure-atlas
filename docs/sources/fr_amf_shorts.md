# `fr_amf_shorts`: France, AMF, public net short positions

| | |
|---|---|
| Dataset | `net_short_positions` |
| Authority | Autorité des marchés financiers (AMF), France |
| Publication | [data.gouv.fr dataset](https://www.data.gouv.fr/datasets/historique-des-positions-courtes-nettes-sur-actions-rendues-publiques-depuis-le-1er-novembre-2012), published by the AMF; background on the [AMF short selling page](https://www.amf-france.org/fr/actualites-publications/dossiers-thematiques/ventes-decouvert) |
| Endpoint | `GET https://www.data.gouv.fr/api/1/datasets/62738e33f1be79935d3e5553/` (dataset metadata), then the CSV resource it lists |
| Format | CSV, `;`-separated, quoted, UTF-8 (ASCII in practice: accents are stripped from the headers) |
| Coverage | Every position published since 1 November 2012 (current and closed) in one file |
| Update | Daily in practice: the resource was refreshed at 10:30 UTC on the day of the last ingest, and its file name carries the extraction timestamp |
| Terms | Licence Ouverte / Open Licence v2.0 (`lov2` on data.gouv.fr): free reuse with attribution ("AMF, data.gouv.fr" and the date) |

Public disclosure follows SSR Article 6 ([Regulation (EU) No 236/2012](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32012R0236)): a net short position is published when it reaches or falls below 0.5% of the issued share capital "and each 0,1 % above that" (Art. 6(2)), no later than 15:30 on the following trading day (Art. 9(2)), on a central website operated or supervised by the competent authority (Art. 9(4)).

## Columns
`Detenteur de la position courte nette; Legal Entity Identifier detenteur; Emetteur / issuer; Ratio; code ISIN;
Date de debut position; Date de debut de publication position; Date de fin de publication position`

Mapping: holder name and **holder LEI** (the only source here that gives it), issuer, ISIN, `position_pct` from
`Ratio`, `position_date` from `Date de debut position`, `published_at` from `Date de debut de publication
position` (a day, stored as midnight Paris time). The publication end date (when a later position replaced it) is
not stored: it is implied by the next position of the same holder and issuer.

## Quirks
- **Rotating URL.** The CSV file name changes with every refresh (`export_od_vad_<from>_<to>.csv`), so the
  connector reads the current URL from the dataset API instead of hard-coding it.
- **Below 0.5%.** The final disclosure of a position that fell below the threshold carries its actual size
  (e.g. 0.35); `below_threshold` is set. The AMF warns that this value need not reflect the position on the
  following days (see the dataset description).
- **Invalid ISINs:** a handful of rows have an ISIN that fails the ISO 6166 check digit; `isin` is null and the
  raw value still keys `record_id`.
- **Outliers:** a few very large ratios (e.g. 89% after a capital restructuring) are kept as published.
- No issuer LEI.

## Observed (full history, ingested 20 October 2026)
41,171 positions from 1 November 2012 to 16 October 2026; 5,147 below-threshold disclosures; 40,722 with a holder
LEI; 7 without a valid ISIN; no holder classified as a natural person.
