// Package email sends notification emails through SMTP or the Resend HTTP API, chosen by configuration.
package email

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/mail"
	"net/smtp"
	"strings"
	"time"
)

// Message is a plain-text email.
type Message struct {
	To      string
	Subject string
	Text    string
}

// Mailer sends one message.
type Mailer interface {
	Send(ctx context.Context, m Message) error
}

// Config selects the provider: "smtp", "resend" or "" (email disabled).
type Config struct {
	Provider     string
	From         string
	SMTPAddr     string // host:port
	SMTPUser     string
	SMTPPassword string
	ResendAPIKey string
	ResendURL    string // default https://api.resend.com/emails
}

// ErrDisabled is returned by the disabled mailer.
var ErrDisabled = errors.New("email delivery is not configured")

// New returns the configured mailer.
func New(cfg Config) (Mailer, error) {
	switch cfg.Provider {
	case "":
		return disabled{}, nil
	case "smtp":
		if cfg.SMTPAddr == "" || cfg.From == "" {
			return nil, errors.New("smtp needs ATLAS_SMTP_ADDR and ATLAS_EMAIL_FROM")
		}
		return &SMTP{cfg: cfg}, nil
	case "resend":
		if cfg.ResendAPIKey == "" || cfg.From == "" {
			return nil, errors.New("resend needs ATLAS_RESEND_API_KEY and ATLAS_EMAIL_FROM")
		}
		if cfg.ResendURL == "" {
			cfg.ResendURL = "https://api.resend.com/emails"
		}
		return &Resend{cfg: cfg, client: &http.Client{Timeout: 10 * time.Second}}, nil
	}
	return nil, fmt.Errorf("unknown email provider %q (smtp or resend)", cfg.Provider)
}

type disabled struct{}

func (disabled) Send(context.Context, Message) error { return ErrDisabled }

// SMTP sends through a relay; authentication is used when a user is set (net/smtp requires TLS for it,
// except on localhost).
type SMTP struct{ cfg Config }

func (s *SMTP) Send(ctx context.Context, m Message) error {
	if strings.ContainsAny(m.To, "\r\n") || strings.ContainsAny(m.Subject, "\r\n") {
		return errors.New("header injection attempt")
	}
	var body bytes.Buffer
	fmt.Fprintf(&body, "From: %s\r\nTo: %s\r\nSubject: %s\r\nDate: %s\r\nMIME-Version: 1.0\r\n"+
		"Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n%s\r\n",
		s.cfg.From, m.To, mime.QEncoding.Encode("utf-8", m.Subject), time.Now().Format(time.RFC1123Z),
		strings.ReplaceAll(m.Text, "\n", "\r\n"))
	var auth smtp.Auth
	if s.cfg.SMTPUser != "" {
		host, _, _ := net.SplitHostPort(s.cfg.SMTPAddr)
		auth = smtp.PlainAuth("", s.cfg.SMTPUser, s.cfg.SMTPPassword, host)
	}
	envelope, err := mail.ParseAddress(s.cfg.From) // From may be "Name <address>"
	if err != nil {
		return fmt.Errorf("invalid ATLAS_EMAIL_FROM: %w", err)
	}
	done := make(chan error, 1)
	go func() { done <- smtp.SendMail(s.cfg.SMTPAddr, auth, envelope.Address, []string{m.To}, body.Bytes()) }()
	select {
	case err := <-done:
		return err
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Resend sends through https://resend.com's HTTP API.
type Resend struct {
	cfg    Config
	client *http.Client
}

func (r *Resend) Send(ctx context.Context, m Message) error {
	payload, _ := json.Marshal(map[string]any{"from": r.cfg.From, "to": []string{m.To}, "subject": m.Subject, "text": m.Text})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, r.cfg.ResendURL, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+r.cfg.ResendAPIKey)
	req.Header.Set("Content-Type", "application/json")
	resp, err := r.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return fmt.Errorf("resend answered %d: %s", resp.StatusCode, detail)
	}
	return nil
}
