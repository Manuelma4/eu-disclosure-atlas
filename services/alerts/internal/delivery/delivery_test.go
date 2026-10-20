package delivery

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/email"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/store"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/testutil"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/webhook"
)

const lei = "549300EXAMPLE0000155"

func filing(id string, amount float64, firstSeen time.Time) []byte {
	b, _ := json.Marshal(map[string]any{"id": id, "source": "se_fi_pdmr", "country": "SE",
		"first_seen_at": firstSeen.UTC().Format(time.RFC3339Nano), "issuer_name": "Example AB", "issuer_lei": lei,
		"role": "ceo", "nature": "buy", "direction": 1, "instrument_type": "share", "amount_eur": amount, "open_market": true})
	return b
}

type message struct {
	topic, key string
	value      []byte
}

type fakeProducer struct {
	mu   sync.Mutex
	sent []message
}

func (p *fakeProducer) Produce(_ context.Context, topic, key string, value []byte) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.sent = append(p.sent, message{topic, key, value})
	return nil
}

func (p *fakeProducer) take() []message {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := p.sent
	p.sent = nil
	return out
}

type fakeMailer struct{ sent []email.Message }

func (m *fakeMailer) Send(_ context.Context, msg email.Message) error {
	m.sent = append(m.sent, msg)
	return nil
}

func TestFilingToWebhookAndEmailWithRetriesAndDeadLetter(t *testing.T) {
	ctx := context.Background()
	s := testutil.Store(t, "delivery")
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	metrics := NewMetrics(prometheus.NewRegistry())
	topics := DefaultTopics("atlas.notifications.v1")

	// A receiver that fails twice, then verifies the signature and accepts.
	var mu sync.Mutex
	var calls int
	var received []map[string]any
	var secret string
	sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		calls++
		body, _ := io.ReadAll(r.Body)
		if calls <= 2 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		if err := webhook.Verify(secret, r.Header, body, time.Now()); err != nil {
			t.Errorf("signature: %v", err)
		}
		var payload map[string]any
		_ = json.Unmarshal(body, &payload)
		received = append(received, payload)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer sink.Close()

	list, err := s.CreateWatchlist(ctx, "alice", "Nordics", []string{lei})
	if err != nil {
		t.Fatal(err)
	}
	endpoint, err := s.CreateEndpoint(ctx, "alice", sink.URL, "", webhook.NewSecret())
	if err != nil {
		t.Fatal(err)
	}
	secret = endpoint.Secret
	min := 10_000.0
	mail := "alice@example.org"
	if _, err := s.CreateRule(ctx, "alice", store.Rule{Name: "CEO buys", WatchlistID: &list.ID, Natures: []string{"buy"},
		MinAmountEUR: &min, WebhookEndpointID: &endpoint.ID, Email: &mail}); err != nil {
		t.Fatal(err)
	}
	// Someone else's rule on every issuer with a higher threshold does not match.
	big := 1e9
	if _, err := s.CreateRule(ctx, "bob", store.Rule{Name: "Whales", MinAmountEUR: &big, Email: &mail}); err != nil {
		t.Fatal(err)
	}

	producer := &fakeProducer{}
	now := time.Now()
	evaluator := &Evaluator{Store: s, Producer: producer, Topics: topics, MaxAge: 24 * time.Hour, Metrics: metrics, Log: log}
	if err := evaluator.Handle(ctx, filing("se_fi_pdmr:old", 50_000, now.Add(-48*time.Hour))); err != nil {
		t.Fatal(err)
	}
	if err := evaluator.Handle(ctx, filing("se_fi_pdmr:small", 500, now)); err != nil {
		t.Fatal(err)
	}
	if len(producer.take()) != 0 {
		t.Fatal("old or small filings produced notifications")
	}
	if err := evaluator.Handle(ctx, filing("se_fi_pdmr:1", 50_000, now)); err != nil {
		t.Fatal(err)
	}
	notifications := producer.take()
	if len(notifications) != 2 || notifications[0].topic != topics.Notifications || notifications[0].key != "alice" {
		t.Fatalf("notifications = %+v", notifications)
	}

	mailer := &fakeMailer{}
	var slept []time.Duration
	deliverer := &Deliverer{Store: s, Producer: producer, Webhooks: webhook.NewSender(true), Mailer: mailer,
		WebsiteURL: "http://localhost:3000", Metrics: metrics, Log: log,
		sleepFn: func(_ context.Context, d time.Duration) error { slept = append(slept, d); return nil }}
	tiers := Tiers(topics, 5*time.Minute, time.Hour)

	// Main tier: email succeeds, webhook fails (503) and moves to retry.5m.
	for _, n := range notifications {
		if err := deliverer.Handle(ctx, tiers[0], n.value); err != nil {
			t.Fatal(err)
		}
	}
	if len(mailer.sent) != 1 || mailer.sent[0].To != mail {
		t.Fatalf("emails = %+v", mailer.sent)
	}
	retry := producer.take()
	if len(retry) != 1 || retry[0].topic != topics.Retry5m {
		t.Fatalf("retry = %+v", retry)
	}
	// The retry tier waits for its delay, fails again (503) and moves to retry.1h, which succeeds.
	if err := deliverer.WaitUntilDue(ctx, tiers[1], time.Now()); err != nil || len(slept) != 1 || slept[0] < 4*time.Minute {
		t.Fatalf("WaitUntilDue slept %v (%v)", slept, err)
	}
	if err := deliverer.Handle(ctx, tiers[1], retry[0].value); err != nil {
		t.Fatal(err)
	}
	retry = producer.take()
	if len(retry) != 1 || retry[0].topic != topics.Retry1h {
		t.Fatalf("second retry = %+v", retry)
	}
	if err := deliverer.Handle(ctx, tiers[2], retry[0].value); err != nil {
		t.Fatal(err)
	}
	if len(received) != 1 || received[0]["type"] != "filing.matched" {
		t.Fatalf("received = %v", received)
	}

	deliveries, err := s.ListDeliveries(ctx, "alice", 10)
	if err != nil || len(deliveries) != 2 {
		t.Fatalf("deliveries = %+v, %v", deliveries, err)
	}
	for _, d := range deliveries {
		if d.Status != "delivered" {
			t.Fatalf("delivery %s: %s", d.Channel, d.Status)
		}
		if d.Channel == "webhook" && (d.Attempts != 3 || *d.ResponseStatus != 204) {
			t.Fatalf("webhook delivery = %+v", d)
		}
	}

	// Replaying the filing (at-least-once upstream) and the notification never notifies twice.
	if err := evaluator.Handle(ctx, filing("se_fi_pdmr:1", 50_000, now)); err != nil {
		t.Fatal(err)
	}
	if replay := producer.take(); len(replay) != 0 {
		t.Fatalf("replay produced %d notifications", len(replay))
	}
	if err := deliverer.Handle(ctx, tiers[0], notifications[0].value); err != nil || len(mailer.sent) != 1 || calls != 3 {
		t.Fatalf("redelivery sent again: %v, %d emails, %d calls", err, len(mailer.sent), calls)
	}
}

func TestLastTierSendsToDeadLetterTopic(t *testing.T) {
	ctx := context.Background()
	s := testutil.Store(t, "dlq")
	list, _ := s.CreateWatchlist(ctx, "alice", "List", []string{lei})
	mail := "alice@example.org"
	rule, err := s.CreateRule(ctx, "alice", store.Rule{Name: "Any", WatchlistID: &list.ID, Email: &mail})
	if err != nil {
		t.Fatal(err)
	}
	producer := &fakeProducer{}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	metrics := NewMetrics(prometheus.NewRegistry())
	topics := DefaultTopics("atlas.notifications.v1")
	evaluator := &Evaluator{Store: s, Producer: producer, Topics: topics, Metrics: metrics, Log: log}
	if err := evaluator.Handle(ctx, filing("se_fi_pdmr:2", 1, time.Now())); err != nil {
		t.Fatal(err)
	}
	n := producer.take()[0]
	disabled, _ := email.New(email.Config{})
	deliverer := &Deliverer{Store: s, Producer: producer, Mailer: disabled, Metrics: metrics, Log: log}
	if err := deliverer.Handle(ctx, Tiers(topics, 0, 0)[2], n.value); err != nil {
		t.Fatal(err)
	}
	dead := producer.take()
	if len(dead) != 1 || dead[0].topic != topics.DLQ {
		t.Fatalf("dead letters = %+v", dead)
	}
	var payload Notification
	_ = json.Unmarshal(dead[0].value, &payload)
	if payload.Attempt != 1 || payload.LastError == "" {
		t.Fatalf("dead letter = %+v", payload)
	}
	d, err := s.GetDelivery(ctx, NotificationID(rule.ID, "se_fi_pdmr:2", "email"))
	if err != nil || d.Status != "failed" || d.LastError == nil {
		t.Fatalf("delivery = %+v, %v", d, err)
	}
	if !errors.Is(disabled.Send(ctx, email.Message{}), email.ErrDisabled) {
		t.Fatal("expected disabled mailer")
	}
}
