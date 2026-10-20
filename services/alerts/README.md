# alerts (Go)

Watchlists, alert rules and notification delivery for signed-in users and partners.

```
atlas.filings.v1 ──evaluator──▶ atlas.notifications.v1 ──delivery──▶ webhook (Standard Webhooks) / email
                                   │ failure
                                   ▼
                     .retry.5m ──▶ .retry.1h ──▶ .dlq
```

- **API** (`/v1`, JSON, RFC 9457 errors): `watchlists` (+ `PUT|DELETE /v1/watchlists/{id}/issuers/{lei}`),
  `rules`, `webhook-endpoints` (the signing secret is returned once, on creation) and `deliveries`.
  Keycloak access tokens are verified against the realm's JWKS (issuer, audience `atlas-alerts`, expiry); reads
  need `alerts:read` or `alerts:write`, writes `alerts:write`. Every row is scoped to the token's `sub`.
- **Evaluator** (consumer group `atlas-alerts-evaluator`, starts at the end of the topic): matches each
  `filing.published.v1` event against enabled rules (watchlist issuers, natures, roles, countries, open-market
  only, minimum EUR amount), ignores events first seen more than `ATLAS_ALERTS_MAX_EVENT_AGE` ago (replays),
  records a pending delivery and produces one notification per channel, keyed by owner.
- **Delivery** (groups `atlas-alerts-delivery`, `-retry-5m`, `-retry-1h`, starting at the beginning of their
  topics): one consumer group per tier. A retry tier waits until a record has
  spent its delay in the topic, then retries; failures move on to the next tier and finally to the dead-letter
  topic, with the attempt count and last error. Offsets are committed after handling (at-least-once).
- **Idempotence:** the notification id is a UUIDv5 of rule × filing × channel; it is the deliveries primary
  key and the `webhook-id`, so a replayed filing or redelivered record never notifies twice.
- **Webhooks:** `webhook-id`, `webhook-timestamp` and `webhook-signature: v1,<base64 HMAC-SHA256>` over
  `id.timestamp.body` with a `whsec_` secret ([Standard Webhooks](https://www.standardwebhooks.com)); https only,
  no redirects, 10 s timeout, and non-public IP addresses refused at dial time (SSRF), unless
  `ATLAS_ALERTS_ALLOW_PRIVATE_WEBHOOKS=true` (local development only).
- **Email:** `ATLAS_EMAIL_PROVIDER=smtp` (`ATLAS_SMTP_ADDR`, optional `ATLAS_SMTP_USER`/`ATLAS_SMTP_PASSWORD`) or
  `resend` (`ATLAS_RESEND_API_KEY`), with `ATLAS_EMAIL_FROM`. Unset: email notifications go to the dead-letter topic.
- **Storage:** PostgreSQL schema `alerts`, migrations embedded in the binary (`internal/store/migrations`) and
  applied at start under an advisory lock.
- **Operations:** `:8080` with `/healthz`, `/readyz` (database), `/metrics` (`atlas_alerts_*`, Go runtime), OTLP
  traces and metrics when `OTEL_EXPORTER_OTLP_ENDPOINT` is set, JSON logs, graceful shutdown.
  `ATLAS_ALERTS_ROLES=api,evaluator,delivery` selects the parts to run, so each scales on its own signal.

| Variable | Default |
|---|---|
| `ATLAS_DATABASE_URL` | `postgres://atlas:atlas-dev-only@localhost:5432/atlas` |
| `ATLAS_KAFKA_BOOTSTRAP` (or `KAFKA_BROKERS`) | `localhost:19092` |
| `ATLAS_OIDC_ISSUER` / `ATLAS_OIDC_AUDIENCE` / `ATLAS_OIDC_JWKS_URL` | `http://localhost:8180/realms/atlas` / `atlas-alerts` / issuer certs |
| `ATLAS_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` |
| `ATLAS_ALERTS_RETRY_DELAYS` | `5m,1h` |
| `ATLAS_ALERTS_MAX_EVENT_AGE` | `24h` |
| `ATLAS_WEBSITE_URL` | `http://localhost:3000` (links in emails) |

`cmd/webhook-sink` is a development receiver that verifies signatures (`WEBHOOK_SECRET`, comma-separated) and
lists what it received on `GET /deliveries`.

```bash
# unit tests; the PostgreSQL tests run when ATLAS_TEST_DATABASE_URL points to a server (admin connection)
docker run --rm -v "$PWD":/src -w /src/services/alerts golang:1.27 go test -race ./...
```
