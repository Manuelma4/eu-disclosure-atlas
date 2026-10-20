// Package delivery turns filings into notifications and delivers them.
//
//	atlas.filings.v1 --Evaluator--> atlas.notifications.v1 --Deliverer--> webhook / email
//	                                  | failure
//	                                  v
//	                      .retry.5m --> .retry.1h --> .dlq
//
// Each notification has a deterministic id (rule × filing × channel) recorded in the deliveries table, so
// replays and redeliveries never notify twice; a delivered or failed notification is skipped.
package delivery

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/email"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/rules"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/store"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/webhook"
)

var namespace = uuid.MustParse("1f0a8d1e-5b52-4c0e-9a77-6a0f1c3e2d10")

// Notification is the payload of atlas.notifications.v1 and its retry topics (key: owner).
type Notification struct {
	ID         string          `json:"id"`
	Owner      string          `json:"owner"`
	RuleID     string          `json:"rule_id"`
	RuleName   string          `json:"rule_name"`
	Channel    string          `json:"channel"` // webhook or email
	Target     string          `json:"target"`  // URL or address (for logs; secrets are read from the store)
	EndpointID string          `json:"endpoint_id,omitempty"`
	FilingID   string          `json:"filing_id"`
	Filing     json.RawMessage `json:"filing"`
	CreatedAt  time.Time       `json:"created_at"`
	Attempt    int             `json:"attempt"`
	LastError  string          `json:"last_error,omitempty"`
}

// NotificationID is stable for a rule, filing and channel.
func NotificationID(ruleID, filingID, channel string) string {
	return uuid.NewSHA1(namespace, []byte(ruleID+"|"+filingID+"|"+channel)).String()
}

// Producer publishes records (a franz-go client in production, a fake in tests).
type Producer interface {
	Produce(ctx context.Context, topic, key string, value []byte) error
}

// Topics names the notification topics.
type Topics struct {
	Notifications, Retry5m, Retry1h, DLQ string
}

// DefaultTopics derives the retry and dead-letter topics from the notification topic.
func DefaultTopics(base string) Topics {
	return Topics{Notifications: base, Retry5m: base + ".retry.5m", Retry1h: base + ".retry.1h", DLQ: base + ".dlq"}
}

// Metrics counts the pipeline's outcomes.
type Metrics struct {
	Filings       prometheus.Counter
	Notifications *prometheus.CounterVec // by channel
	Deliveries    *prometheus.CounterVec // by channel and outcome
}

func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		Filings: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "atlas_alerts_filings_evaluated_total", Help: "Filings evaluated against alert rules."}),
		Notifications: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "atlas_alerts_notifications_total", Help: "Notifications produced."}, []string{"channel"}),
		Deliveries: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "atlas_alerts_delivery_attempts_total", Help: "Delivery attempts by outcome."},
			[]string{"channel", "outcome"}),
	}
	reg.MustRegister(m.Filings, m.Notifications, m.Deliveries)
	return m
}

// Evaluator matches filings with rules.
type Evaluator struct {
	Store    *store.Store
	Producer Producer
	Topics   Topics
	MaxAge   time.Duration // ignore filings first seen longer ago (replays, backfills)
	Metrics  *Metrics
	Log      *slog.Logger
	Now      func() time.Time
}

// Handle evaluates one filing.published.v1 payload. Errors are transient (store or broker) and the caller
// retries; invalid payloads are logged and skipped.
func (e *Evaluator) Handle(ctx context.Context, value []byte) error {
	filing, err := rules.Decode(value)
	if err != nil {
		e.Log.Warn("skipping invalid filing event", "err", err)
		return nil
	}
	e.Metrics.Filings.Inc()
	if e.MaxAge > 0 && e.now().Sub(filing.FirstSeenAt) > e.MaxAge {
		return nil
	}
	lei := ""
	if filing.IssuerLEI != nil {
		lei = *filing.IssuerLEI
	}
	candidates, err := e.Store.MatchingRules(ctx, lei)
	if err != nil {
		return err
	}
	for _, rule := range candidates {
		if !rules.Match(rule, filing) {
			continue
		}
		var targets []Notification
		if rule.WebhookEndpointID != nil && rule.WebhookURL != "" {
			targets = append(targets, Notification{Channel: "webhook", Target: rule.WebhookURL, EndpointID: *rule.WebhookEndpointID})
		}
		if rule.Email != nil {
			targets = append(targets, Notification{Channel: "email", Target: *rule.Email})
		}
		for _, n := range targets {
			n.ID = NotificationID(rule.ID, filing.ID, n.Channel)
			n.Owner, n.RuleID, n.RuleName = rule.Owner, rule.ID, rule.Name
			n.FilingID, n.Filing, n.CreatedAt = filing.ID, json.RawMessage(value), e.now()
			d, err := e.Store.EnsureDelivery(ctx, store.Delivery{ID: n.ID, Owner: n.Owner, RuleID: n.RuleID,
				FilingID: n.FilingID, Channel: n.Channel, Target: n.Target})
			if err != nil {
				return err
			}
			if d.Status == "delivered" || d.Status == "failed" {
				continue // a replay of a filing already handled
			}
			payload, _ := json.Marshal(n)
			if err := e.Producer.Produce(ctx, e.Topics.Notifications, n.Owner, payload); err != nil {
				return err
			}
			e.Metrics.Notifications.WithLabelValues(n.Channel).Inc()
		}
	}
	return nil
}

func (e *Evaluator) now() time.Time {
	if e.Now != nil {
		return e.Now()
	}
	return time.Now()
}

// Tier is one stage of delivery: the topic it reads, how long records wait there, and where failures go.
type Tier struct {
	Topic string
	Delay time.Duration
	Next  string // the next retry topic, or the dead-letter topic for the last tier
	Last  bool   // failures here are final (sent to Next = DLQ)
}

// Tiers returns main → retry.5m → retry.1h → dlq with the given delays.
func Tiers(t Topics, retry5m, retry1h time.Duration) []Tier {
	return []Tier{
		{Topic: t.Notifications, Next: t.Retry5m},
		{Topic: t.Retry5m, Delay: retry5m, Next: t.Retry1h},
		{Topic: t.Retry1h, Delay: retry1h, Next: t.DLQ, Last: true},
	}
}

// Deliverer sends notifications.
type Deliverer struct {
	Store       *store.Store
	Producer    Producer
	Webhooks    *webhook.Sender
	Mailer      email.Mailer
	WebsiteURL  string
	Metrics     *Metrics
	Log         *slog.Logger
	sleepFn     func(ctx context.Context, d time.Duration) error
}

// WaitUntilDue blocks until a record produced at `produced` has spent the tier's delay in its topic.
func (d *Deliverer) WaitUntilDue(ctx context.Context, tier Tier, produced time.Time) error {
	wait := time.Until(produced.Add(tier.Delay))
	if wait <= 0 {
		return nil
	}
	if d.sleepFn != nil {
		return d.sleepFn(ctx, wait)
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(wait):
		return nil
	}
}

// Handle delivers one notification from a tier. Errors are transient (store or broker) and retried by the
// caller; a failed delivery itself is not an error: it moves the notification to the next tier.
func (d *Deliverer) Handle(ctx context.Context, tier Tier, value []byte) error {
	var n Notification
	if err := json.Unmarshal(value, &n); err != nil || n.ID == "" {
		d.Log.Warn("skipping invalid notification", "topic", tier.Topic, "err", err)
		return nil
	}
	current, err := d.Store.GetDelivery(ctx, n.ID)
	if errors.Is(err, store.ErrNotFound) {
		return nil // its rule was deleted
	}
	if err != nil {
		return err
	}
	if current.Status == "delivered" || current.Status == "failed" {
		return nil
	}

	status, deliveryErr := d.deliver(ctx, n)
	n.Attempt++
	if deliveryErr == nil {
		d.Metrics.Deliveries.WithLabelValues(n.Channel, "delivered").Inc()
		return d.Store.RecordAttempt(ctx, n.ID, "delivered", status, nil)
	}
	message := truncate(deliveryErr.Error(), 500)
	n.LastError = message
	outcome := "retrying"
	if tier.Last {
		outcome = "failed"
	}
	d.Metrics.Deliveries.WithLabelValues(n.Channel, outcome).Inc()
	d.Log.Info("delivery failed", "notification", n.ID, "channel", n.Channel, "attempt", n.Attempt,
		"next", tier.Next, "err", message)
	payload, _ := json.Marshal(n)
	if err := d.Producer.Produce(ctx, tier.Next, n.Owner, payload); err != nil {
		return err
	}
	return d.Store.RecordAttempt(ctx, n.ID, outcome, status, &message)
}

func (d *Deliverer) deliver(ctx context.Context, n Notification) (*int, error) {
	switch n.Channel {
	case "webhook":
		url, secret, err := d.Store.EndpointSecret(ctx, n.EndpointID)
		if err != nil {
			return nil, fmt.Errorf("endpoint unavailable: %w", err)
		}
		body, _ := json.Marshal(map[string]any{
			"type":      "filing.matched",
			"timestamp": time.Now().UTC().Format(time.RFC3339),
			"data": map[string]any{
				"notification_id": n.ID,
				"rule":            map[string]string{"id": n.RuleID, "name": n.RuleName},
				"filing":          n.Filing,
			},
		})
		result := d.Webhooks.Send(ctx, url, secret, n.ID, body)
		var status *int
		if result.Status != 0 {
			status = &result.Status
		}
		if result.OK() {
			return status, nil
		}
		return status, result.Err
	case "email":
		return nil, d.Mailer.Send(ctx, d.email(n))
	}
	return nil, fmt.Errorf("unknown channel %q", n.Channel)
}

func (d *Deliverer) email(n Notification) email.Message {
	var f rules.Filing
	_ = json.Unmarshal(n.Filing, &f)
	amount := "amount not available"
	if f.AmountEUR != nil {
		amount = fmt.Sprintf("EUR %.0f", *f.AmountEUR)
	}
	link := d.WebsiteURL + "/filings"
	if f.IssuerLEI != nil {
		link = d.WebsiteURL + "/company/" + *f.IssuerLEI
	}
	text := fmt.Sprintf("Your alert %q matched a new filing.\n\n%s: %s (%s), %s, %s.\nSource: %s, filing %s.\n\n%s\n\n"+
		"EU Disclosure Atlas. Insiders appear as pseudonymous ids. Not investment advice.\n",
		n.RuleName, f.IssuerName, f.Nature, f.Role, f.InstrumentType, amount, f.Source, f.ID, link)
	return email.Message{To: n.Target, Subject: fmt.Sprintf("Insider %s at %s", f.Nature, f.IssuerName), Text: text}
}

func truncate(s string, n int) string {
	s = strings.ToValidUTF8(s, "")
	if len(s) <= n {
		return s
	}
	return s[:n]
}
