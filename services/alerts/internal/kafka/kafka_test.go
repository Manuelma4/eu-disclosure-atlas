package kafka

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kfake"
	"github.com/twmb/franz-go/pkg/kgo"
)

// A handler error is retried in place; offsets are committed only after success, so a restarted
// consumer resumes after the last handled record.
func TestConsumeRetriesThenCommits(t *testing.T) {
	cluster, err := kfake.NewCluster(kfake.NumBrokers(1), kfake.SeedTopics(1, "in"))
	if err != nil {
		t.Fatal(err)
	}
	defer cluster.Close()
	brokers := cluster.ListenAddrs()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	log := slog.New(slog.NewTextHandler(io.Discard, nil))

	consumer, err := NewGroupConsumer(brokers, "test", "g", "in", false)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var seen []string
	failures := 1
	done := make(chan struct{})
	runCtx, stop := context.WithCancel(ctx)
	go func() {
		_ = Consume(runCtx, consumer, log, func(_ context.Context, r *kgo.Record) error {
			mu.Lock()
			defer mu.Unlock()
			if string(r.Value) == "b" && failures > 0 {
				failures--
				return errors.New("transient")
			}
			seen = append(seen, string(r.Value))
			if len(seen) == 2 {
				close(done)
			}
			return nil
		})
		consumer.Close()
	}()
	// Wait for the group to join (a new group starts at the end of the topic), then produce.
	time.Sleep(500 * time.Millisecond)
	producer, err := NewProducer(brokers, "test")
	if err != nil {
		t.Fatal(err)
	}
	defer producer.Client.Close()
	for _, v := range []string{"a", "b"} {
		if err := producer.Produce(ctx, "in", "k", []byte(v)); err != nil {
			t.Fatal(err)
		}
	}
	select {
	case <-done:
	case <-ctx.Done():
		t.Fatalf("seen %v", seen)
	}
	time.Sleep(200 * time.Millisecond) // let the commit land
	stop()

	if err := producer.Produce(ctx, "in", "k", []byte("c")); err != nil {
		t.Fatal(err)
	}
	again, err := NewGroupConsumer(brokers, "test", "g", "in", false)
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()
	fetches := again.PollRecords(ctx, 10)
	if records := fetches.Records(); len(records) != 1 || string(records[0].Value) != "c" {
		t.Fatalf("resumed at %v", records)
	}
	if seen[0] != "a" || seen[1] != "b" {
		t.Fatalf("order %v", seen)
	}
}
