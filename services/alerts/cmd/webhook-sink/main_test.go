package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"testing/iotest"
	"time"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/webhook"
)

func TestSinkRejectsUnreadableBodiesBeforeRecording(t *testing.T) {
	secret := webhook.NewSecret()
	var logs bytes.Buffer
	sink := sinkHandler(slog.New(slog.NewTextHandler(&logs, nil)), []string{secret})
	atLimit := `{}` + strings.Repeat(" ", (1<<20)-2)
	post := func(id string, body io.Reader, signedBody string, knownLength bool) *httptest.ResponseRecorder {
		t.Helper()
		now := time.Now()
		signature, err := webhook.Sign(secret, id, now, []byte(signedBody))
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest(http.MethodPost, "/", body)
		if (req.ContentLength >= 0) != knownLength {
			t.Fatalf("unexpected Content-Length for %s: %d", id, req.ContentLength)
		}
		req.Header.Set("webhook-id", id)
		req.Header.Set("webhook-timestamp", strconv.FormatInt(now.Unix(), 10))
		req.Header.Set("webhook-signature", signature)
		res := httptest.NewRecorder()
		sink.ServeHTTP(res, req)
		return res
	}
	history := func() string {
		t.Helper()
		res := httptest.NewRecorder()
		sink.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/deliveries", nil))
		if res.Code != http.StatusOK {
			t.Fatalf("delivery history status = %d", res.Code)
		}
		return res.Body.String()
	}

	if res := post("control", strings.NewReader(atLimit), atLimit, true); res.Code != http.StatusNoContent {
		t.Fatalf("body at the limit: status %d, want %d", res.Code, http.StatusNoContent)
	}
	before := history()
	var deliveries []received
	if err := json.Unmarshal([]byte(before), &deliveries); err != nil {
		t.Fatal(err)
	}
	if len(deliveries) != 1 || deliveries[0].ID != "control" || !deliveries[0].Verified {
		t.Fatalf("delivery history has %d entries, want one verified control", len(deliveries))
	}
	originalLogs := logs.String()
	for _, tc := range []struct {
		name        string
		body        io.Reader
		signedBody  string
		knownLength bool
		code        int
		message     string
	}{
		{"oversized-known-length", strings.NewReader(atLimit + "x"), atLimit, true,
			http.StatusRequestEntityTooLarge, "request body exceeds 1 MiB"},
		{"oversized-stream", io.MultiReader(strings.NewReader(atLimit), strings.NewReader("x")), atLimit, false,
			http.StatusRequestEntityTooLarge, "request body exceeds 1 MiB"},
		{"body-read-error", io.MultiReader(strings.NewReader("{}"), iotest.ErrReader(errors.New("synthetic read failure"))), "{}", false,
			http.StatusBadRequest, "read error"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			res := post(tc.name, tc.body, tc.signedBody, tc.knownLength)
			if res.Code != tc.code || strings.TrimSpace(res.Body.String()) != tc.message {
				t.Fatalf("response = %d %q, want %d %q", res.Code, res.Body.String(), tc.code, tc.message)
			}
			if history() != before || logs.String() != originalLogs {
				t.Fatal("rejected request changed the delivery history or log")
			}
		})
	}
}
