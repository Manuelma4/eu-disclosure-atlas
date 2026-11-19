package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
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

func TestSinkRejectsTimestampTextChangedAfterSigning(t *testing.T) {
	secret := webhook.NewSecret()
	sink := sinkHandler(slog.New(slog.NewTextHandler(io.Discard, nil)), []string{secret})
	now := time.Now()
	body := []byte(`{}`)
	signature, err := webhook.Sign(secret, "msg_1", now, body)
	if err != nil {
		t.Fatal(err)
	}
	stamp := strconv.FormatInt(now.Unix(), 10)
	for _, raw := range []string{stamp, "0" + stamp, "+" + stamp} {
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
		req.Header.Set("webhook-id", "msg_1")
		req.Header.Set("webhook-timestamp", raw)
		req.Header.Set("webhook-signature", signature)
		res := httptest.NewRecorder()
		sink.ServeHTTP(res, req)
		want := http.StatusUnauthorized
		if raw == stamp {
			want = http.StatusNoContent
		}
		if res.Code != want {
			t.Errorf("timestamp %q: status %d, want %d", raw, res.Code, want)
		}
	}
	res := httptest.NewRecorder()
	sink.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/deliveries", nil))
	var rows []received
	if err := json.Unmarshal(res.Body.Bytes(), &rows); err != nil {
		t.Fatal(err)
	}
	if len(rows) != 3 || !rows[0].Verified || rows[1].Verified || rows[2].Verified {
		t.Fatalf("unexpected verification history: %+v", rows)
	}
}

func TestSinkRejectsAnEmptySigningKeyAndAcceptsTheRotatedKey(t *testing.T) {
	valid := webhook.NewSecret()
	sink := sinkHandler(slog.New(slog.NewTextHandler(io.Discard, nil)), []string{"whsec_", valid})
	history := func() []received {
		t.Helper()
		res := httptest.NewRecorder()
		sink.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/deliveries", nil))
		var rows []received
		if err := json.Unmarshal(res.Body.Bytes(), &rows); err != nil || rows == nil {
			t.Fatalf("history = %s, error = %v", res.Body.String(), err)
		}
		return rows
	}
	if len(history()) != 0 {
		t.Fatal("unexpected startup history")
	}
	body := []byte("{}")
	now := time.Now()
	mac := hmac.New(sha256.New, nil)
	fmt.Fprintf(mac, "%s.%d.", "empty", now.Unix())
	mac.Write(body)
	forged := "v1," + base64.StdEncoding.EncodeToString(mac.Sum(nil))
	send := func(id, signature string) int {
		req := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
		req.Header.Set("webhook-id", id)
		req.Header.Set("webhook-timestamp", strconv.FormatInt(now.Unix(), 10))
		req.Header.Set("webhook-signature", signature)
		res := httptest.NewRecorder()
		sink.ServeHTTP(res, req)
		return res.Code
	}
	if status := send("empty", forged); status != http.StatusUnauthorized {
		t.Fatalf("empty key response = %d, want 401", status)
	}
	signature, err := webhook.Sign(valid, "valid", now, body)
	if err != nil {
		t.Fatal(err)
	}
	if status := send("valid", signature); status != http.StatusNoContent {
		t.Fatalf("rotated key response = %d, want 204", status)
	}
	rows := history()
	if len(rows) != 2 || rows[0].Verified || !rows[1].Verified || rows[0].ID != "empty" || rows[1].ID != "valid" {
		t.Fatalf("history = %v", rows)
	}
}

func TestSinkHistoryIsAnArrayBeforeAndAfterDelivery(t *testing.T) {
	sink := sinkHandler(slog.New(slog.NewTextHandler(io.Discard, nil)), nil)
	history := func() []received {
		t.Helper()
		res := httptest.NewRecorder()
		sink.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/deliveries", nil))
		if res.Code != http.StatusOK || res.Header().Get("Content-Type") != "application/json" {
			t.Fatalf("history response = %d %q", res.Code, res.Header().Get("Content-Type"))
		}
		var deliveries []received
		if err := json.Unmarshal(res.Body.Bytes(), &deliveries); err != nil {
			t.Fatal(err)
		}
		if deliveries == nil {
			t.Fatalf("history was JSON null, want an array")
		}
		return deliveries
	}
	if count := len(history()); count != 0 {
		t.Fatalf("startup history length = %d, want 0", count)
	}
	req := httptest.NewRequest(http.MethodPost, "/", strings.NewReader("{}"))
	req.Header.Set("webhook-id", "first")
	res := httptest.NewRecorder()
	sink.ServeHTTP(res, req)
	if res.Code != http.StatusNoContent {
		t.Fatalf("delivery status = %d, want %d", res.Code, http.StatusNoContent)
	}
	if deliveries := history(); len(deliveries) != 1 || deliveries[0].ID != "first" {
		t.Fatalf("history = %v, want one delivery with id first", deliveries)
	}
}

type blockedHistoryWriter struct {
	*httptest.ResponseRecorder
	writing chan struct{}
	release chan struct{}
}

func (w blockedHistoryWriter) Write(p []byte) (int, error) {
	close(w.writing)
	<-w.release
	return w.ResponseRecorder.Write(p)
}

func TestSlowHistoryResponseDoesNotBlockDelivery(t *testing.T) {
	sink := sinkHandler(slog.New(slog.NewTextHandler(io.Discard, nil)), nil)
	post := func(id string) int {
		req := httptest.NewRequest(http.MethodPost, "/", strings.NewReader("{}"))
		req.Header.Set("webhook-id", id)
		res := httptest.NewRecorder()
		sink.ServeHTTP(res, req)
		return res.Code
	}
	if status := post("before"); status != http.StatusNoContent {
		t.Fatalf("first delivery status = %d", status)
	}
	out := blockedHistoryWriter{httptest.NewRecorder(), make(chan struct{}), make(chan struct{})}
	historyDone := make(chan struct{})
	go func() {
		defer close(historyDone)
		sink.ServeHTTP(out, httptest.NewRequest(http.MethodGet, "/deliveries", nil))
	}()
	t.Cleanup(func() {
		if out.release != nil {
			close(out.release)
		}
		<-historyDone
	})
	select {
	case <-out.writing:
	case <-time.After(2 * time.Second):
		t.Fatal("history writer was not reached")
	}

	deliveryDone := make(chan int, 1)
	go func() { deliveryDone <- post("during") }()
	select {
	case status := <-deliveryDone:
		if status != http.StatusNoContent {
			t.Fatalf("second delivery status = %d", status)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("delivery blocked while the history response was being written")
	}
	close(out.release)
	<-historyDone
	out.release = nil
	var deliveries []received
	if err := json.Unmarshal(out.Body.Bytes(), &deliveries); err != nil {
		t.Fatal(err)
	}
	if len(deliveries) != 1 || deliveries[0].ID != "before" {
		t.Fatalf("history = %v, want only the delivery present at capture", deliveries)
	}
}

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
