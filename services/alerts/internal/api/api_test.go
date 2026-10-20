package api

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/testutil"
)

const lei = "549300EXAMPLE0000155"

type client struct {
	t     *testing.T
	base  string
	token string
}

func (c client) do(method, path string, body any) (int, map[string]any) {
	c.t.Helper()
	var reader io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		reader = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, c.base+path, reader)
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		c.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

func setup(t *testing.T) (*httptest.Server, *testutil.Issuer) {
	s := testutil.Store(t, "api")
	issuer := testutil.NewIssuer(t)
	a := New(Config{CORSOrigins: []string{"http://localhost:3000"}, AllowInsecureWebhooks: true}, s, issuer.Verifier(),
		slog.New(slog.NewTextHandler(io.Discard, nil)))
	mux := http.NewServeMux()
	a.Routes(mux)
	srv := httptest.NewServer(a.CORS(mux))
	t.Cleanup(srv.Close)
	return srv, issuer
}

func TestAuthentication(t *testing.T) {
	srv, issuer := setup(t)
	anonymous := client{t, srv.URL, ""}
	if status, body := anonymous.do("GET", "/v1/watchlists", nil); status != 401 || body["status"].(float64) != 401 {
		t.Fatalf("anonymous: %d %v", status, body)
	}
	expired := client{t, srv.URL, issuer.Token(t, "alice", []string{"alerts:write"},
		map[string]any{"exp": time.Now().Add(-time.Minute).Unix()})}
	if status, _ := expired.do("GET", "/v1/watchlists", nil); status != 401 {
		t.Fatalf("expired: %d", status)
	}
	wrongAudience := client{t, srv.URL, issuer.Token(t, "alice", []string{"alerts:write"}, map[string]any{"aud": "other"})}
	if status, _ := wrongAudience.do("GET", "/v1/watchlists", nil); status != 401 {
		t.Fatalf("wrong audience: %d", status)
	}
	reader := client{t, srv.URL, issuer.Token(t, "alice", []string{"alerts:read"}, nil)}
	if status, _ := reader.do("GET", "/v1/watchlists", nil); status != 200 {
		t.Fatalf("reader GET: %d", status)
	}
	if status, _ := reader.do("POST", "/v1/watchlists", map[string]any{"name": "x"}); status != 403 {
		t.Fatalf("reader POST: %d", status)
	}
}

func TestWatchlistRuleEndpointLifecycleIsScopedByOwner(t *testing.T) {
	srv, issuer := setup(t)
	alice := client{t, srv.URL, issuer.Token(t, "alice", []string{"alerts:write"}, nil)}
	bob := client{t, srv.URL, issuer.Token(t, "bob", []string{"alerts:write"}, nil)}

	status, list := alice.do("POST", "/v1/watchlists", map[string]any{"name": "Nordics", "issuers": []string{lei}})
	if status != 201 || list["issuers"].([]any)[0] != lei {
		t.Fatalf("create watchlist: %d %v", status, list)
	}
	id := list["id"].(string)
	if status, _ := alice.do("POST", "/v1/watchlists", map[string]any{"name": "Bad", "issuers": []string{"nope"}}); status != 422 {
		t.Fatalf("bad LEI: %d", status)
	}
	if status, _ := alice.do("PUT", "/v1/watchlists/"+id+"/issuers/529900t8bm49aursdo55", nil); status != 200 {
		t.Fatalf("add issuer: %d", status)
	}
	if status, _ := bob.do("GET", "/v1/watchlists/"+id, nil); status != 404 {
		t.Fatalf("bob reads alice's watchlist: %d", status)
	}
	if status, _ := bob.do("PUT", "/v1/watchlists/"+id+"/issuers/"+lei, nil); status != 404 {
		t.Fatalf("bob edits alice's watchlist: %d", status)
	}

	status, endpoint := alice.do("POST", "/v1/webhook-endpoints", map[string]any{"url": "http://sink:8099/hook"})
	if status != 201 || endpoint["secret"] == nil {
		t.Fatalf("create endpoint: %d %v", status, endpoint)
	}
	if status, _ := alice.do("POST", "/v1/webhook-endpoints", map[string]any{"url": "ftp://x"}); status != 422 {
		t.Fatalf("bad URL: %d", status)
	}
	_, endpoints := alice.do("GET", "/v1/webhook-endpoints", nil)
	if e := endpoints["items"].([]any)[0].(map[string]any); e["secret"] != nil {
		t.Fatal("secret listed after creation")
	}

	rule := map[string]any{"name": "CEO buys", "watchlist_id": id, "natures": []string{"buy"}, "roles": []string{"ceo"},
		"min_amount_eur": 10000, "webhook_endpoint_id": endpoint["id"], "email": "alice@example.org"}
	status, created := alice.do("POST", "/v1/rules", rule)
	if status != 201 || created["watchlist_id"] != id {
		t.Fatalf("create rule: %d %v", status, created)
	}
	if status, _ := bob.do("POST", "/v1/rules", rule); status != 422 {
		t.Fatalf("bob's rule on alice's watchlist: %d", status)
	}
	for _, bad := range []map[string]any{
		{"name": "no channel"},
		{"name": "x", "email": "not an email"},
		{"name": "x", "email": "a@example.org", "natures": []string{"teleport"}},
		{"name": "x", "email": "a@example.org", "unknown_field": true},
	} {
		if status, _ := alice.do("POST", "/v1/rules", bad); status != 422 && status != 400 {
			t.Fatalf("invalid rule %v accepted: %d", bad, status)
		}
	}
	if _, rules := alice.do("GET", "/v1/rules", nil); len(rules["items"].([]any)) != 1 {
		t.Fatalf("rules: %v", rules)
	}
	if _, deliveries := alice.do("GET", "/v1/deliveries", nil); len(deliveries["items"].([]any)) != 0 {
		t.Fatalf("deliveries: %v", deliveries)
	}
	if status, _ := alice.do("DELETE", "/v1/rules/"+created["id"].(string), nil); status != 204 {
		t.Fatalf("delete rule: %d", status)
	}
	if status, _ := alice.do("DELETE", "/v1/watchlists/"+id, nil); status != 204 {
		t.Fatalf("delete watchlist: %d", status)
	}
}

func TestCORSPreflight(t *testing.T) {
	srv, _ := setup(t)
	req, _ := http.NewRequest(http.MethodOptions, srv.URL+"/v1/watchlists", nil)
	req.Header.Set("Origin", "http://localhost:3000")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 204 || resp.Header.Get("Access-Control-Allow-Headers") != "Authorization, Content-Type" {
		t.Fatalf("preflight: %d %v", resp.StatusCode, resp.Header)
	}
}
