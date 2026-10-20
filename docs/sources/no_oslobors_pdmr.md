# `no_oslobors_pdmr`: Norway, Oslo Børs NewsWeb, managers' transactions

| | |
|---|---|
| Dataset | `insider_transactions` |
| Publisher | Oslo Børs NewsWeb (Euronext), the officially appointed mechanism (OAM) in Norway; it stayed the OAM when supervision of disclosure moved from Oslo Børs to Finanstilsynet on 1 April 2025 ([Wikborg Rein](https://www.wr.no/en/news/the-norwegian-fsa-assumes-the-role-as-norwegian-take-over-authority-and-supervisory-authority-for-disclosure-regime), [Thommessen](https://www.thommessen.no/en/news/new-supervisory-regime-for-issuers-of-instruments-listed-on-the-oslo-stock-exchange)) |
| Register | [NewsWeb, category "Mandatory notification of trade primary insiders"](https://newsweb.oslobors.no/search?category=1102) |
| List | `POST https://api3.oslo.oslobors.no/v1/newsreader/list?category=1102&fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD` (JSON; `overflow` is true when the window holds too many messages) |
| Message | `POST https://api3.oslo.oslobors.no/v1/newsreader/message?messageId=<id>` |
| Attachment | `GET https://api3.oslo.oslobors.no/v1/newsreader/attachment?messageId=<id>&attachmentId=<id>` |
| Format | JSON for lists and messages (the endpoints NewsWeb's own front end calls; GET on list and message returns 405). Attachments are PDFs, sometimes other files |
| Coverage | NewsWeb keeps the full archive of the category; the parser was checked on messages from May 2025, February, March and September 2026 |
| Terms | No reuse licence is stated for NewsWeb. The announcements are regulated information that issuers must make public. `newsweb.oslobors.no/robots.txt` returns the web app (no robots rules) and the API host has none. The connector sends one request per second and identifies itself |

## Message fields
`messageId`, `newsId`, `title`, `category` (id 1102), `markets`, `issuerId`, `issuerSign`, `issuerName`,
`publishedTime` (UTC, ISO 8601), `correctionForMessageId`, `correctedByMessageId`, `numbAttachments`,
`attachments[{id, name}]`, `body` (free text, not read).

## Attachments
1. **Finanstilsynet's form KRT-1500** printed to PDF, about half of the messages. Two layouts:
   - the labelled form "Skjemaversjon 46345" in Norwegian or English ("Etternavn"/"Last name", "Stilling / Rolle",
     "Transaksjonstype", "Pris per enhet"/"Price per unit", "Volum", "Dato", "Handelsplass");
   - since 2026, a numbered form ("1.4.2 Fullt navn", "2.4.1 Transaksjonstype : Kjøp", "2.8.1 Gjennomsnittlig pris
     per enhet : 21,423", "2.10.1 Handelsplass : XOSL - Oslo Børs").
2. **The issuer's own 2016/523 template** (Word table to PDF), read by the shared template reader
   (`connectors/_mar_template.py`), which only keeps values it can read unambiguously.
3. Press releases, presentations and scans without a text layer: not read.

When a message carries both a KRT-1500 form and a template, only the form is read (they describe the same
trades). The bronze manifest marks each attachment with `form` and `use`.

## Mapping and privacy
- One record per price/volume line, else one per transaction with the aggregated volume and average price.
- `record_id` is derived from the attachment's content hash: the same form attached to a Norwegian and an
  English announcement gives one record.
- `published_at` is the message's `publishedTime`. A message with `correctedByMessageId` has its records marked
  `is_superseded`; a KRT-1500 correction ("Korrigering", or a correction text) sets `is_amendment`.
- Names: the manager, a closely associated person and the reporter (often a lawyer) are pseudonymized or
  dropped. A closely associated company keeps its name unless it shares a word with the manager's name
  ("<surname> Holding AS").
- Selections in the labelled KRT-1500 layout are check boxes that do not survive text extraction, so the form
  is read from its sections: a "Nærstående foretak" section means a closely associated company; a filled
  "Beskrivelse av korrigering" means a correction. Scanned forms mark the chosen box with "(•)".
- Dates: Norwegian layouts write `dd.mm.yyyy`; the English layout writes US dates (`2/3/2026` is 3 February),
  and the connector keeps the reading that is not after the publication date.
- Free text (comments, correction descriptions, message bodies) is never stored.

## Quirks
- Issuers sometimes publish the same notification twice (Norwegian and English messages); identical
  attachments collapse into one record, different documents for the same trade do not.
- About a fifth of the sampled messages have no readable form (press release only, scans, layouts the
  template reader rejects); the listing's bronze manifest counts them as `unread`.
- KRT-1500 venues are MIC codes with a name ("XOFF - Utenfor en handelsplass" is off venue).

## Observed
- 6 to 19 October 2026: 29 messages with attachments, 18 with a readable form; 59 transactions, 58 with a volume,
  53 with a price, all with ISIN and issuer LEI.
- Offline check on 318 messages (May 2025, February, March, September 2026): 251 yield records (114 from
  KRT-1500 forms, 137 from issuer templates).
