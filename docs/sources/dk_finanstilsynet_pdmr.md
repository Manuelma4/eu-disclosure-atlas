# `dk_finanstilsynet_pdmr`: Denmark, Finanstilsynet OAM, managers' transactions

| | |
|---|---|
| Dataset | `insider_transactions` |
| Authority | Finanstilsynet (Danish Financial Supervisory Authority), as the officially appointed mechanism (OAM) |
| Register | [Selskabsmeddelelser](https://www.finanstilsynet.dk/finansielle-temaer/kapitalmarked/selskabsmeddelelser): "all publicly available announcements reported to Finanstilsynet's OAM" |
| Search | `POST https://appft.gold.extension.gopublic.dk/api/9217fa13-5d9a-46c6-9921-69ee7e6cfaf6/search` with a JSON body: category filter `MANTRA`/`FT_MANTRA` ("Managers' transactions"), `PublicationDateFilter` `{min, max}` (ISO dates, inclusive), `page` (1-based), `pageSize` 100 (200 is refused) |
| Details | `GET .../details/{id}`: announcement id, publication and registration time, issuer (name, CVR number, LEI), reporting party, document links |
| Documents | Public blob storage, `https://saesappublicprd.blob.core.windows.net/stc-public-submissions/<yyyy>/<mm>/<submission>/documents/<file name>` (file names contain spaces and must be percent-encoded) |
| Format | JSON (the search app embedded in finanstilsynet.dk; the module id comes from the iframe's `config` parameter and may change if the page is rebuilt). Documents are issuer-made PDFs or HTML pages |
| Coverage | From 12 May 2015 (legacy rows bulk-imported with ids below 100,000); about 45 announcements a month |
| Terms | No reuse licence is stated for the search; www.finanstilsynet.dk/robots.txt allows everything. The announcements are regulated information that issuers must make public. The connector sends one request per second and identifies itself |

## Documents
An announcement holds one or more documents: the issuer's press release, the 2016/523 template (Word table to
PDF, or an HTML table), often in Danish and English, sometimes one file per person. The template is read by the
shared reader (`connectors/_mar_template.py`), which understands English and Danish labels and only keeps
values it can read unambiguously (a price written as "see the appendix" stays empty).

When both languages carry the template, only the English documents are read (Danish otherwise), so a trade
is not counted twice. The bronze manifest marks each document with `language` and `use`.

## Mapping and privacy
- `published_at` is the announcement's "Published" time (Copenhagen local time, stored in UTC).
- The issuer LEI comes from the template, else from the announcement.
- `record_id` is derived from the document's content hash, the notification's index in it and the line.
- `source_url` is the announcement's details record, never the document URL: issuers often put the manager's
  name in the file name.
- A legal entity as the notifying person is always a closely associated person (managers are natural persons).
  The position text of a closely associated person usually names the manager; only the normalized role is
  kept, and a name after "closely associated with", "related party to" or "nærtstående til" becomes the
  manager's pseudonym.
- Danish documents use Danish number formats ("3.575 stk.", "372,98 kr."): numbers in documents titled
  "(Danish)" are read that way; "kr." means DKK.
- A transaction date after the publication date is a typo and is left empty.

## Quirks
- Some blobs start with a multipart header before `%PDF-`; the reader skips it.
- Announcements whose documents are only press releases (for example weekly buy-back reports in the same
  category) produce no record; the listing's bronze manifest counts them as `unread`.
- Some issuers publish the same template in a new announcement each day of a buy-back programme.

## Observed (20 August to 19 October 2026)
156 announcements with documents; 130 of them yield records (the others are press releases, buy-back reports or
layouts the reader rejects): 274 transactions, 267 with a volume, 264 with a price, 273 with the issuer LEI;
165 by closely associated persons.
