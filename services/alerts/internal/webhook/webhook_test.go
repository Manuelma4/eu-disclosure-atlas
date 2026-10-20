package webhook

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestSignMatchesStandardWebhooksVector(t *testing.T) {
	// Test vector from the Standard Webhooks reference libraries.
	sig, err := Sign("whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", "msg_p5jXN8AQM9LWM0D4loKWxJek",
		time.Unix(1614265330, 0), []byte(`{"test": 2432232314}`))
	if err != nil || sig != "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=" {
		t.Fatalf("Sign = %q, %v", sig, err)
	}
}

func TestSendAndVerify(t *testing.T) {
	secret := NewSecret()
	if !strings.HasPrefix(secret, "whsec_") {
		t.Fatal(secret)
	}
	var verifyErr error
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		verifyErr = Verify(secret, r.Header, body, time.Now())
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	res := NewSender(true).Send(context.Background(), srv.URL, secret, "msg_1", []byte(`{"a":1}`))
	if !res.OK() || verifyErr != nil {
		t.Fatalf("result %+v, verify %v", res, verifyErr)
	}

	h := http.Header{}
	h.Set("webhook-id", "msg_1")
	h.Set("webhook-timestamp", "1614265330")
	h.Set("webhook-signature", "v1,AAAA")
	if Verify(secret, h, []byte(`{}`), time.Now()) == nil {
		t.Fatal("stale timestamp accepted")
	}
	h.Set("webhook-timestamp", strconv.FormatInt(time.Now().Unix(), 10))
	if Verify(secret, h, []byte(`{}`), time.Now()) == nil {
		t.Fatal("bad signature accepted")
	}
}

func TestSenderReportsFailuresAndBlocksPrivateAddresses(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	if res := NewSender(true).Send(context.Background(), srv.URL, NewSecret(), "m", nil); res.OK() || res.Status != 500 {
		t.Fatalf("500 should fail: %+v", res)
	}
	res := NewSender(false).Send(context.Background(), srv.URL, NewSecret(), "m", nil)
	if res.OK() || !strings.Contains(res.Err.Error(), "non-public address") {
		t.Fatalf("loopback should be refused: %+v", res)
	}
}

func TestValidateURL(t *testing.T) {
	for raw, insecure := range map[string]bool{"https://example.com/hook": false, "http://sink:8099/": true} {
		if err := ValidateURL(raw, insecure); err != nil {
			t.Fatalf("%s: %v", raw, err)
		}
	}
	for _, raw := range []string{"http://example.com", "ftp://x", "/relative", "https://user:pw@example.com"} {
		if ValidateURL(raw, false) == nil {
			t.Fatalf("%s accepted", raw)
		}
	}
}
