-- Alerts service data: watchlists, alert rules, webhook endpoints and the delivery log.
-- Owners are OIDC subjects (Keycloak `sub`); the service owns the `alerts` schema and nothing else.
CREATE SCHEMA IF NOT EXISTS alerts;

CREATE TABLE alerts.watchlists (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner       text NOT NULL,
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX watchlists_owner_idx ON alerts.watchlists (owner);

CREATE TABLE alerts.watchlist_issuers (
    watchlist_id uuid NOT NULL REFERENCES alerts.watchlists (id) ON DELETE CASCADE,
    issuer_lei   text NOT NULL CHECK (issuer_lei ~ '^[0-9A-Z]{18}[0-9]{2}$'),
    added_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (watchlist_id, issuer_lei)
);
CREATE INDEX watchlist_issuers_lei_idx ON alerts.watchlist_issuers (issuer_lei);

CREATE TABLE alerts.webhook_endpoints (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner       text NOT NULL,
    url         text NOT NULL,
    secret      text NOT NULL, -- Standard Webhooks signing secret (whsec_...), shown to the owner once
    description text NOT NULL DEFAULT '',
    enabled     boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX webhook_endpoints_owner_idx ON alerts.webhook_endpoints (owner);

CREATE TABLE alerts.alert_rules (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner               text NOT NULL,
    name                text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    watchlist_id        uuid REFERENCES alerts.watchlists (id) ON DELETE CASCADE, -- NULL: every issuer
    natures             text[] NOT NULL DEFAULT '{}', -- empty: any nature
    roles               text[] NOT NULL DEFAULT '{}',
    countries           text[] NOT NULL DEFAULT '{}',
    open_market_only    boolean NOT NULL DEFAULT false,
    min_amount_eur      double precision CHECK (min_amount_eur >= 0),
    email               text,
    webhook_endpoint_id uuid REFERENCES alerts.webhook_endpoints (id) ON DELETE SET NULL,
    enabled             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX alert_rules_owner_idx ON alerts.alert_rules (owner);
CREATE INDEX alert_rules_watchlist_idx ON alerts.alert_rules (watchlist_id) WHERE enabled;

-- One row per notification (rule × filing × channel); its id is the notification id and the
-- `webhook-id`, so replays of the same filing never deliver twice.
CREATE TABLE alerts.deliveries (
    id              uuid PRIMARY KEY,
    owner           text NOT NULL,
    rule_id         uuid NOT NULL REFERENCES alerts.alert_rules (id) ON DELETE CASCADE,
    filing_id       text NOT NULL,
    channel         text NOT NULL CHECK (channel IN ('webhook', 'email')),
    target          text NOT NULL,
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'retrying', 'delivered', 'failed')),
    attempts        integer NOT NULL DEFAULT 0,
    response_status integer,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    delivered_at    timestamptz
);
CREATE INDEX deliveries_owner_created_idx ON alerts.deliveries (owner, created_at DESC);
