# `es_cnmv_shorts`: Spain, CNMV, public net short positions

| | |
|---|---|
| Dataset | `net_short_positions` |
| Authority | Comisión Nacional del Mercado de Valores (CNMV), Spain |
| Publication | [CNMV, net short positions](https://www.cnmv.es/portal/consultas/busqueda.aspx?id=29): "Fichero descargable con las posiciones cortas netas publicadas e históricas" |
| Endpoint | `GET https://www.cnmv.es/DocPortal/Posiciones-Cortas/NetShortPositions.xls` |
| Format | Excel 97-2003 (`.xls`, BIFF); needs the optional extra `pip install -e '.[shorts]'` (`xlrd`) |
| Coverage | Current and closed positions; closed ones go back to June 2010, before the SSR applied |
| Update | Daily (the first sheet holds the publication date; the file is regenerated in the evening) |
| Terms | No reuse licence is stated in the [legal notice](https://www.cnmv.es/portal/utilidades/NotaLegal.aspx) (information is provided for information purposes). Publication is required by law (SSR Art. 9(4)); attribute "CNMV" |

Public disclosure follows SSR Article 6 ([Regulation (EU) No 236/2012](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32012R0236)): a net short position is published when it reaches or falls below 0.5% of the issued share capital "and each 0,1 % above that" (Art. 6(2)), no later than 15:30 on the following trading day (Art. 9(2)), on a central website operated or supervised by the competent authority (Art. 9(4)).

## Sheets
- `Fecha - Date`: the publication date of the file.
- `Última - Current`: the latest position per holder and issuer (a subset of the next sheet).
- `Serie - Series`: every publication of the positions that are still open.
- `Anteriores - Previous`: every publication of closed positions.

Each data sheet has three empty rows, then `LEI; ISIN; Emisor / Issuer; Tenedor de la Posición / Position holder;
Fecha posición / Position date; Posición corta (%) / Net short position (%)`.

## Quirks
- **The LEI is the issuer's**, not the holder's.
- **Below 0.5% is published as 0** (e.g. 0.55 one day, 0 the next): `position_pct` is null and
  `below_threshold` is set.
- **Dates are text with padding** (`" 2026-08-04 "`).
- **No double counting:** the Current sheet repeats rows of the Series sheet; `record_id` collapses them.
- **Pre-SSR rows** (June 2010 to October 2012, from the earlier Spanish disclosure regime) are kept when `--start`
  allows it; `--start 2012-11-01` drops them.

## Observed (full history, ingested 20 October 2026)
11,649 positions from 1 November 2012 to 16 October 2026 (plus earlier rows with an earlier `--start`); 741
below-threshold disclosures (90 of them published as 0); issuer LEI on every row; 2 rows from 1 holder classified
as a natural person (pseudonymized).
