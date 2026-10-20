package consumer

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kfake"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/hub"
	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/server"
)

func TestConsumerBackfillsFromTheEndAndFollows(t *testing.T) {
	cluster, err := kfake.NewCluster(kfake.NumBrokers(1), kfake.SeedTopics(1, "atlas.filings.v1"))
	if err != nil {
		t.Fatal(err)
	}
	defer cluster.Close()
	producer, err := kgo.NewClient(kgo.SeedBrokers(cluster.ListenAddrs()...))
	if err != nil {
		t.Fatal(err)
	}
	defer producer.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	produce := func(values ...string) {
		for _, v := range values {
			if err := producer.ProduceSync(ctx, &kgo.Record{Topic: "atlas.filings.v1", Value: []byte(v)}).FirstErr(); err != nil {
				t.Fatal(err)
			}
		}
	}
	produce(`{"id":"old","first_seen_at":"2026-10-02T22:00:00Z"}`,
		`{"id":"recent","first_seen_at":"2026-10-02T22:01:00Z"}`)

	h := hub.New(10, 10)
	metrics := server.NewMetrics()
	c, err := New(Config{Brokers: cluster.ListenAddrs(), Topic: "atlas.filings.v1", Backfill: 1, ClientID: "test"},
		h, metrics, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if err := c.Ping(ctx); err != nil {
		t.Fatal(err)
	}
	go func() { _ = c.Run(ctx) }()

	waitLen(t, h, 1) // backfill of one record: "recent" only
	produce(`not json`, `{"id":"new","first_seen_at":"2026-10-02T22:02:00Z"}`,
		`{"id":"new","first_seen_at":"2026-10-02T22:02:00Z"}`) // duplicate (at-least-once upstream)
	waitLen(t, h, 2)
	_, events := h.Subscribe(nil)
	if events[0].Record != "recent" || events[1].Record != "new" {
		t.Fatalf("events = %v", events)
	}
	time.Sleep(100 * time.Millisecond)
	if h.Len() != 2 {
		t.Fatalf("duplicate or invalid record buffered: %d", h.Len())
	}
}

func waitLen(t *testing.T, h *hub.Hub, n int) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for h.Len() < n {
		if time.Now().After(deadline) {
			t.Fatalf("hub has %d events, want %d", h.Len(), n)
		}
		time.Sleep(10 * time.Millisecond)
	}
}
