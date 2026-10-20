// Package auth validates Keycloak (OIDC) access tokens: signature against the realm's JWKS, issuer,
// audience and expiry, then exposes the subject and scopes to handlers.
package auth

import (
	"context"
	"net/http"
	"slices"
	"strings"

	"github.com/coreos/go-oidc/v3/oidc"
)

// Principal is the authenticated caller.
type Principal struct {
	Subject string
	Scopes  []string
}

// Has reports whether the token carries the scope.
func (p Principal) Has(scope string) bool { return slices.Contains(p.Scopes, scope) }

type ctxKey struct{}

// From returns the principal stored by the middleware.
func From(ctx context.Context) (Principal, bool) {
	p, ok := ctx.Value(ctxKey{}).(Principal)
	return p, ok
}

// WithPrincipal stores a principal (tests and the middleware).
func WithPrincipal(ctx context.Context, p Principal) context.Context {
	return context.WithValue(ctx, ctxKey{}, p)
}

// Verifier checks bearer tokens.
type Verifier struct{ v *oidc.IDTokenVerifier }

// NewVerifier builds a verifier for the issuer and audience. jwksURL may differ from the issuer's host
// (inside Docker the service reaches Keycloak at http://keycloak:8080 while tokens say localhost:8180).
func NewVerifier(ctx context.Context, issuer, audience, jwksURL string) *Verifier {
	if jwksURL == "" {
		jwksURL = strings.TrimSuffix(issuer, "/") + "/protocol/openid-connect/certs"
	}
	return NewVerifierWithKeySet(issuer, audience, oidc.NewRemoteKeySet(ctx, jwksURL))
}

// NewVerifierWithKeySet is used with a static key set in tests.
func NewVerifierWithKeySet(issuer, audience string, keys oidc.KeySet) *Verifier {
	return &Verifier{v: oidc.NewVerifier(issuer, keys, &oidc.Config{
		ClientID:             audience,
		SupportedSigningAlgs: []string{oidc.RS256, oidc.ES256, oidc.PS256},
	})}
}

// Verify validates a raw token and returns its principal.
func (v *Verifier) Verify(ctx context.Context, raw string) (Principal, error) {
	token, err := v.v.Verify(ctx, raw)
	if err != nil {
		return Principal{}, err
	}
	var claims struct {
		Scope string `json:"scope"`
	}
	if err := token.Claims(&claims); err != nil {
		return Principal{}, err
	}
	return Principal{Subject: token.Subject, Scopes: strings.Fields(claims.Scope)}, nil
}

// Require wraps a handler: 401 without a valid bearer token, 403 without one of the scopes.
func (v *Verifier) Require(onError func(w http.ResponseWriter, r *http.Request, status int, detail string),
	next http.Handler, scopes ...string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
		if !ok || raw == "" {
			w.Header().Set("WWW-Authenticate", `Bearer realm="atlas"`)
			onError(w, r, http.StatusUnauthorized, "Bearer token required")
			return
		}
		p, err := v.Verify(r.Context(), raw)
		if err != nil {
			w.Header().Set("WWW-Authenticate", `Bearer realm="atlas", error="invalid_token"`)
			onError(w, r, http.StatusUnauthorized, "Invalid token")
			return
		}
		if len(scopes) > 0 && !slices.ContainsFunc(scopes, p.Has) {
			w.Header().Set("WWW-Authenticate", `Bearer realm="atlas", error="insufficient_scope", scope="`+
				strings.Join(scopes, " ")+`"`)
			onError(w, r, http.StatusForbidden, "Missing scope "+strings.Join(scopes, " or "))
			return
		}
		next.ServeHTTP(w, r.WithContext(WithPrincipal(r.Context(), p)))
	})
}
