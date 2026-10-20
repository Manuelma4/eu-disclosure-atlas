// Package store keeps watchlists, alert rules, webhook endpoints and deliveries in PostgreSQL.
// Every read and write for a user is scoped by owner (the OIDC subject).
package store

import (
	"context"
	"embed"
	"errors"
	"fmt"
	"io/fs"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed migrations/*.sql
var migrations embed.FS

// ErrNotFound is returned when a row does not exist or belongs to another owner.
var ErrNotFound = errors.New("not found")

// ErrInvalid wraps constraint violations (bad LEI, missing channel, unknown reference...).
var ErrInvalid = errors.New("invalid")

type Watchlist struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	Issuers   []string  `json:"issuers"`
	CreatedAt time.Time `json:"created_at"`
}

type Endpoint struct {
	ID          string    `json:"id"`
	URL         string    `json:"url"`
	Description string    `json:"description"`
	Enabled     bool      `json:"enabled"`
	Secret      string    `json:"secret,omitempty"` // only returned on creation
	CreatedAt   time.Time `json:"created_at"`
}

type Rule struct {
	ID                string    `json:"id"`
	Owner             string    `json:"-"`
	Name              string    `json:"name"`
	WatchlistID       *string   `json:"watchlist_id"`
	Natures           []string  `json:"natures"`
	Roles             []string  `json:"roles"`
	Countries         []string  `json:"countries"`
	OpenMarketOnly    bool      `json:"open_market_only"`
	MinAmountEUR      *float64  `json:"min_amount_eur"`
	Email             *string   `json:"email"`
	WebhookEndpointID *string   `json:"webhook_endpoint_id"`
	Enabled           bool      `json:"enabled"`
	CreatedAt         time.Time `json:"created_at"`
	WebhookURL        string    `json:"-"` // filled by MatchingRules
}

type Delivery struct {
	ID             string     `json:"id"`
	Owner          string     `json:"-"`
	RuleID         string     `json:"rule_id"`
	FilingID       string     `json:"filing_id"`
	Channel        string     `json:"channel"`
	Target         string     `json:"target"`
	Status         string     `json:"status"`
	Attempts       int        `json:"attempts"`
	ResponseStatus *int       `json:"response_status"`
	LastError      *string    `json:"last_error"`
	CreatedAt      time.Time  `json:"created_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
	DeliveredAt    *time.Time `json:"delivered_at"`
}

// Store is the PostgreSQL implementation.
type Store struct{ pool *pgxpool.Pool }

// Open connects and applies pending migrations.
func Open(ctx context.Context, url string) (*Store, error) {
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		return nil, err
	}
	s := &Store{pool: pool}
	if err := s.migrate(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("migrate: %w", err)
	}
	return s, nil
}

func (s *Store) Close()                         { s.pool.Close() }
func (s *Store) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

// migrate applies migrations/*.sql in name order, once each, under an advisory lock so concurrent
// replicas starting together do not race.
func (s *Store) migrate(ctx context.Context) error {
	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()
	if _, err := conn.Exec(ctx, "SELECT pg_advisory_lock(7_240_813)"); err != nil {
		return err
	}
	defer conn.Exec(context.Background(), "SELECT pg_advisory_unlock(7_240_813)") //nolint:errcheck
	if _, err := conn.Exec(ctx, `CREATE TABLE IF NOT EXISTS public.alerts_schema_migrations (
		version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return err
	}
	names, err := fs.Glob(migrations, "migrations/*.sql")
	if err != nil {
		return err
	}
	sort.Strings(names)
	for _, name := range names {
		version := strings.TrimSuffix(strings.TrimPrefix(name, "migrations/"), ".sql")
		var applied bool
		if err := conn.QueryRow(ctx, "SELECT EXISTS (SELECT 1 FROM public.alerts_schema_migrations WHERE version = $1)",
			version).Scan(&applied); err != nil {
			return err
		}
		if applied {
			continue
		}
		sql, err := migrations.ReadFile(name)
		if err != nil {
			return err
		}
		tx, err := conn.Begin(ctx)
		if err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, string(sql)); err != nil {
			_ = tx.Rollback(ctx)
			return fmt.Errorf("%s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, "INSERT INTO public.alerts_schema_migrations (version) VALUES ($1)", version); err != nil {
			_ = tx.Rollback(ctx)
			return err
		}
		if err := tx.Commit(ctx); err != nil {
			return err
		}
	}
	return nil
}

func classify(err error) error {
	var pg *pgconn.PgError
	if errors.As(err, &pg) {
		switch pg.Code {
		case "23514", "23503", "23502", "22P02": // check, foreign key, not null, invalid text representation
			return fmt.Errorf("%w: %s", ErrInvalid, pg.Message)
		}
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	return err
}

func affected(tag pgconn.CommandTag, err error) error {
	if err != nil {
		return classify(err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// Watchlists -----------------------------------------------------------------------------------------------

const watchlistSelect = `SELECT w.id, w.name, w.created_at,
	coalesce(array_agg(i.issuer_lei ORDER BY i.issuer_lei) FILTER (WHERE i.issuer_lei IS NOT NULL), '{}')
	FROM alerts.watchlists w LEFT JOIN alerts.watchlist_issuers i ON i.watchlist_id = w.id`

func scanWatchlists(rows pgx.Rows) ([]Watchlist, error) {
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Watchlist, error) {
		var w Watchlist
		err := r.Scan(&w.ID, &w.Name, &w.CreatedAt, &w.Issuers)
		return w, err
	})
}

func (s *Store) ListWatchlists(ctx context.Context, owner string) ([]Watchlist, error) {
	rows, err := s.pool.Query(ctx, watchlistSelect+` WHERE w.owner = $1 GROUP BY w.id ORDER BY w.created_at`, owner)
	if err != nil {
		return nil, err
	}
	return scanWatchlists(rows)
}

func (s *Store) GetWatchlist(ctx context.Context, owner, id string) (Watchlist, error) {
	rows, err := s.pool.Query(ctx, watchlistSelect+` WHERE w.owner = $1 AND w.id = $2 GROUP BY w.id`, owner, id)
	if err != nil {
		return Watchlist{}, classify(err)
	}
	lists, err := scanWatchlists(rows)
	if err != nil {
		return Watchlist{}, classify(err)
	}
	if len(lists) == 0 {
		return Watchlist{}, ErrNotFound
	}
	return lists[0], nil
}

func (s *Store) CreateWatchlist(ctx context.Context, owner, name string, issuers []string) (Watchlist, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return Watchlist{}, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var id string
	if err := tx.QueryRow(ctx, "INSERT INTO alerts.watchlists (owner, name) VALUES ($1, $2) RETURNING id",
		owner, name).Scan(&id); err != nil {
		return Watchlist{}, classify(err)
	}
	for _, lei := range issuers {
		if _, err := tx.Exec(ctx, `INSERT INTO alerts.watchlist_issuers (watchlist_id, issuer_lei) VALUES ($1, $2)
			ON CONFLICT DO NOTHING`, id, lei); err != nil {
			return Watchlist{}, classify(err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return Watchlist{}, err
	}
	return s.GetWatchlist(ctx, owner, id)
}

func (s *Store) DeleteWatchlist(ctx context.Context, owner, id string) error {
	return affected(s.pool.Exec(ctx, "DELETE FROM alerts.watchlists WHERE owner = $1 AND id = $2", owner, id))
}

func (s *Store) AddIssuer(ctx context.Context, owner, id, lei string) error {
	return affected(s.pool.Exec(ctx, `INSERT INTO alerts.watchlist_issuers (watchlist_id, issuer_lei)
		SELECT id, $3 FROM alerts.watchlists WHERE owner = $1 AND id = $2
		ON CONFLICT (watchlist_id, issuer_lei) DO UPDATE SET added_at = alerts.watchlist_issuers.added_at`,
		owner, id, lei))
}

func (s *Store) RemoveIssuer(ctx context.Context, owner, id, lei string) error {
	return affected(s.pool.Exec(ctx, `DELETE FROM alerts.watchlist_issuers i USING alerts.watchlists w
		WHERE i.watchlist_id = w.id AND w.owner = $1 AND w.id = $2 AND i.issuer_lei = $3`, owner, id, lei))
}

// Webhook endpoints ----------------------------------------------------------------------------------------

func (s *Store) ListEndpoints(ctx context.Context, owner string) ([]Endpoint, error) {
	rows, err := s.pool.Query(ctx, `SELECT id, url, description, enabled, created_at FROM alerts.webhook_endpoints
		WHERE owner = $1 ORDER BY created_at`, owner)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Endpoint, error) {
		var e Endpoint
		err := r.Scan(&e.ID, &e.URL, &e.Description, &e.Enabled, &e.CreatedAt)
		return e, err
	})
}

func (s *Store) CreateEndpoint(ctx context.Context, owner, url, description, secret string) (Endpoint, error) {
	e := Endpoint{URL: url, Description: description, Enabled: true, Secret: secret}
	err := s.pool.QueryRow(ctx, `INSERT INTO alerts.webhook_endpoints (owner, url, description, secret)
		VALUES ($1, $2, $3, $4) RETURNING id, created_at`, owner, url, description, secret).Scan(&e.ID, &e.CreatedAt)
	return e, classify(err)
}

func (s *Store) DeleteEndpoint(ctx context.Context, owner, id string) error {
	return affected(s.pool.Exec(ctx, "DELETE FROM alerts.webhook_endpoints WHERE owner = $1 AND id = $2", owner, id))
}

// EndpointSecret returns the URL and signing secret of an enabled endpoint (delivery path, no owner scope).
func (s *Store) EndpointSecret(ctx context.Context, id string) (url, secret string, err error) {
	err = s.pool.QueryRow(ctx, "SELECT url, secret FROM alerts.webhook_endpoints WHERE id = $1 AND enabled",
		id).Scan(&url, &secret)
	return url, secret, classify(err)
}

// Rules ----------------------------------------------------------------------------------------------------

const ruleColumns = `r.id, r.owner, r.name, r.watchlist_id, r.natures, r.roles, r.countries, r.open_market_only,
	r.min_amount_eur, r.email, r.webhook_endpoint_id, r.enabled, r.created_at`

func scanRule(r pgx.CollectableRow) (Rule, error) {
	var x Rule
	err := r.Scan(&x.ID, &x.Owner, &x.Name, &x.WatchlistID, &x.Natures, &x.Roles, &x.Countries, &x.OpenMarketOnly,
		&x.MinAmountEUR, &x.Email, &x.WebhookEndpointID, &x.Enabled, &x.CreatedAt)
	return x, err
}

func (s *Store) ListRules(ctx context.Context, owner string) ([]Rule, error) {
	rows, err := s.pool.Query(ctx, "SELECT "+ruleColumns+" FROM alerts.alert_rules r WHERE r.owner = $1 ORDER BY r.created_at", owner)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, scanRule)
}

// CreateRule inserts a rule; referenced watchlists and endpoints must belong to the same owner.
func (s *Store) CreateRule(ctx context.Context, owner string, r Rule) (Rule, error) {
	rows, err := s.pool.Query(ctx, `
		WITH ok AS (
			SELECT ($3::uuid IS NULL OR EXISTS (SELECT 1 FROM alerts.watchlists WHERE id = $3 AND owner = $1))
			   AND ($10::uuid IS NULL OR EXISTS (SELECT 1 FROM alerts.webhook_endpoints WHERE id = $10 AND owner = $1)) AS ok
		)
		INSERT INTO alerts.alert_rules AS r (owner, name, watchlist_id, natures, roles, countries, open_market_only,
			min_amount_eur, email, webhook_endpoint_id)
		SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9, $10 FROM ok WHERE ok
		RETURNING `+ruleColumns,
		owner, r.Name, r.WatchlistID, nonNil(r.Natures), nonNil(r.Roles), nonNil(r.Countries), r.OpenMarketOnly,
		r.MinAmountEUR, r.Email, r.WebhookEndpointID)
	if err != nil {
		return Rule{}, classify(err)
	}
	created, err := pgx.CollectRows(rows, scanRule)
	if err != nil {
		return Rule{}, classify(err)
	}
	if len(created) == 0 {
		return Rule{}, fmt.Errorf("%w: watchlist or webhook endpoint not found", ErrInvalid)
	}
	return created[0], nil
}

func (s *Store) DeleteRule(ctx context.Context, owner, id string) error {
	return affected(s.pool.Exec(ctx, "DELETE FROM alerts.alert_rules WHERE owner = $1 AND id = $2", owner, id))
}

// MatchingRules returns the enabled rules that watch this issuer (or every issuer), with their webhook URL.
// Criteria other than the issuer are evaluated by package rules.
func (s *Store) MatchingRules(ctx context.Context, issuerLEI string) ([]Rule, error) {
	rows, err := s.pool.Query(ctx, "SELECT "+ruleColumns+`, coalesce(e.url, '')
		FROM alerts.alert_rules r LEFT JOIN alerts.webhook_endpoints e ON e.id = r.webhook_endpoint_id AND e.enabled
		WHERE r.enabled AND (r.watchlist_id IS NULL OR EXISTS (
			SELECT 1 FROM alerts.watchlist_issuers i WHERE i.watchlist_id = r.watchlist_id AND i.issuer_lei = $1))`,
		issuerLEI)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (Rule, error) {
		var x Rule
		err := r.Scan(&x.ID, &x.Owner, &x.Name, &x.WatchlistID, &x.Natures, &x.Roles, &x.Countries, &x.OpenMarketOnly,
			&x.MinAmountEUR, &x.Email, &x.WebhookEndpointID, &x.Enabled, &x.CreatedAt, &x.WebhookURL)
		return x, err
	})
}

// Deliveries -----------------------------------------------------------------------------------------------

// EnsureDelivery records a notification as pending unless it exists, and returns its current state.
func (s *Store) EnsureDelivery(ctx context.Context, d Delivery) (Delivery, error) {
	if _, err := s.pool.Exec(ctx, `INSERT INTO alerts.deliveries (id, owner, rule_id, filing_id, channel, target)
		VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (id) DO NOTHING`,
		d.ID, d.Owner, d.RuleID, d.FilingID, d.Channel, d.Target); err != nil {
		return Delivery{}, classify(err)
	}
	return s.GetDelivery(ctx, d.ID)
}

const deliveryColumns = `id, owner, rule_id, filing_id, channel, target, status, attempts, response_status, last_error,
	created_at, updated_at, delivered_at`

func scanDelivery(r pgx.CollectableRow) (Delivery, error) {
	var d Delivery
	err := r.Scan(&d.ID, &d.Owner, &d.RuleID, &d.FilingID, &d.Channel, &d.Target, &d.Status, &d.Attempts,
		&d.ResponseStatus, &d.LastError, &d.CreatedAt, &d.UpdatedAt, &d.DeliveredAt)
	return d, err
}

func (s *Store) GetDelivery(ctx context.Context, id string) (Delivery, error) {
	rows, err := s.pool.Query(ctx, "SELECT "+deliveryColumns+" FROM alerts.deliveries WHERE id = $1", id)
	if err != nil {
		return Delivery{}, classify(err)
	}
	d, err := pgx.CollectExactlyOneRow(rows, scanDelivery)
	return d, classify(err)
}

// RecordAttempt stores the outcome of one delivery attempt.
func (s *Store) RecordAttempt(ctx context.Context, id, status string, responseStatus *int, lastError *string) error {
	return affected(s.pool.Exec(ctx, `UPDATE alerts.deliveries SET status = $2, attempts = attempts + 1,
		response_status = $3, last_error = $4, updated_at = now(),
		delivered_at = CASE WHEN $2 = 'delivered' THEN now() ELSE delivered_at END WHERE id = $1`,
		id, status, responseStatus, lastError))
}

func (s *Store) ListDeliveries(ctx context.Context, owner string, limit int) ([]Delivery, error) {
	rows, err := s.pool.Query(ctx, "SELECT "+deliveryColumns+` FROM alerts.deliveries WHERE owner = $1
		ORDER BY created_at DESC LIMIT $2`, owner, limit)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, scanDelivery)
}

func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}
