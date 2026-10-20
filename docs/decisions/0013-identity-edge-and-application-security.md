# 0013: Identity, edge and application security

Status: accepted (2026-10-20)

## Context
The platform has anonymous readers, signed-in users (watchlists, alerts), partners using the API at higher
limits, and internal services. It republishes legally public data that includes pseudonymized personal data.

## Decision
- **Identity:** Keycloak (OIDC / OAuth 2.0). The website uses authorization code + PKCE with passkeys enabled;
  partners use client credentials; services validate JWTs (audience, issuer, expiry) and enforce scopes
  (`alerts:write`, `ask:use`).
- **Edge:** Envoy Gateway implementing the Kubernetes Gateway API: TLS termination, JWT validation, per-client
  rate limits, request size limits and CORS, in front of a CDN with a WAF.
- **Public tier:** anonymous reads with IP rate limits; API keys (stored as hashes) for higher quotas.
- **Threat model:** STRIDE per trust boundary in `docs/security/threat-model.md`, reviewed on each new service.
- **Application controls:** the SQL guard for any model-written query, an allowlist of regulator hosts for
  fetchers (no SSRF), structured logs with personal-data redaction, strict Content-Security-Policy on the web.
- **Verification:** CodeQL, dependency audits (pip-audit, npm audit, govulncheck), container scanning, and a
  DAST pass (OWASP ZAP baseline) against the preview environment.

## Consequences
- Security decisions are visible and testable; the same controls apply in every environment.
- Keycloak adds an operated component; it is the most widely deployed open-source identity provider.
