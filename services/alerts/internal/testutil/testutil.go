// Package testutil provides a throwaway PostgreSQL database and signed test tokens.
package testutil

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/go-jose/go-jose/v4"
	"github.com/jackc/pgx/v5"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/auth"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/store"
)

// Store opens a fresh database named after the test package on ATLAS_TEST_DATABASE_URL (an admin
// connection, e.g. postgres://postgres:test@localhost:5432/postgres) and skips the test when it is unset.
func Store(t *testing.T, name string) *store.Store {
	t.Helper()
	admin := os.Getenv("ATLAS_TEST_DATABASE_URL")
	if admin == "" {
		t.Skip("set ATLAS_TEST_DATABASE_URL to run PostgreSQL tests")
	}
	ctx := context.Background()
	conn, err := pgx.Connect(ctx, admin)
	if err != nil {
		t.Fatal(err)
	}
	db := "alerts_test_" + name
	for _, sql := range []string{"DROP DATABASE IF EXISTS " + db + " WITH (FORCE)", "CREATE DATABASE " + db} {
		if _, err := conn.Exec(ctx, sql); err != nil {
			t.Fatal(err)
		}
	}
	_ = conn.Close(ctx)
	u, _ := url.Parse(admin)
	u.Path = "/" + db
	s, err := store.Open(ctx, u.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	return s
}

// Issuer signs access tokens like Keycloak's.
type Issuer struct {
	URL, Audience string
	key           *rsa.PrivateKey
}

func NewIssuer(t *testing.T) *Issuer {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	return &Issuer{URL: "http://keycloak.test/realms/atlas", Audience: "atlas-alerts", key: key}
}

// Verifier accepts this issuer's tokens.
func (i *Issuer) Verifier() *auth.Verifier {
	return auth.NewVerifierWithKeySet(i.URL, i.Audience, &oidc.StaticKeySet{PublicKeys: []crypto.PublicKey{i.key.Public()}})
}

// Token returns a signed token for subject with the given scopes; extra claims override defaults.
func (i *Issuer) Token(t *testing.T, subject string, scopes []string, extra map[string]any) string {
	t.Helper()
	signer, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.RS256, Key: i.key}, nil)
	if err != nil {
		t.Fatal(err)
	}
	claims := map[string]any{"iss": i.URL, "aud": []string{i.Audience, "account"}, "sub": subject,
		"exp": time.Now().Add(5 * time.Minute).Unix(), "iat": time.Now().Unix(), "scope": strings.Join(scopes, " ")}
	for k, v := range extra {
		claims[k] = v
	}
	payload, _ := json.Marshal(claims)
	signed, err := signer.Sign(payload)
	if err != nil {
		t.Fatal(err)
	}
	token, err := signed.CompactSerialize()
	if err != nil {
		t.Fatal(err)
	}
	return token
}
