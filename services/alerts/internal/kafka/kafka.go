// Package kafka wires franz-go clients: an idempotent producer and at-least-once group consumers that
// commit offsets only after a record has been handled.
package kafka

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	"github.com/twmb/franz-go/plugin/kotel"
)

func tracing() kgo.Opt {
	return kgo.WithHooks(kotel.NewKotel(kotel.WithTracer(kotel.NewTracer())).Hooks()...)
}

// Producer is an idempotent producer (franz-go's default) with acks from all in-sync replicas.
type Producer struct{ Client *kgo.Client }

func NewProducer(brokers []string, clientID string) (*Producer, error) {
	client, err := kgo.NewClient(kgo.SeedBrokers(brokers...), kgo.ClientID(clientID), kgo.RequiredAcks(kgo.AllISRAcks()),
		kgo.ProducerBatchCompression(kgo.ZstdCompression()), tracing())
	if err != nil {
		return nil, err
	}
	return &Producer{Client: client}, nil
}

// Produce writes one record and waits for the broker's acknowledgement.
func (p *Producer) Produce(ctx context.Context, topic, key string, value []byte) error {
	return p.Client.ProduceSync(ctx, &kgo.Record{Topic: topic, Key: []byte(key), Value: value}).FirstErr()
}

// NewGroupConsumer joins group on topic. A new group starts at the end of the topic: alerts are about new
// filings, not history.
func NewGroupConsumer(brokers []string, clientID, group, topic string) (*kgo.Client, error) {
	return kgo.NewClient(
		kgo.SeedBrokers(brokers...),
		kgo.ClientID(clientID),
		kgo.ConsumerGroup(group),
		kgo.ConsumeTopics(topic),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtEnd()),
		kgo.DisableAutoCommit(),
		tracing(),
	)
}

// Consume polls until ctx ends. Each record is handled until it succeeds (transient errors back off
// exponentially up to 30 s), then the batch's offsets are committed: at-least-once processing. Rebalances
// are not blocked (delay tiers may wait for an hour); a record redelivered after one is deduplicated.
func Consume(ctx context.Context, client *kgo.Client, log *slog.Logger, handle func(context.Context, *kgo.Record) error) error {
	for {
		fetches := client.PollRecords(ctx, 100)
		if fetches.IsClientClosed() || ctx.Err() != nil {
			return nil
		}
		fetches.EachError(func(topic string, partition int32, err error) {
			if !errors.Is(err, context.Canceled) {
				log.Warn("fetch error", "topic", topic, "partition", partition, "err", err)
			}
		})
		records := fetches.Records()
		for _, record := range records {
			backoff := 500 * time.Millisecond
			for {
				err := handle(ctx, record)
				if err == nil {
					break
				}
				if ctx.Err() != nil {
					return nil
				}
				log.Warn("handler failed, retrying", "topic", record.Topic, "offset", record.Offset, "err", err)
				select {
				case <-ctx.Done():
				case <-time.After(backoff):
				}
				backoff = min(backoff*2, 30*time.Second)
			}
		}
		if len(records) > 0 {
			if err := client.CommitRecords(ctx, records...); err != nil && ctx.Err() == nil {
				log.Warn("commit failed; records may be redelivered", "err", err)
			}
		}
	}
}
