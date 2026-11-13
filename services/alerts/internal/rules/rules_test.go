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

func BenchmarkMatch(b *testing.B) {
	filing, err := Decode([]byte(payload))
	if err != nil {
		b.Fatal(err)
	}
	watchlist := "w1"
	minimum, aboveAmount := 100_000.0, 200_000.0
	rules := []store.Rule{
		{Enabled: true},
		{},
		{Enabled: true, Natures: []string{"sell"}},
		{Enabled: true, Roles: []string{"cfo", "ceo"}, Countries: []string{"fr", "se"}},
		{Enabled: true, WatchlistID: &watchlist, OpenMarketOnly: true, MinAmountEUR: &minimum},
		{Enabled: true, MinAmountEUR: &aboveAmount},
	}
	want := []bool{true, false, false, true, true, false}
	const matchesPerBatch = 3
	for i, rule := range rules {
		if got := Match(rule, filing); got != want[i] {
			b.Fatalf("rule %d matched %t, want %t", i, got, want[i])
		}
	}
	matched := 0
	b.ReportAllocs()
	for b.Loop() {
		for _, rule := range rules {
			if Match(rule, filing) {
				matched++
			}
		}
	}
	if matched != matchesPerBatch*b.N {
		b.Fatalf("matched %d rules, want %d", matched, matchesPerBatch*b.N)
	}
	b.ReportMetric(float64(len(rules)), "rules/op")
}
