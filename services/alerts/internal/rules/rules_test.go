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

func BenchmarkMatchCandidates(b *testing.B) {
	lei := "549300EXAMPLE0000155"
	amount, highMinimum := 150_000.0, 200_000.0
	watchlist := "w1"
	filings := []Filing{
		{Country: "SE", Role: "ceo", Nature: "buy", IssuerLEI: &lei, AmountEUR: &amount, OpenMarket: true},
		{Country: "FR", Role: "cfo", Nature: "sell", IssuerLEI: &lei, AmountEUR: &amount, OpenMarket: true},
		{Country: "DE", Role: "chair", Nature: "grant", IssuerLEI: &lei, AmountEUR: &amount},
		{Country: "SE", Role: "ceo", Nature: "buy", OpenMarket: true},
	}
	groups := []struct {
		rule store.Rule
		want [4]bool
	}{
		{store.Rule{Enabled: true}, [4]bool{true, true, true, true}},
		{store.Rule{Enabled: true, Natures: []string{"buy"}, Countries: []string{"SE", "FR"}}, [4]bool{true, false, false, true}},
		{store.Rule{Enabled: true, Roles: []string{"ceo", "cfo"}, Countries: []string{"AT", "BE", "DE", "DK", "ES", "FI", "FR", "SE"}, MinAmountEUR: &highMinimum}, [4]bool{}},
		{store.Rule{Enabled: true, WatchlistID: &watchlist, OpenMarketOnly: true, MinAmountEUR: &amount}, [4]bool{true, true, false, false}},
	}

	candidates := make([][]store.Rule, len(filings))
	matchesPerBatch, evaluationsPerBatch := 0, 0
	for i, filing := range filings {
		for range 16 {
			for _, group := range groups {
				if filing.IssuerLEI == nil && group.rule.WatchlistID != nil {
					continue // the store only returns watchlist rules for a known issuer
				}
				if got := Match(group.rule, filing); got != group.want[i] {
					b.Fatalf("filing %d, candidate %d: match %t, want %t", i, len(candidates[i]), got, group.want[i])
				}
				candidates[i] = append(candidates[i], group.rule)
				evaluationsPerBatch++
				if group.want[i] {
					matchesPerBatch++
				}
			}
		}
	}
	const evaluations = 240
	const expectedMatches = 128
	if evaluationsPerBatch != evaluations {
		b.Fatalf("fixture has %d candidates, want %d", evaluationsPerBatch, evaluations)
	}
	if matchesPerBatch != expectedMatches {
		b.Fatalf("fixture matched %d rules, want %d", matchesPerBatch, expectedMatches)
	}
	matched := 0
	b.ReportAllocs()
	for b.Loop() {
		for i, filing := range filings {
			for _, rule := range candidates[i] {
				if Match(rule, filing) {
					matched++
				}
			}
		}
	}
	if matched != expectedMatches*b.N {
		b.Fatalf("matched %d rules, want %d", matched, expectedMatches*b.N)
	}
	b.ReportMetric(evaluations, "rules/op")
}
