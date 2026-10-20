# `be_fsma_pdmr`: Belgium, FSMA, managers' transactions

| | |
|---|---|
| Dataset | `insider_transactions` |
| Authority | Financial Services and Markets Authority (FSMA), Belgium |
| Register | [Managers' transactions](https://www.fsma.be/en/managers-transactions), [search](https://www.fsma.be/en/transaction-search) |
| Listing | `GET https://www.fsma.be/en/transaction-search?issuer=&date[min]=YYYY-MM-DD&date[max]=YYYY-MM-DD&order=field_ct_date_time&sort=asc&page=N` (`page` is 0-based, 50 rows per page) |
| Detail | `GET https://www.fsma.be/en/manager-transaction/<issuer-slug>-<n>`, one transaction per page |
| Format | Server-rendered HTML (Drupal). Each value sits in `div.field--name-field-ct-<name> .field__item` |
| Coverage | The search returns notifications published since 2020; earlier years are sparse |
| Terms | "The information available on the FSMA's websites comes under a Creative Commons Attribution (CC BY 4.0) licence" ([disclaimer and copyright](https://www.fsma.be/en/disclaimer-copyright)). Attribute "FSMA". The FSMA publishes the notifications under Article 25, §2 of the Law of 2 August 2002; their content is the sole responsibility of the notifying persons ([FSMA](https://www.fsma.be/en/managers-transactions)) |
| Politeness | [robots.txt](https://www.fsma.be/robots.txt) sets `Crawl-delay: 30`; the connector waits 30 seconds between requests. `/en/transaction-search` and `/en/manager-transaction/` are not disallowed (`/en/search` is) |

## Fields (detail page, `field-ct-*` class names)
`date-time` (publication date), `declarer-name` (notifying person), `declarer-type`, `description` (for a
closely associated person: the manager they are linked to), `issuer`, `instrument-type`,
`instrument-isin-code`, `transaction-type`, `transaction-type-spec` (optional detail, e.g. "Transaction linked to
remuneration package: ..."), `transaction-place`, `transaction-date`, `transaction-currency`,
`transaction-quantity`, `price`, `amount`, `body` (free-text comment). Labels are translated per language, so
the parser keys on the class names.

## Mapping and privacy
- One record per detail page; `record_id` comes from the Drupal node id, which is the same in every language.
- `published_at` is the publication date at midnight in Brussels (no time is published), stored in UTC.
- Numbers: the `content` attribute holds the exact value (`3.249`) while the page shows a rounded Belgian
  format (`3,25`, `10.000` for ten thousand); the connector reads `content` and only falls back to the display.
- Roles: "Senior executive" maps to `executive`; "Member of administrative management or supervisory body" to
  `board_member`. "Person closely associated to ..." sets `closely_associated`.
- The notifying person keeps their name only when it is recognizably a legal entity. Belgian management
  companies often have short names without a legal form ("BVHX"); those are pseudonymized like natural persons.
- The `description` of a closely associated person's notification is pseudonymized as the PDMR and never
  stored. The free-text `body` is never stored.
- "Off exchange" venues set `on_venue = false`. A transaction type of "Other" is normalized from its
  specification ("Donation" -> gift).
- No LEI and no initial/amendment flag are published.

## Quirks
- Detail slugs are numbered per issuer with gaps (not every number is public).
- A full backfill is slow by design: one request per 30 seconds, one request per transaction.
- The MAR notification threshold is EUR 20,000 per calendar year ([FSMA](https://www.fsma.be/en/managers-transactions);
  Regulation (EU) 2024/2809 raised it from EUR 5,000 from 4 December 2024).
