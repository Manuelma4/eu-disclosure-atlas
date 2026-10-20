// Command webhook-sink is a development receiver for alert webhooks: it verifies the Standard Webhooks
// signature with WEBHOOK_SECRET (when set) and logs each delivery as JSON. GET /deliveries lists the last
// 100 received, so end-to-end tests can assert on them.
package main

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/webhook"
)

type received struct {
	ID       string          `json:"id"`
	Verified bool            `json:"verified"`
	Error    string          `json:"error,omitempty"`
	Body     json.RawMessage `json:"body"`
	At       time.Time       `json:"received_at"`
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	secret := os.Getenv("WEBHOOK_SECRET")
	var mu sync.Mutex
	var last []received

	mux := http.NewServeMux()
	mux.HandleFunc("POST /", func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		if err != nil {
			http.Error(w, "read error", http.StatusBadRequest)
			return
		}
		rec := received{ID: r.Header.Get("webhook-id"), Body: body, At: time.Now().UTC()}
		if secret != "" {
			if err := webhook.Verify(secret, r.Header, body, time.Now()); err != nil {
				rec.Error = err.Error()
			} else {
				rec.Verified = true
			}
		}
		if !json.Valid(body) {
			rec.Body = nil
		}
		log.Info("webhook", "id", rec.ID, "verified", rec.Verified, "error", rec.Error, "bytes", len(body))
		mu.Lock()
		last = append(last, rec)
		if len(last) > 100 {
			last = last[1:]
		}
		mu.Unlock()
		if secret != "" && !rec.Verified {
			http.Error(w, "invalid signature", http.StatusUnauthorized)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("GET /deliveries", func(w http.ResponseWriter, _ *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(last)
	})
	addr := os.Getenv("WEBHOOK_SINK_ADDR")
	if addr == "" {
		addr = ":8099"
	}
	log.Info("listening", "addr", addr)
	server := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		log.Error("exit", "err", err)
		os.Exit(1)
	}
}
