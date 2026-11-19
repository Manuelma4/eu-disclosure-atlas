package webhook

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestVerifyBindsTheRawTimestampHeader(t *testing.T) {
	secret := "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
	key, err := base64.StdEncoding.DecodeString(strings.TrimPrefix(secret, "whsec_"))
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1614265330, 0)
	body := []byte(`{"value":1}`)
	canonical, err := Sign(secret, "msg_1", now, body)
	if err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{"1614265330", "01614265330", "+1614265330"} {
		t.Run(raw, func(t *testing.T) {
			headers := http.Header{}
			headers.Set("webhook-id", "msg_1")
			headers.Set("webhook-timestamp", raw)
			headers.Set("webhook-signature", canonical)
			if err := Verify(secret, headers, body, now); (err == nil) != (raw == "1614265330") {
				t.Errorf("canonical signature with timestamp %q: %v", raw, err)
			}
			mac := hmac.New(sha256.New, key)
			fmt.Fprintf(mac, "%s.%s.", "msg_1", raw)
			mac.Write(body)
			headers.Set("webhook-signature", "v1,"+base64.StdEncoding.EncodeToString(mac.Sum(nil)))
			if err := Verify(secret, headers, body, now); err != nil {
				t.Errorf("independent raw signature rejected: %v", err)
			}
			if Verify(secret, headers, []byte(`{"value":2}`), now) == nil {
				t.Error("changed body accepted")
			}
			for _, delta := range []time.Duration{-Tolerance - time.Second, Tolerance + time.Second} {
				if Verify(secret, headers, body, now.Add(delta)) == nil {
					t.Error("out-of-tolerance timestamp accepted")
				}
			}
		})
	}
}

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

func TestEmptySigningKeyIsRejected(t *testing.T) {
	now := time.Unix(1614265330, 0)
	body := []byte(`{}`)
	headers := http.Header{
		"Webhook-Id":        {"test"},
		"Webhook-Timestamp": {"1614265330"},
		"Webhook-Signature": {"v1,06UCkhGCn5kOit6dSyTLexifGb2i7oVnMq8gF5PDXzc="},
	}
	for _, secret := range []string{"", "whsec_", "whsec_\r\n"} {
		if _, err := Sign(secret, "test", now, body); err == nil {
			t.Error("accepted an empty signing key")
		}
		if err := Verify(secret, headers, body, now); err == nil {
			t.Error("accepted a signature with an empty signing key")
		}
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
