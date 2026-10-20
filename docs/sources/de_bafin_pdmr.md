# `de_bafin_pdmr`: Germany, BaFin, managers' transactions (MAR 19): blocked

Status: **blocked** (October 2026). No connector ships for this source; this note records why and what would
unblock it.

| | |
|---|---|
| Dataset | `insider_transactions` |
| Authority | Bundesanstalt für Finanzdienstleistungsaufsicht (BaFin), Germany |
| Database | [Managers' transactions (Eigengeschäfte von Führungskräften)](https://portal.mvp.bafin.de/database/DealingsInfo/), a search form (`sucheForm.do`) over the past year of notifications, with a CSV export of result lists |
| robots.txt | `https://portal.mvp.bafin.de/robots.txt` is `User-agent: *` / `Disallow: /` (checked 20 October 2026) |
| Primary storage | The Unternehmensregister (§ 26 WpHG with § 8b HGB) |

## Why it is blocked
- The project crawls only where robots.txt allows it (see `CLAUDE.md`). The whole BaFin portal host that serves
  the database disallows every path for every user agent, so the database and its CSV export are out of reach for
  an automated client, however polite.
- The Unternehmensregister, where issuers must store the notices, disallows its search, publication and register
  paths (`/de/suche`, `/de/veroeffentlichung`, `/de/registerinformationen`, `/en/publication` and others) in
  `https://www.unternehmensregister.de/robots.txt` (checked 20 October 2026).
- Issuers also publish the notices through commercial news services; their terms do not grant reuse, so they are
  not a source for this project.

## What would unblock it
- Written permission from BaFin to retrieve the database export automatically (or a bulk or open-data file), or
  a robots.txt change. Then: `GET sucheForm.do` with the date filters, follow the CSV export link, map the columns
  onto `InsiderTransaction` as for the other EU-template sources, and fetch daily since the database keeps only
  one year.
- A manual route stays possible: a person can download the CSV in a browser; an import-from-file command would
  be needed to ingest it (not built).
