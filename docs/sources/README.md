# Sources

One row per authority. Statuses: **live**, planned, researching, blocked (with the reason). Each live source
has a note `docs/sources/<source>.md` covering endpoint, format, coverage, terms and quirks.

| Jurisdiction | Authority | Insider trades (MAR 19) | Net short positions (SSR 6) | Notes |
|---|---|---|---|---|
| SE | Finansinspektionen (FI) | **live**: [`se_fi_pdmr`](se_fi_pdmr.md) | **live**: [`se_fi_shorts`](se_fi_shorts.md) | Open data, CSV export, history since 3 July 2016 |
| FR | AMF (BDIF) | **live**: [`fr_amf_pdmr`](fr_amf_pdmr.md) | **live**: [`fr_amf_shorts`](fr_amf_shorts.md) | Insider trades: JSON listing + one PDF per notification (EU template, French and English since August 2026), history since February 2021, rule-based parser with an optional LLM fallback |
| DE | BaFin | planned (WS2) | **live**: [`de_bafin_shorts`](de_bafin_shorts.md) | BaFin's managers' transactions database shows only the past year; primary storage is the company register |
| NL | AFM | planned (WS2) | **live**: [`nl_afm_shorts`](nl_afm_shorts.md) | Public registers with export |
| IE | Central Bank of Ireland | blocked (see below) | **live**: [`ie_cbi_shorts`](ie_cbi_shorts.md) | Net short positions file includes a historical table |
| ES | CNMV | **live**: [`es_cnmv_pdmr`](es_cnmv_pdmr.md) | **live**: [`es_cnmv_shorts`](es_cnmv_shorts.md) | Insider trades: HTML listing + one CNMV-generated form PDF per notification, since May 2018. Short positions: one Excel workbook, current and closed positions since 2010 |
| IT | Consob | researching | blocked: the site sits behind a Radware CAPTCHA for automated clients (October 2026); the project does not bypass CAPTCHAs | Consob offers an Excel file of current and historic positions; needs an agreed access route (e.g. ask Consob) |
| BE | FSMA | **live**: [`be_fsma_pdmr`](be_fsma_pdmr.md) | researching (WS4) | Structured HTML detail page per transaction, CC BY 4.0, 30 s crawl delay |
| DK | Finanstilsynet (OAM) | **live**: [`dk_finanstilsynet_pdmr`](dk_finanstilsynet_pdmr.md) | researching (WS4) | JSON search + issuer-made template PDFs/HTML, since 2015 |
| NO | Oslo Børs NewsWeb (OAM), Finanstilsynet forms | **live**: [`no_oslobors_pdmr`](no_oslobors_pdmr.md) | researching (WS4) | JSON API + KRT-1500 form PDFs or issuer templates |
| FI | FIN-FSA | blocked (see below) | researching (WS4) | No authority register; issuers' releases on Nasdaq Helsinki |
| PT | CMVM | deferred (see below) | researching (WS4) | Reachable through the SDI web app's private API; issuer-made PDFs |
| PL | KNF | blocked (see below) | researching (WS4) | Notifications are attachments to ESPI/EBI reports; GPW blocks scripted downloads |
| AT | FMA | blocked (see below) | researching (WS4) | Cloudflare challenge; robots.txt disallows the CSV export |
| others | | researching | researching (WS4) | |

## Insider trades: dead ends and deferred sources (WS3, October 2026)
- **Austria (FMA).** The directors' dealings pages sit behind a Cloudflare managed challenge ("Just a moment...")
  for scripted clients, and [robots.txt](https://www.fma.gv.at/robots.txt) disallows
  `/wp-content/plugins/dw-fma/csvexport.php`, the register's CSV export. The project does not solve challenges
  or ignore robots.txt. Route: ask the FMA for an export or an agreed access.
- **Ireland (Central Bank of Ireland).** Managers notify the CBI through its private portal; the CBI publishes
  no register ([CBI](https://www.centralbank.ie/regulation/industry-market-sectors/securities-markets/market-abuse-regulation/notification-of-managers-transactions)).
  Issuers publish through regulatory news services; Euronext Dublin's company news pages answer scripted
  clients with an AWS WAF challenge. Route: ESAP once it covers MAR notifications.
- **Poland (KNF).** KNF only receives notifications (form at 19mar.knf.gov.pl, with a CAPTCHA); issuers publish
  them as ESPI/EBI current reports. GPW's report listing and detail pages are reachable (they carry the ESAP
  type `TRANSD`), but the attachments that hold the transaction details are blocked for scripted clients by a
  web application firewall. Without them a connector would only know who reported and when.
- **Finland (FIN-FSA).** FIN-FSA receives notifications through an authenticated e-service and publishes no
  register; the public record is the issuer's stock exchange release on Nasdaq Helsinki. Nasdaq's news API
  serves those releases as JSON with the template in structured text, but it is a commercial exchange feed
  without stated reuse terms. Route: confirm Nasdaq's terms, then a connector on that API is straightforward.
- **Portugal (CMVM).** The SDI's "Transações de dirigentes" list (since 2008) is reachable without login, but
  only through the private OutSystems API behind the web app (a CSRF cookie handshake and version tokens that
  change on redeploys), the documents are issuer-made PDFs mixing press release and template, and no reuse
  terms are stated. Deferred until a stable route or the CMVM's agreement; the shared template reader
  (`connectors/_mar_template.py`) would need Portuguese labels.
- **Italy (Consob).** Not surveyed for insider trades in WS3: the site serves a CAPTCHA to automated clients
  (see the short positions row).

## Reference data (WS5)
| Source | Use |
|---|---|
| GLEIF API | LEI to legal name, country, status |
| ESMA FIRDS | ISIN to instrument and issuer LEI |
| OpenFIGI | ISIN to exchange tickers |
| ECB euro reference rates | Amounts in EUR |
