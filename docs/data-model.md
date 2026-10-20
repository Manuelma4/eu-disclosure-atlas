# Data model

The schemas live in `src/disclosure_atlas/models.py`; Parquet types are derived from them, so this page and
the files cannot drift apart.

## `insider_transactions`
One managers' transaction (MAR Article 19), one row per transaction line. Fields follow the EU notification
template (Commission Implementing Regulation (EU) 2016/523): 1 person, 2 reason, 3 issuer, 4 transaction.

| Column | Type | Description |
|---|---|---|
| `record_id` | string | Stable id from the source's natural key |
| `source` | string | Connector name, e.g. `se_fi_pdmr` |
| `jurisdiction` | string | ISO 3166-1 alpha-2 country of the publishing authority |
| `source_url` | string | Where the record was published |
| `published_at` | timestamp (UTC) | Publication by the authority |
| `transaction_date` | date | Template 4e |
| `issuer_name`, `issuer_lei` | string | Template 3a, 3b (LEI checksum-validated) |
| `pdmr_id` | string | Pseudonym of the person discharging managerial responsibilities |
| `pdmr_role` | enum | `ceo`, `cfo`, `chair`, `executive`, `board_member`, `other`, `unknown` (most senior if several) |
| `pdmr_role_raw` | string | Position as published (template 2a) |
| `notifier_id` | string | Pseudonym of the notifying person; equals `pdmr_id` unless a closely associated person notified |
| `notifier_name` | string | Only when the notifier is a legal entity; never for natural persons |
| `notifier_is_legal_entity`, `closely_associated` | bool | |
| `is_amendment`, `amendment_details` | bool, string | Template 2b |
| `linked_to_share_option_programme` | bool | |
| `nature` | enum | `buy`, `sell`, `subscription`, `grant`, `exercise`, `conversion`, `exchange`, `gift`, `inheritance`, `pledge`, `lending`, `internal_transfer`, `merger`, `other` |
| `nature_raw` | string | Template 4b as published |
| `direction` | int | +1 holding increases, -1 decreases, 0 neutral or unknown |
| `instrument_type` | enum | `share`, `option`, `warrant`, `subscription_right`, `convertible`, `bond`, `swap`, `other` |
| `instrument_type_raw`, `instrument_name`, `isin` | string | Template 4a (ISIN checksum-validated) |
| `volume`, `price`, `currency`, `amount` | float, float, string, float | Template 4c; `amount = volume × price` in `currency` |
| `venue`, `on_venue` | string, bool | Template 4f; `on_venue = false` for "outside a trading venue" |
| `is_superseded`, `status_raw` | bool, string | Revised or cancelled at the source |
| `fetched_at`, `parser` | timestamp, string | Provenance |
| `is_open_market` | bool | Gold only: buy or sell of shares on a trading venue |

## `net_short_positions`
One public net short position (SSR Article 6: at least 0.5% of issued share capital, then every 0.1%).

| Column | Type | Description |
|---|---|---|
| `record_id`, `source`, `jurisdiction`, `source_url`, `published_at` | | As above |
| `position_date` | date | Date of the position |
| `holder_name`, `holder_lei` | string | Position holder; legal entities keep their name, natural persons get a pseudonym (`p_...`, stable per issuer) |
| `holder_is_natural_person` | bool | The holder did not look like a legal entity (no LEI, legal form or institutional word) and was pseudonymized |
| `issuer_name`, `issuer_lei`, `isin` | string | Issuer of the shares |
| `position_pct` | float | Net short position in % of issued share capital; null when the source only says "below 0.5%" |
| `below_threshold` | bool | The disclosure that the position fell below 0.5% (published once, then the position is no longer public) |
| `is_superseded`, `fetched_at`, `parser` | | As above |
