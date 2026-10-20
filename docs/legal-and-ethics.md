# Legal and ethical design

This page records engineering decisions. It is not legal advice. Review it with a qualified person before any
public data release.

## Why the data is public
- **Insider trades:** MAR (Regulation (EU) No 596/2014) Article 19 requires persons discharging managerial
  responsibilities and their closely associated persons to notify transactions. Issuers must make the
  notifications public, and national competent authorities publish them.
- **Short positions:** the Short Selling Regulation (Regulation (EU) No 236/2012) Article 6 requires public
  disclosure of net short positions in shares at or above 0.5% of issued share capital (and each 0.1% step
  above) through a central website operated or supervised by the competent authority.

## Personal data (GDPR)
Managers are natural persons, so their names are personal data even though the law requires publication.
The project minimises what it republishes:
- **Pseudonyms, not names:** natural persons appear only as keyed, per-issuer pseudonyms (`pdmr_id`,
  `notifier_id`). See [decisions/0002](decisions/0002-pseudonymize-natural-persons.md).
- **Legal entities keep their names** (for example a family holding company that is a closely associated
  person), because data about legal persons is generally not personal data. Open question for review before
  release: holding companies named after an individual can indirectly identify them. A strict publishing mode
  that also pseudonymises those names is an option.
- **Raw files are not redistributed:** bronze contains names exactly as published and stays private.
- **Retention:** MAR requires issuers to keep the information public for at least five years. A retention
  policy aligned with that period is to be defined before public release.
- **Rights requests:** a contact and takedown process will be documented with the first public release.

## Source terms and crawling
- Each source note (`docs/sources/<source>.md`) records the terms of use and the access method.
- Crawling is polite: an identifying user agent, at most one request per second per host, back-off on
  errors, and only public pages and documented exports.
- Attribution: published datasets name every source authority.

## Not investment advice
The data and research describe the past. They are not recommendations. Providing investment recommendations
or managing money for others requires authorisation (in France, from the AMF).
