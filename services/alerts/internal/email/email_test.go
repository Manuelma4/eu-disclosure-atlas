package email

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestResendAndConfiguration(t *testing.T) {
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer re_test" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	m, err := New(Config{Provider: "resend", From: "alerts@example.org", ResendAPIKey: "re_test", ResendURL: srv.URL})
	if err != nil {
		t.Fatal(err)
	}
	if err := m.Send(context.Background(), Message{To: "user@example.org", Subject: "Hi", Text: "Body"}); err != nil {
		t.Fatal(err)
	}
	if got["subject"] != "Hi" || got["to"].([]any)[0] != "user@example.org" {
		t.Fatalf("payload %v", got)
	}

	if _, err := New(Config{Provider: "smtp"}); err == nil {
		t.Fatal("smtp without address accepted")
	}
	if _, err := New(Config{Provider: "pigeon"}); err == nil {
		t.Fatal("unknown provider accepted")
	}
	disabled, _ := New(Config{})
	if disabled.Send(context.Background(), Message{}) != ErrDisabled {
		t.Fatal("disabled mailer should refuse")
	}
	smtp, _ := New(Config{Provider: "smtp", SMTPAddr: "127.0.0.1:1", From: "a@example.org"})
	if smtp.Send(context.Background(), Message{To: "x@example.org\r\nBcc: y@example.org"}) == nil {
		t.Fatal("header injection accepted")
	}
}
