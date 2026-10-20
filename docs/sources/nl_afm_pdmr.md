# `nl_afm_pdmr`: Netherlands, AFM, register of managers' transactions (MAR 19)

| | |
|---|---|
| Dataset | `insider_transactions` |
| Authority | Autoriteit Financiële Markten (AFM), Netherlands |
| Register | [Managers' transactions MAR 19](https://www.afm.nl/en/sector/registers/meldingenregisters/transacties-leidinggevenden-mar19-) |
| Index | `GET https://www.afm.nl/export.aspx?type=0ee836dc-5520-459d-bcf4-a4a689de6614&format=xml` (also `format=csv`, fewer columns) |
| Details | `GET https://www.afm.nl/en/sector/registers/meldingenregisters/transacties-leidinggevenden-mar19-/details?id=<meldingid>` (HTML) |
| Format | Index: XML, one `<vermelding>` per notification, the whole register in one file (about 1.2 MB). Details: HTML |
| Coverage | The index holds every notification since 31 December 2005 (about 9,500 in October 2026); about 260 notifications in 90 days |
| Update | Daily ("Date last update" on each page) |
| robots.txt | `Allow: /` except `/sitecore` (checked 20 October 2026) |
| Terms | No reuse licence is stated ("© Copyright AFM, all rights reserved" in the site footer). Publication is required by law (MAR Art. 19(3)); attribute "AFM" |

Managers and their closely associated persons notify the issuer and the competent authority "promptly and no
later than three business days after the date of the transaction", and the issuer makes the information public
within the same delay (Art. 19(1) and 19(3) of [Regulation (EU) No 596/2014](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32014R0596)),
using the template of [Implementing Regulation (EU) 2016/523](https://eur-lex.europa.eu/eli/reg_impl/2016/523/oj).

## Index fields (XML)
`meldingid; transactiedatum; uitgevendeinstelling; meldingsplichtige; meldingsplichtigeachternaam;
nauwgelieerdaan; functie; lei; vermelding`. The CSV export carries only the first four.

## Detail page
A field list (`Notifiable`, `Closely affiliated` when the notifier is a closely associated person, `Issuing
institution`, `LEI`, `Position/Status`, `Transaction`), then two tables: `Transactions` (one row per line:
`Instrument type; ISIN; Transaction category; Transaction type; Stock option program; Trading place; Price;
Quantity; Unit`) and `Aggregated information`.

## Mapping
- `Notifiable` is the notifier; for a closely associated person, `Closely affiliated` names the PDMR. Both are
  pseudonymized within the issuer (LEI); a notifier that is a legal entity keeps its name.
- `nature_raw` is `"<category> / <type>"`. The category (`Verwerving` acquisition, `Vervreemding` disposal)
  sets the direction; the type sets the nature: `Koop` buy, `Verkoop` sell, `Uitoefening ...` and
  `Omwisseling i.v.m. uitoefening` exercise, `Omwisseling van soort effect` conversion, `Schenking/gift` gift,
  `Uitlenen` lending (direction 0). The generic `Verwerving`, `Dividend` and `Overdracht` (transfer) map to
  `other`: a generic acquisition is often a free award, not a purchase.
- Instruments: `Gewoon aandeel`, `Aandelen A/B`, depository receipts are shares; `Personeelsoptie` an option;
  `obligatie` a bond. Conditional or restricted share awards and rights are `other` (rights to shares).
- Positions are free text, mostly English; Dutch terms are rewritten (`voorzitter` chair, `commissaris`
  supervisory board, `raad van bestuur`/`bestuurder` executive). A two-tier company's "Management Board" is its
  executive body, and a "Business Group President" is an executive, not a chair.
- `amount = Price x Quantity`; `on_venue` is false for `OTC`.

## Quirks
- **No publication date.** Neither the index nor the page gives one; the `meldingid` starts with the
  transaction date (`YYYYMMDD`), not the filing date. `published_at` is null, and a fetch window selects
  notifications by transaction date, reaching back `LATE_FILING_DAYS` (14) days so that a notification
  published after an earlier run still gets picked up (re-fetched pages dedupe by `record_id`).
- **Unit is the currency** (`EUR`, `USD`, `GBP`), not a unit of quantity.
- **Aggregated table has shifted labels.** In `Aggregated information` the `Stock option program` header is
  missing while its value is present, so labels and values are off by one; only `Transactions` is parsed.
- **Names inside positions.** For relatives, `Position/Status` sometimes names a person. When it contains a word
  of the notifier's or the PDMR's name, `pdmr_role_raw` is dropped so no name reaches silver.
- **Line ids.** A line has no id of its own; `record_id` is (`meldingid`, line index), stable while AFM keeps
  the order of lines.
- **No amendment flag.** Corrections appear as new notifications; `is_amendment` stays false.
- Each page ends with a "Date last update" that changes daily, so re-fetched pages get a new bronze hash.

## Observed (90 days, ingested 20 October 2026)
`ingest nl_afm_pdmr --start 2026-07-22 --end 2026-10-20`: 290 notifications (transaction dates 8 July to
12 October 2026, the grace period included), 441 lines.
- **LEI:** 441 of 441 lines (100%) carry a valid issuer LEI.
- **ISIN:** 391 of 441 lines (89%) carry a valid ISIN; all 355 share lines do. The 50 without one are awards,
  rights and options.
- Natures: 123 buy, 116 sell, 105 other (52 `Dividend`, 48 generic `Verwerving`, 5 `Overdracht`), 53 exercise,
  29 conversion, 7 grant, 6 gift, 2 lending.
- 61 lines by closely associated persons, 55 lines notified by a legal entity; 3 positions dropped because they
  named a person.
- Currencies: EUR 314, USD 81, GBP 40, NOK 5, ZAR 1.
- Roles (parser as shipped): 245 executive, 108 board member, 34 CEO, 25 other, 23 CFO, 3 chair, 3 unknown.
