# 0002: Pseudonymize natural persons

Status: accepted (2026-10-20)

## Context
MAR requires managers' transactions to be public, so the names are published by authorities. Republishing
them in bulk, in a new, searchable, cross-country dataset, is a new processing purpose under GDPR. Most
research and investor use cases need the role ("the CFO bought"), not the name.

## Decision
- Natural persons become `p_` + HMAC-SHA256(key, issuer scope + normalized name), truncated to 20 hex
  characters. The key comes from `ATLAS_PSEUDONYM_KEY` (a CI secret for published data) or from a private
  local key file.
- The scope is the issuer, so ids are stable within one issuer (cluster-buy detection works) and unlinkable
  across issuers.
- Legal entities, detected from legal forms and corporate keywords, keep their names. Unknown cases are
  treated as natural persons, so the heuristic fails safe.
- Raw bronze files keep names as published and are never redistributed.

## Consequences
- Cross-issuer analysis of the same person is not possible from the published data, by design.
- Changing the key changes every person id and record id, so the published dataset uses one fixed key.
