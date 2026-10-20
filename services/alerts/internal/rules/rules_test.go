package rules

import (
	"testing"

	"github.com/Manuelma4/eu-disclosure-atlas/services/alerts/internal/store"
)

const payload = `{"id":"se_fi_pdmr:1","source":"se_fi_pdmr","country":"SE","first_seen_at":"2026-10-02T22:00:00Z",
"issuer_name":"Example AB","issuer_lei":"549300EXAMPLE0000155","role":"ceo","nature":"buy","direction":1,
"instrument_type":"share","amount_eur":150000.0,"open_market":true}`

func TestMatch(t *testing.T) {
	f, err := Decode([]byte(payload))
	if err != nil {
		t.Fatal(err)
	}
	min := func(v float64) *float64 { return &v }
	list := "w1"
	cases := []struct {
		name string
		rule store.Rule
		want bool
	}{
		{"any", store.Rule{Enabled: true}, true},
		{"disabled", store.Rule{}, false},
		{"nature", store.Rule{Enabled: true, Natures: []string{"sell"}}, false},
		{"role and country", store.Rule{Enabled: true, Roles: []string{"cfo", "ceo"}, Countries: []string{"se"}}, true},
		{"amount", store.Rule{Enabled: true, MinAmountEUR: min(200000)}, false},
		{"amount ok", store.Rule{Enabled: true, MinAmountEUR: min(100000), OpenMarketOnly: true}, true},
		{"watchlist", store.Rule{Enabled: true, WatchlistID: &list}, true},
	}
	for _, c := range cases {
		if got := Match(c.rule, f); got != c.want {
			t.Errorf("%s: got %v", c.name, got)
		}
	}
	f.IssuerLEI = nil
	if Match(store.Rule{Enabled: true, WatchlistID: &list}, f) {
		t.Error("watchlist rule matched a filing without LEI")
	}
	f.AmountEUR = nil
	if Match(store.Rule{Enabled: true, MinAmountEUR: min(1)}, f) {
		t.Error("amount rule matched a filing without amount")
	}
	if _, err := Decode([]byte(`{"id":""}`)); err == nil {
		t.Error("invalid payload accepted")
	}
}
