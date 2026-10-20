// Package consumer reads atlas.filings.v1 into the hub. Every replica reads every partition on its own
// (no consumer group, no committed offsets): the gateway is a stateless fan-out, so replicas scale with
// open connections and a restart simply refills the replay buffer from the end of the topic.
package consumer

import (
	"context"
	"errors"
	"log/slog"
	"strconv"

	"github.com/twmb/franz-go/pkg/kgo"
	"github.com/twmb/franz-go/plugin/kotel"

	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/hub"
	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/server"
)

// Config selects the brokers and topic.
type Config struct {
	Brokers  []string
	Topic    string
	Backfill int64 // records per partition re-read on start to fill the replay buffer
	ClientID string
}

// Consumer feeds the hub from Kafka.
type Consumer struct {
	client  *kgo.Client
	cfg     Config
	hub     *hub.Hub
	metrics *server.Metrics
	log     *slog.Logger
}

// New creates the Kafka client; it does not connect until Ping or Run.
func New(cfg Config, h *hub.Hub, m *server.Metrics, log *slog.Logger) (*Consumer, error) {
	start := kgo.NewOffset().AtEnd()
	if cfg.Backfill > 0 {
		start = start.Relative(-cfg.Backfill)
	}
	tracing := kotel.NewKotel(kotel.WithTracer(kotel.NewTracer()))
	client, err := kgo.NewClient(
		kgo.SeedBrokers(cfg.Brokers...),
		kgo.ClientID(cfg.ClientID),
		kgo.ConsumeTopics(cfg.Topic),
		kgo.ConsumeResetOffset(start),
		kgo.WithHooks(tracing.Hooks()...),
	)
	if err != nil {
		return nil, err
	}
	return &Consumer{client: client, cfg: cfg, hub: h, metrics: m, log: log}, nil
}

// Ping checks that a broker answers (readiness).
func (c *Consumer) Ping(ctx context.Context) error { return c.client.Ping(ctx) }

// Run polls until ctx is cancelled.
func (c *Consumer) Run(ctx context.Context) error {
	for {
		fetches := c.client.PollFetches(ctx)
		if fetches.IsClientClosed() || ctx.Err() != nil {
			return nil
		}
		fetches.EachError(func(topic string, partition int32, err error) {
			if !errors.Is(err, context.Canceled) {
				c.log.Warn("fetch error", "topic", topic, "partition", partition, "err", err)
			}
		})
		fetches.EachPartition(func(p kgo.FetchTopicPartition) {
			for _, record := range p.Records {
				c.handle(record)
			}
			if n := len(p.Records); n > 0 {
				lag := p.HighWatermark - p.Records[n-1].Offset - 1
				c.metrics.Lag.WithLabelValues(p.Topic, strconv.Itoa(int(p.Partition))).Set(float64(max(lag, 0)))
			}
		})
	}
}

func (c *Consumer) handle(record *kgo.Record) {
	c.metrics.EventsConsumed.Inc()
	event, err := hub.Decode(record.Value)
	if err != nil {
		c.metrics.DecodeErrors.Inc()
		c.log.Warn("skipping record", "partition", record.Partition, "offset", record.Offset, "err", err)
		return
	}
	if !c.hub.Publish(event) {
		c.metrics.Duplicates.Inc()
	}
}

// Close leaves the brokers.
func (c *Consumer) Close() { c.client.Close() }
