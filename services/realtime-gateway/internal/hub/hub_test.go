package hub

import (
	"fmt"
	"testing"
	"time"
)

var t0 = time.Date(2026, 10, 2, 22, 0, 0, 0, time.UTC)

func event(minute int, id string) Event {
	seen := t0.Add(time.Duration(minute) * time.Minute)
	return Event{Position: Position{Seen: seen, Record: id},
		Data: fmt.Appendf(nil, `{"id":%q,"first_seen_at":%q}`, id, seen.Format(time.RFC3339Nano))}
}

func TestPositionFormatMatchesPythonIsoformat(t *testing.T) {
	p := Position{Seen: t0, Record: "se_fi_pdmr:1"}
	if got, want := p.String(), "2026-10-02T22:00:00+00:00|se_fi_pdmr:1"; got != want {
		t.Fatalf("got %q want %q", got, want)
	}
	p.Seen = t0.Add(123456 * time.Microsecond)
	if got, want := p.String(), "2026-10-02T22:00:00.123456+00:00|se_fi_pdmr:1"; got != want {
		t.Fatalf("got %q want %q", got, want)
	}
	for _, in := range []string{p.String(), "2026-10-02T22:00:00.123456Z|se_fi_pdmr:1",
		"2026-10-02T22:00:00.123456 00:00|se_fi_pdmr:1"} {
		q, err := ParsePosition(in)
		if err != nil || !q.Seen.Equal(p.Seen) || q.Record != p.Record {
			t.Fatalf("ParsePosition(%q) = %v, %v", in, q, err)
		}
	}
	for _, bad := range []string{"", "nope", "2026-10-02|", "yesterday|x"} {
		if _, err := ParsePosition(bad); err == nil {
			t.Fatalf("ParsePosition(%q) should fail", bad)
		}
	}
}

func TestDecode(t *testing.T) {
	e, err := Decode([]byte("{\n \"id\": \"a\", \"first_seen_at\": \"2026-10-02T22:00:00Z\", \"x\": 1}"))
	if err != nil || e.Record != "a" || !e.Seen.Equal(t0) || string(e.Data) != `{"id":"a","first_seen_at":"2026-10-02T22:00:00Z","x":1}` {
		t.Fatalf("Decode = %+v, %v", e, err)
	}
	for _, bad := range []string{`not json`, `{"id":"a"}`, `{"first_seen_at":"2026-10-02T22:00:00Z"}`} {
		if _, err := Decode([]byte(bad)); err == nil {
			t.Fatalf("Decode(%s) should fail", bad)
		}
	}
}

func TestReplayIsOrderedBoundedAndDeduplicated(t *testing.T) {
	h := New(3, 8)
	h.Publish(event(1, "a"))
	h.Publish(event(3, "c"))
	h.Publish(event(2, "b")) // out of order: inserted in place
	if h.Publish(event(2, "b")) {
		t.Fatal("duplicate accepted")
	}
	h.Publish(event(4, "d")) // evicts a
	_, all := h.Subscribe(nil)
	if ids := records(all); ids != "bcd" {
		t.Fatalf("buffer = %s", ids)
	}
	after := Position{Seen: t0.Add(2 * time.Minute), Record: "b"}
	_, backlog := h.Subscribe(&after)
	if ids := records(backlog); ids != "cd" {
		t.Fatalf("backlog after b = %s", ids)
	}
	if !h.Publish(event(1, "a")) { // evicted ids may come back (outside the window)
		t.Fatal("evicted id rejected")
	}
}

func TestLiveDeliverySlowSubscriberAndClose(t *testing.T) {
	h := New(10, 1)
	fast, _ := h.Subscribe(nil)
	slow, _ := h.Subscribe(nil)
	h.Publish(event(1, "a"))
	if e := <-fast.C; e.Record != "a" {
		t.Fatalf("got %s", e.Record)
	}
	h.Publish(event(2, "b")) // slow still holds a: its buffer is full, so it is disconnected
	if _, open := <-slow.C; !open {
		t.Fatal("slow subscriber should still drain a first")
	}
	if _, open := <-slow.C; open {
		t.Fatal("slow subscriber should be closed")
	}
	if h.Subscribers() != 1 {
		t.Fatalf("subscribers = %d", h.Subscribers())
	}
	h.Close()
	<-fast.C // b
	if _, open := <-fast.C; open {
		t.Fatal("Close should close subscribers")
	}
	h.Unsubscribe(fast) // idempotent
	if h.Publish(event(3, "c")) {
		t.Fatal("closed hub accepted an event")
	}
}

func records(events []Event) string {
	s := ""
	for _, e := range events {
		s += e.Record
	}
	return s
}
