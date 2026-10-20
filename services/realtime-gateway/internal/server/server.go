// Package server serves the live stream (/v1/stream, server-sent events), health probes and metrics.
package server

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"slices"
	"strings"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/hub"
)

// Metrics are the gateway's Prometheus series.
type Metrics struct {
	Clients        prometheus.Gauge
	EventsConsumed prometheus.Counter
	EventsSent     prometheus.Counter
	Duplicates     prometheus.Counter
	DecodeErrors   prometheus.Counter
	Lag            *prometheus.GaugeVec
	Registry       *prometheus.Registry
}

// NewMetrics registers the gateway metrics (plus Go runtime and process metrics) on a fresh registry.
func NewMetrics() *Metrics {
	reg := prometheus.NewRegistry()
	m := &Metrics{
		Clients: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "atlas_gateway_clients", Help: "Connected SSE clients."}),
		EventsConsumed: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "atlas_gateway_events_consumed_total", Help: "Filing events read from Kafka."}),
		EventsSent: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "atlas_gateway_events_sent_total", Help: "Filing events written to SSE clients."}),
		Duplicates: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "atlas_gateway_events_duplicate_total", Help: "Events dropped as duplicates (same record id)."}),
		DecodeErrors: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "atlas_gateway_decode_errors_total", Help: "Kafka records that are not valid filing events."}),
		Lag: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "atlas_gateway_consumer_lag", Help: "Records behind the high watermark, per partition."},
			[]string{"topic", "partition"}),
		Registry: reg,
	}
	reg.MustRegister(m.Clients, m.EventsConsumed, m.EventsSent, m.Duplicates, m.DecodeErrors, m.Lag,
		prometheus.NewGoCollector(), prometheus.NewProcessCollector(prometheus.ProcessCollectorOpts{}))
	return m
}

// Config holds the HTTP behaviour.
type Config struct {
	CORSOrigins []string
	Heartbeat   time.Duration
	Retry       time.Duration
}

// Server is the HTTP side of the gateway.
type Server struct {
	cfg     Config
	hub     *hub.Hub
	metrics *Metrics
	log     *slog.Logger
	ready   atomic.Bool
}

// New returns a server; it reports ready only after SetReady(true).
func New(cfg Config, h *hub.Hub, m *Metrics, log *slog.Logger) *Server {
	if cfg.Heartbeat == 0 {
		cfg.Heartbeat = 15 * time.Second
	}
	if cfg.Retry == 0 {
		cfg.Retry = 5 * time.Second
	}
	return &Server{cfg: cfg, hub: h, metrics: m, log: log}
}

// SetReady flips the readiness probe (true once Kafka is reachable, false while shutting down).
func (s *Server) SetReady(ready bool) { s.ready.Store(ready) }

// Handler routes every endpoint.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/stream", s.stream)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { writeText(w, http.StatusOK, "ok") })
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		if s.ready.Load() {
			writeText(w, http.StatusOK, "ready")
			return
		}
		writeText(w, http.StatusServiceUnavailable, "not ready")
	})
	mux.Handle("GET /metrics", promhttp.HandlerFor(s.metrics.Registry, promhttp.HandlerOpts{}))
	return s.cors(mux)
}

func writeText(w http.ResponseWriter, status int, body string) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(body + "\n"))
}

// problem writes an RFC 9457 problem document, like the Python API.
func problem(w http.ResponseWriter, r *http.Request, status int, detail string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"type": "about:blank", "title": http.StatusText(status), "status": status, "detail": detail,
		"instance": r.URL.Path,
	})
}

func (s *Server) cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin != "" && (slices.Contains(s.cfg.CORSOrigins, origin) || slices.Contains(s.cfg.CORSOrigins, "*")) {
			h := w.Header()
			h.Set("Access-Control-Allow-Origin", origin)
			h.Add("Vary", "Origin")
			if r.Method == http.MethodOptions {
				h.Set("Access-Control-Allow-Methods", "GET, OPTIONS")
				h.Set("Access-Control-Allow-Headers", "Last-Event-ID, Cache-Control")
				h.Set("Access-Control-Max-Age", "600")
				w.WriteHeader(http.StatusNoContent)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

// stream serves `filing` events. Resume: the Last-Event-ID header, else ?since=; without either the client
// receives only events newer than the moment it connected (the Python API's behaviour).
func (s *Server) stream(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		problem(w, r, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	start := r.Header.Get("Last-Event-ID")
	if start == "" {
		start = r.URL.Query().Get("since")
	}
	after := hub.Position{Seen: time.Now().UTC()}
	if start != "" {
		pos, err := hub.ParsePosition(start)
		if err != nil {
			problem(w, r, http.StatusBadRequest, "Invalid stream position")
			return
		}
		after = pos
	}

	sub, backlog := s.hub.Subscribe(&after)
	defer s.hub.Unsubscribe(sub)
	s.metrics.Clients.Inc()
	defer s.metrics.Clients.Dec()

	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	if _, err := fmt.Fprintf(w, "retry: %d\n\n", s.cfg.Retry.Milliseconds()); err != nil {
		return
	}
	for _, e := range backlog {
		if !s.send(w, e) {
			return
		}
	}
	flusher.Flush()

	heartbeat := time.NewTicker(s.cfg.Heartbeat)
	defer heartbeat.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case e, open := <-sub.C:
			if !open { // shutting down, or this client fell too far behind: it reconnects and resumes
				return
			}
			if !s.send(w, e) {
				return
			}
			flusher.Flush()
		case <-heartbeat.C:
			if _, err := w.Write([]byte(": ping\n\n")); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

func (s *Server) send(w http.ResponseWriter, e hub.Event) bool {
	var b strings.Builder
	b.Grow(len(e.Data) + 96)
	b.WriteString("id: ")
	b.WriteString(e.ID())
	b.WriteString("\nevent: filing\ndata: ")
	b.Write(e.Data)
	b.WriteString("\n\n")
	if _, err := w.Write([]byte(b.String())); err != nil {
		return false
	}
	s.metrics.EventsSent.Inc()
	return true
}
