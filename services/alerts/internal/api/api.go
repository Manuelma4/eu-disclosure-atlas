// Package api is the alerts REST API (/v1): watchlists, alert rules, webhook endpoints and the delivery
// log of the signed-in user. Reads need the alerts:read or alerts:write scope, writes need alerts:write.
package api

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/mail"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/auth"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/rules"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/store"
	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/webhook"
)

var leiPattern = regexp.MustCompile(`^[0-9A-Z]{18}[0-9]{2}$`)

// Config holds API behaviour.
type Config struct {
	CORSOrigins           []string
	AllowInsecureWebhooks bool // accept http:// endpoints (local development only)
}

// API serves the REST endpoints.
type API struct {
	cfg   Config
	store *store.Store
	auth  *auth.Verifier
	log   *slog.Logger
}

func New(cfg Config, s *store.Store, v *auth.Verifier, log *slog.Logger) *API {
	return &API{cfg: cfg, store: s, auth: v, log: log}
}

// Routes registers the /v1 endpoints on mux.
func (a *API) Routes(mux *http.ServeMux) {
	read := func(h http.HandlerFunc) http.Handler {
		return a.auth.Require(Problem, h, "alerts:read", "alerts:write")
	}
	write := func(h http.HandlerFunc) http.Handler { return a.auth.Require(Problem, h, "alerts:write") }

	mux.Handle("GET /v1/watchlists", read(a.listWatchlists))
	mux.Handle("POST /v1/watchlists", write(a.createWatchlist))
	mux.Handle("GET /v1/watchlists/{id}", read(a.getWatchlist))
	mux.Handle("DELETE /v1/watchlists/{id}", write(a.deleteWatchlist))
	mux.Handle("PUT /v1/watchlists/{id}/issuers/{lei}", write(a.addIssuer))
	mux.Handle("DELETE /v1/watchlists/{id}/issuers/{lei}", write(a.removeIssuer))

	mux.Handle("GET /v1/rules", read(a.listRules))
	mux.Handle("POST /v1/rules", write(a.createRule))
	mux.Handle("DELETE /v1/rules/{id}", write(a.deleteRule))

	mux.Handle("GET /v1/webhook-endpoints", read(a.listEndpoints))
	mux.Handle("POST /v1/webhook-endpoints", write(a.createEndpoint))
	mux.Handle("DELETE /v1/webhook-endpoints/{id}", write(a.deleteEndpoint))

	mux.Handle("GET /v1/deliveries", read(a.listDeliveries))
}

// CORS lets the website call the API with a bearer token.
func (a *API) CORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if origin := r.Header.Get("Origin"); origin != "" && slices.Contains(a.cfg.CORSOrigins, origin) {
			h := w.Header()
			h.Set("Access-Control-Allow-Origin", origin)
			h.Add("Vary", "Origin")
			if r.Method == http.MethodOptions {
				h.Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
				h.Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
				h.Set("Access-Control-Max-Age", "600")
				w.WriteHeader(http.StatusNoContent)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

// Problem writes an RFC 9457 problem document.
func Problem(w http.ResponseWriter, r *http.Request, status int, detail string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"type": "about:blank", "title": http.StatusText(status), "status": status, "detail": detail,
		"instance": r.URL.Path,
	})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		Problem(w, r, http.StatusBadRequest, "Invalid JSON body: "+err.Error())
		return false
	}
	return true
}

func owner(r *http.Request) string {
	p, _ := auth.From(r.Context())
	return p.Subject
}

// fail maps store errors to HTTP statuses.
func (a *API) fail(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, store.ErrNotFound):
		Problem(w, r, http.StatusNotFound, "Not found")
	case errors.Is(err, store.ErrInvalid):
		Problem(w, r, http.StatusUnprocessableEntity, err.Error())
	default:
		a.log.Error("request failed", "path", r.URL.Path, "err", err)
		Problem(w, r, http.StatusInternalServerError, "Internal error")
	}
}

func validLEI(lei string) (string, bool) {
	lei = strings.ToUpper(strings.TrimSpace(lei))
	return lei, leiPattern.MatchString(lei)
}

// Watchlists -----------------------------------------------------------------------------------------------

func (a *API) listWatchlists(w http.ResponseWriter, r *http.Request) {
	lists, err := a.store.ListWatchlists(r.Context(), owner(r))
	if err != nil {
		a.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": lists})
}

func (a *API) createWatchlist(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name    string   `json:"name"`
		Issuers []string `json:"issuers"`
	}
	if !decode(w, r, &body) {
		return
	}
	body.Name = strings.TrimSpace(body.Name)
	if body.Name == "" || len(body.Name) > 100 {
		Problem(w, r, http.StatusUnprocessableEntity, "name must be 1 to 100 characters")
		return
	}
	for i, lei := range body.Issuers {
		var ok bool
		if body.Issuers[i], ok = validLEI(lei); !ok {
			Problem(w, r, http.StatusUnprocessableEntity, "invalid LEI "+lei)
			return
		}
	}
	list, err := a.store.CreateWatchlist(r.Context(), owner(r), body.Name, body.Issuers)
	if err != nil {
		a.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, list)
}

func (a *API) getWatchlist(w http.ResponseWriter, r *http.Request) {
	list, err := a.store.GetWatchlist(r.Context(), owner(r), r.PathValue("id"))
	if err != nil {
		a.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, list)
}

func (a *API) deleteWatchlist(w http.ResponseWriter, r *http.Request) {
	if err := a.store.DeleteWatchlist(r.Context(), owner(r), r.PathValue("id")); err != nil {
		a.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (a *API) addIssuer(w http.ResponseWriter, r *http.Request) {
	lei, ok := validLEI(r.PathValue("lei"))
	if !ok {
		Problem(w, r, http.StatusUnprocessableEntity, "invalid LEI")
		return
	}
	if err := a.store.AddIssuer(r.Context(), owner(r), r.PathValue("id"), lei); err != nil {
		a.fail(w, r, err)
		return
	}
	a.getWatchlist(w, r)
}

func (a *API) removeIssuer(w http.ResponseWriter, r *http.Request) {
	lei, _ := validLEI(r.PathValue("lei"))
	if err := a.store.RemoveIssuer(r.Context(), owner(r), r.PathValue("id"), lei); err != nil {
		a.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// Rules ----------------------------------------------------------------------------------------------------

type ruleInput struct {
	Name              string   `json:"name"`
	WatchlistID       *string  `json:"watchlist_id"`
	Natures           []string `json:"natures"`
	Roles             []string `json:"roles"`
	Countries         []string `json:"countries"`
	OpenMarketOnly    bool     `json:"open_market_only"`
	MinAmountEUR      *float64 `json:"min_amount_eur"`
	Email             *string  `json:"email"`
	WebhookEndpointID *string  `json:"webhook_endpoint_id"`
}

func (in *ruleInput) validate() string {
	in.Name = strings.TrimSpace(in.Name)
	switch {
	case in.Name == "" || len(in.Name) > 100:
		return "name must be 1 to 100 characters"
	case in.Email == nil && in.WebhookEndpointID == nil:
		return "a rule needs an email or a webhook_endpoint_id"
	case in.MinAmountEUR != nil && *in.MinAmountEUR < 0:
		return "min_amount_eur must be positive"
	}
	if in.Email != nil {
		addr, err := mail.ParseAddress(*in.Email)
		if err != nil || addr.Name != "" {
			return "invalid email"
		}
		in.Email = &addr.Address
	}
	for _, n := range in.Natures {
		if !slices.Contains(rules.Natures, n) {
			return "unknown nature " + n
		}
	}
	for _, role := range in.Roles {
		if !slices.Contains(rules.Roles, role) {
			return "unknown role " + role
		}
	}
	for i, c := range in.Countries {
		if len(c) != 2 {
			return "countries are ISO 3166-1 alpha-2 codes"
		}
		in.Countries[i] = strings.ToUpper(c)
	}
	return ""
}

func (a *API) listRules(w http.ResponseWriter, r *http.Request) {
	list, err := a.store.ListRules(r.Context(), owner(r))
	if err != nil {
		a.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": list})
}

func (a *API) createRule(w http.ResponseWriter, r *http.Request) {
	var in ruleInput
	if !decode(w, r, &in) {
		return
	}
	if msg := in.validate(); msg != "" {
		Problem(w, r, http.StatusUnprocessableEntity, msg)
		return
	}
	rule, err := a.store.CreateRule(r.Context(), owner(r), store.Rule{
		Name: in.Name, WatchlistID: in.WatchlistID, Natures: in.Natures, Roles: in.Roles, Countries: in.Countries,
		OpenMarketOnly: in.OpenMarketOnly, MinAmountEUR: in.MinAmountEUR, Email: in.Email,
		WebhookEndpointID: in.WebhookEndpointID,
	})
	if err != nil {
		a.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, rule)
}

func (a *API) deleteRule(w http.ResponseWriter, r *http.Request) {
	if err := a.store.DeleteRule(r.Context(), owner(r), r.PathValue("id")); err != nil {
		a.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// Webhook endpoints ----------------------------------------------------------------------------------------

func (a *API) listEndpoints(w http.ResponseWriter, r *http.Request) {
	list, err := a.store.ListEndpoints(r.Context(), owner(r))
	if err != nil {
		a.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": list})
}

func (a *API) createEndpoint(w http.ResponseWriter, r *http.Request) {
	var body struct {
		URL         string `json:"url"`
		Description string `json:"description"`
	}
	if !decode(w, r, &body) {
		return
	}
	if err := webhook.ValidateURL(body.URL, a.cfg.AllowInsecureWebhooks); err != nil {
		Problem(w, r, http.StatusUnprocessableEntity, err.Error())
		return
	}
	endpoint, err := a.store.CreateEndpoint(r.Context(), owner(r), body.URL, body.Description, webhook.NewSecret())
	if err != nil {
		a.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, endpoint) // the only response that contains the signing secret
}

func (a *API) deleteEndpoint(w http.ResponseWriter, r *http.Request) {
	if err := a.store.DeleteEndpoint(r.Context(), owner(r), r.PathValue("id")); err != nil {
		a.fail(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// Deliveries -----------------------------------------------------------------------------------------------

func (a *API) listDeliveries(w http.ResponseWriter, r *http.Request) {
	limit, err := strconv.Atoi(r.URL.Query().Get("limit"))
	if err != nil || limit < 1 || limit > 200 {
		limit = 50
	}
	list, err := a.store.ListDeliveries(r.Context(), owner(r), limit)
	if err != nil {
		a.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": list})
}
