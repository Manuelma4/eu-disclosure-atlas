package main

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/webhook"
)

func TestSinkRejectsOversizedSignedPrefix(t *testing.T) {
	secret := webhook.NewSecret()
	sink := sinkHandler(slog.New(slog.NewTextHandler(io.Discard, nil)), []string{secret})
	atLimit := `{}` + strings.Repeat(" ", (1<<20)-2)
	now := time.Now()
	signature, err := webhook.Sign(secret, "test", now, []byte(atLimit))
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		body string
		code int
	}{
		{atLimit + "x", http.StatusRequestEntityTooLarge},
		{atLimit, http.StatusNoContent},
	} {
		req := httptest.NewRequest(http.MethodPost, "/", strings.NewReader(tc.body))
		req.Header.Set("webhook-id", "test")
		req.Header.Set("webhook-timestamp", strconv.FormatInt(now.Unix(), 10))
		req.Header.Set("webhook-signature", signature)
		res := httptest.NewRecorder()
		sink.ServeHTTP(res, req)
		if res.Code != tc.code {
			t.Errorf("body length %d: status %d, want %d", len(tc.body), res.Code, tc.code)
		}
	}
	res := httptest.NewRecorder()
	sink.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/deliveries", nil))
	var deliveries []received
	if err := json.Unmarshal(res.Body.Bytes(), &deliveries); err != nil {
		t.Fatal(err)
	}
	if len(deliveries) != 1 || !deliveries[0].Verified {
		t.Fatalf("delivery history has %d entries, want one verified delivery", len(deliveries))
	}
}
