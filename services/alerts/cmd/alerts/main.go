// Command alerts runs the alerts service: the REST API for watchlists and rules, the rule evaluator on
// atlas.filings.v1 and the delivery workers on atlas.notifications.v1 and its retry topics. ATLAS_ALERTS_ROLES
// selects which parts run (all by default), so each can be scaled on its own signal in Kubernetes.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/twmb/franz-go/pkg/kgo"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/api"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/auth"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/delivery"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/email"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/kafka"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/store"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/telemetry"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/webhook"
)

const service = "alerts"

var version = "dev"

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func duration(key string, fallback time.Duration) time.Duration {
	if d, err := time.ParseDuration(os.Getenv(key)); err == nil {
		return d
	}
	return fallback
}

func split(v string) []string {
	var out []string
	for _, s := range strings.Split(v, ",") {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}

func main() {
	log := telemetry.Logger(service)
	if err := run(log); err != nil {
		log.Error("exit", "err", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	shutdownTracing, err := telemetry.Setup(ctx, service, version)
	if err != nil {
		return err
	}

	roles := split(env("ATLAS_ALERTS_ROLES", "api,evaluator,delivery"))
	brokers := split(env("ATLAS_KAFKA_BOOTSTRAP", env("KAFKA_BROKERS", "localhost:19092")))
	topics := delivery.DefaultTopics(env("ATLAS_NOTIFICATIONS_TOPIC", "atlas.notifications.v1"))
	allowPrivate := env("ATLAS_ALERTS_ALLOW_PRIVATE_WEBHOOKS", "") == "true" // local development only

	db, err := store.Open(ctx, env("ATLAS_DATABASE_URL", "postgres://atlas:atlas-dev-only@localhost:5432/atlas"))
	if err != nil {
		return err
	}
	defer db.Close()

	registry := prometheus.NewRegistry()
	registry.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	metrics := delivery.NewMetrics(registry)

	var producer *kafka.Producer
	if slices.Contains(roles, "evaluator") || slices.Contains(roles, "delivery") {
		if producer, err = kafka.NewProducer(brokers, service); err != nil {
			return err
		}
		defer producer.Client.Close()
	}

	var workers sync.WaitGroup
	startConsumer := func(group, topic string, fromStart bool, handle func(context.Context, *kgo.Record) error) error {
		client, err := kafka.NewGroupConsumer(brokers, service, group, topic, fromStart)
		if err != nil {
			return err
		}
		workers.Go(func() {
			defer client.Close()
			_ = kafka.Consume(ctx, client, log.With("topic", topic), handle)
		})
		return nil
	}

	if slices.Contains(roles, "evaluator") {
		evaluator := &delivery.Evaluator{Store: db, Producer: producer, Topics: topics,
			MaxAge: duration("ATLAS_ALERTS_MAX_EVENT_AGE", 24*time.Hour), Metrics: metrics, Log: log}
		if err := startConsumer("atlas-alerts-evaluator", env("ATLAS_FILINGS_TOPIC", "atlas.filings.v1"), false,
			func(ctx context.Context, r *kgo.Record) error { return evaluator.Handle(ctx, r.Value) }); err != nil {
			return err
		}
	}
	if slices.Contains(roles, "delivery") {
		mailer, err := email.New(email.Config{
			Provider: env("ATLAS_EMAIL_PROVIDER", ""), From: env("ATLAS_EMAIL_FROM", ""),
			SMTPAddr: env("ATLAS_SMTP_ADDR", ""), SMTPUser: env("ATLAS_SMTP_USER", ""),
			SMTPPassword: env("ATLAS_SMTP_PASSWORD", ""), ResendAPIKey: env("ATLAS_RESEND_API_KEY", ""),
		})
		if err != nil {
			return err
		}
		deliverer := &delivery.Deliverer{Store: db, Producer: producer, Webhooks: webhook.NewSender(allowPrivate),
			Mailer: mailer, WebsiteURL: env("ATLAS_WEBSITE_URL", "http://localhost:3000"), Metrics: metrics, Log: log}
		retries := split(env("ATLAS_ALERTS_RETRY_DELAYS", "5m,1h"))
		if len(retries) != 2 {
			return errors.New("ATLAS_ALERTS_RETRY_DELAYS needs two durations, e.g. 5m,1h")
		}
		first, err1 := time.ParseDuration(retries[0])
		second, err2 := time.ParseDuration(retries[1])
		if err := errors.Join(err1, err2); err != nil {
			return err
		}
		for _, tier := range delivery.Tiers(topics, first, second) {
			if err := startConsumer(tier.Group, tier.Topic, true, func(ctx context.Context, r *kgo.Record) error {
				if err := deliverer.WaitUntilDue(ctx, tier, r.Timestamp); err != nil {
					return err
				}
				return deliverer.Handle(ctx, tier, r.Value)
			}); err != nil {
				return err
			}
		}
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("ok\n")) })
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		pingCtx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		if err := db.Ping(pingCtx); err != nil || ctx.Err() != nil {
			http.Error(w, "not ready", http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte("ready\n"))
	})
	mux.Handle("GET /metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
	var handler http.Handler = mux
	if slices.Contains(roles, "api") {
		issuer := env("ATLAS_OIDC_ISSUER", "http://localhost:8180/realms/atlas")
		verifier := auth.NewVerifier(ctx, issuer, env("ATLAS_OIDC_AUDIENCE", "atlas-alerts"), env("ATLAS_OIDC_JWKS_URL", ""))
		a := api.New(api.Config{
			CORSOrigins:           split(env("ATLAS_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")),
			AllowInsecureWebhooks: allowPrivate,
		}, db, verifier, log)
		a.Routes(mux)
		handler = a.CORS(mux)
	}
	httpServer := &http.Server{
		Addr: env("ATLAS_ALERTS_ADDR", ":8080"),
		Handler: otelhttp.NewHandler(handler, service, otelhttp.WithFilter(func(r *http.Request) bool {
			return !slices.Contains([]string{"/healthz", "/readyz", "/metrics"}, r.URL.Path)
		})),
		ReadHeaderTimeout: 5 * time.Second,
	}
	served := make(chan error, 1)
	go func() {
		log.Info("listening", "addr", httpServer.Addr, "roles", roles, "version", version)
		served <- httpServer.ListenAndServe()
	}()

	select {
	case <-ctx.Done():
	case err := <-served:
		if !errors.Is(err, http.ErrServerClosed) {
			return err
		}
	}
	log.Info("shutting down")
	stop()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = httpServer.Shutdown(shutdownCtx)
	workers.Wait() // consumers finish their current record and close (leaving their groups)
	return shutdownTracing(shutdownCtx)
}
