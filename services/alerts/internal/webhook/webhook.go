// Package webhook signs and sends webhooks following the Standard Webhooks specification
// (https://www.standardwebhooks.com): headers webhook-id, webhook-timestamp and webhook-signature, where the
// signature is "v1," + base64(HMAC-SHA256(secret, id + "." + timestamp + "." + body)).
package webhook

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const secretPrefix = "whsec_"

// Tolerance is how far a webhook-timestamp may be from the receiver's clock (replay protection).
const Tolerance = 5 * time.Minute

// NewSecret returns a random signing secret ("whsec_" + base64 of 24 bytes).
func NewSecret() string {
	b := make([]byte, 24)
	_, _ = rand.Read(b)
	return secretPrefix + base64.StdEncoding.EncodeToString(b)
}

func key(secret string) ([]byte, error) {
	return base64.StdEncoding.DecodeString(strings.TrimPrefix(secret, secretPrefix))
}

// Sign returns the webhook-signature header value.
func Sign(secret, id string, ts time.Time, body []byte) (string, error) {
	k, err := key(secret)
	if err != nil {
		return "", fmt.Errorf("invalid secret: %w", err)
	}
	mac := hmac.New(sha256.New, k)
	fmt.Fprintf(mac, "%s.%d.", id, ts.Unix())
	mac.Write(body)
	return "v1," + base64.StdEncoding.EncodeToString(mac.Sum(nil)), nil
}

// Verify checks the headers of a received webhook against the secret.
func Verify(secret string, header http.Header, body []byte, now time.Time) error {
	id, tsRaw, sigs := header.Get("webhook-id"), header.Get("webhook-timestamp"), header.Get("webhook-signature")
	if id == "" || tsRaw == "" || sigs == "" {
		return errors.New("missing webhook headers")
	}
	unix, err := strconv.ParseInt(tsRaw, 10, 64)
	if err != nil {
		return errors.New("invalid webhook-timestamp")
	}
	ts := time.Unix(unix, 0)
	if now.Sub(ts) > Tolerance || ts.Sub(now) > Tolerance {
		return errors.New("webhook-timestamp outside tolerance")
	}
	expected, err := Sign(secret, id, ts, body)
	if err != nil {
		return err
	}
	for _, sig := range strings.Fields(sigs) { // several space-separated signatures during key rotation
		if subtle.ConstantTimeCompare([]byte(sig), []byte(expected)) == 1 {
			return nil
		}
	}
	return errors.New("no matching signature")
}

// ValidateURL accepts https URLs (and http when allowInsecure, for local development).
func ValidateURL(raw string, allowInsecure bool) error {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return errors.New("webhook URL must be absolute")
	}
	if u.Scheme != "https" && !(allowInsecure && u.Scheme == "http") {
		return errors.New("webhook URL must use https")
	}
	if u.User != nil {
		return errors.New("webhook URL must not contain credentials")
	}
	return nil
}

// Sender posts signed webhooks. Unless AllowPrivate, it refuses to connect to loopback, private, link-local
// and other non-public addresses (checked on the resolved IP at dial time, so DNS cannot be used for SSRF).
type Sender struct {
	Client       *http.Client
	AllowPrivate bool
	UserAgent    string
}

// NewSender returns a sender with a 10 s timeout and no redirects.
func NewSender(allowPrivate bool) *Sender {
	s := &Sender{AllowPrivate: allowPrivate, UserAgent: "EU-Disclosure-Atlas-Webhooks/1"}
	dialer := &net.Dialer{Timeout: 5 * time.Second, Control: s.control}
	s.Client = &http.Client{
		Timeout:       10 * time.Second,
		Transport:     &http.Transport{DialContext: dialer.DialContext, Proxy: nil, MaxIdleConnsPerHost: 4},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	return s
}

func (s *Sender) control(_, address string, _ syscall.RawConn) error {
	if s.AllowPrivate {
		return nil
	}
	addrPort, err := netip.ParseAddrPort(address)
	if err != nil {
		return err
	}
	ip := addrPort.Addr().Unmap()
	if !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return fmt.Errorf("refusing to deliver to non-public address %s", ip)
	}
	return nil
}

// Result is the outcome of one attempt.
type Result struct {
	Status int
	Err    error
}

// OK reports a 2xx response.
func (r Result) OK() bool { return r.Err == nil && r.Status >= 200 && r.Status < 300 }

// Send signs body with the endpoint secret and POSTs it.
func (s *Sender) Send(ctx context.Context, endpoint, secret, id string, body []byte) Result {
	now := time.Now()
	signature, err := Sign(secret, id, now, body)
	if err != nil {
		return Result{Err: err}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return Result{Err: err}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", s.UserAgent)
	req.Header.Set("webhook-id", id)
	req.Header.Set("webhook-timestamp", strconv.FormatInt(now.Unix(), 10))
	req.Header.Set("webhook-signature", signature)
	resp, err := s.Client.Do(req)
	if err != nil {
		return Result{Err: err}
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return Result{Status: resp.StatusCode, Err: fmt.Errorf("endpoint answered %d", resp.StatusCode)}
	}
	return Result{Status: resp.StatusCode}
}
