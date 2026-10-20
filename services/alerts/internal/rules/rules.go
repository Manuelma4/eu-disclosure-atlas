// Package rules decodes filing.published.v1 events and evaluates alert rules against them.
package rules

import (
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"time"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/store"
)

// Filing holds the fields of contracts/events/filing-published.v1.json that rules and messages use.
type Filing struct {
	ID              string    `json:"id"`
	Source          string    `json:"source"`
	Country         string    `json:"country"`
	FirstSeenAt     time.Time `json:"first_seen_at"`
	PublishedAt     *string   `json:"published_at"`
	TransactionDate *string   `json:"transaction_date"`
	IssuerName      string    `json:"issuer_name"`
	IssuerLEI       *string   `json:"issuer_lei"`
	Role            string    `json:"role"`
	Nature          string    `json:"nature"`
	Direction       int       `json:"direction"`
	InstrumentType  string    `json:"instrument_type"`
	ISIN            *string   `json:"isin"`
	Volume          *float64  `json:"volume"`
	Price           *float64  `json:"price"`
	Currency        *string   `json:"currency"`
	AmountEUR       *float64  `json:"amount_eur"`
	OpenMarket      bool      `json:"open_market"`
}

// Decode parses an event payload, keeping the raw JSON for notifications.
func Decode(value []byte) (Filing, error) {
	var f Filing
	if err := json.Unmarshal(value, &f); err != nil {
		return f, err
	}
	if f.ID == "" || f.FirstSeenAt.IsZero() {
		return f, errors.New("id and first_seen_at are required")
	}
	return f, nil
}

// Natures accepted in rules (models.Nature in the Python package).
var Natures = []string{"buy", "sell", "subscription", "grant", "exercise", "conversion", "exchange", "gift",
	"inheritance", "pledge", "lending", "internal_transfer", "merger", "other"}

// Roles accepted in rules (models.Role).
var Roles = []string{"ceo", "cfo", "chair", "executive", "board_member", "other", "unknown"}

// Match reports whether the filing satisfies the rule's criteria. The issuer (watchlist) condition is
// applied by the store query; it is checked again here when the rule has a watchlist but the filing no LEI.
func Match(r store.Rule, f Filing) bool {
	if !r.Enabled {
		return false
	}
	if r.WatchlistID != nil && (f.IssuerLEI == nil || *f.IssuerLEI == "") {
		return false
	}
	if len(r.Natures) > 0 && !slices.Contains(r.Natures, f.Nature) {
		return false
	}
	if len(r.Roles) > 0 && !slices.Contains(r.Roles, f.Role) {
		return false
	}
	if len(r.Countries) > 0 && !slices.ContainsFunc(r.Countries, func(c string) bool { return strings.EqualFold(c, f.Country) }) {
		return false
	}
	if r.OpenMarketOnly && !f.OpenMarket {
		return false
	}
	if r.MinAmountEUR != nil && (f.AmountEUR == nil || *f.AmountEUR < *r.MinAmountEUR) {
		return false
	}
	return true
}
