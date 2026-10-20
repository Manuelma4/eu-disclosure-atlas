// Command realtime-gateway streams new filings from Kafka (atlas.filings.v1) to browsers as server-sent
// events on /v1/stream, with the same event format and resume semantics as the Python API.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/consumer"
	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/hub"
	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/server"
	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/telemetry"
)

const service = "realtime-gateway"

var version = "dev" // set with -ldflags "-X main.version=..."

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return v
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

	metrics := server.NewMetrics()
	h := hub.New(envInt("ATLAS_GATEWAY_BUFFER", 1000), envInt("ATLAS_GATEWAY_CLIENT_BUFFER", 256))
	srv := server.New(server.Config{
		CORSOrigins: split(env("ATLAS_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")),
	}, h, metrics, log)

	kafka, err := consumer.New(consumer.Config{
		Brokers:  split(env("ATLAS_KAFKA_BOOTSTRAP", env("KAFKA_BROKERS", "localhost:19092"))),
		Topic:    env("ATLAS_FILINGS_TOPIC", "atlas.filings.v1"),
		Backfill: int64(envInt("ATLAS_GATEWAY_BACKFILL", 200)),
		ClientID: service,
	}, h, metrics, log)
	if err != nil {
		return err
	}

	// Probes, metrics and long-lived streams are not request spans; filings are traced through Kafka spans.
	handler := otelhttp.NewHandler(srv.Handler(), service, otelhttp.WithFilter(func(r *http.Request) bool {
		switch r.URL.Path {
		case "/healthz", "/readyz", "/metrics", "/v1/stream":
			return false
		}
		return true
	}))
	httpServer := &http.Server{
		Addr:              env("ATLAS_GATEWAY_ADDR", ":8080"),
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	consumed := make(chan error, 1)
	go func() { consumed <- kafka.Run(ctx) }()
	go func() { // ready once a broker answers
		for ctx.Err() == nil {
			pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
			err := kafka.Ping(pingCtx)
			cancel()
			if err == nil {
				srv.SetReady(true)
				log.Info("kafka reachable")
				return
			}
			log.Warn("kafka not reachable yet", "err", err)
			select {
			case <-ctx.Done():
			case <-time.After(2 * time.Second):
			}
		}
	}()

	served := make(chan error, 1)
	go func() {
		log.Info("listening", "addr", httpServer.Addr, "version", version)
		served <- httpServer.ListenAndServe()
	}()

	select {
	case <-ctx.Done():
	case err = <-served:
		if !errors.Is(err, http.ErrServerClosed) {
			return err
		}
	case err = <-consumed:
		log.Error("consumer stopped", "err", err)
	}

	log.Info("shutting down")
	srv.SetReady(false)
	h.Close() // ends every open stream; browsers reconnect to another replica with Last-Event-ID
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		log.Warn("http shutdown", "err", err)
	}
	kafka.Close()
	return shutdownTracing(shutdownCtx)
}
