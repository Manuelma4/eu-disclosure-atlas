package server

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Manuelma4/eu-disclosure-atlas/services/realtime-gateway/internal/hub"
)

var t0 = time.Date(2026, 10, 2, 22, 0, 0, 0, time.UTC)

func filing(minute int, id string) hub.Event {
	seen := t0.Add(time.Duration(minute) * time.Minute)
	e, err := hub.Decode(fmt.Appendf(nil, `{"id":%q,"first_seen_at":%q,"issuer_name":"Example AB"}`, id,
		seen.Format(time.RFC3339Nano)))
	if err != nil {
		panic(err)
	}
	return e
}

func newServer(t *testing.T) (*httptest.Server, *hub.Hub, *Server) {
	t.Helper()
	h := hub.New(100, 16)
	s := New(Config{CORSOrigins: []string{"http://localhost:3000"}, Heartbeat: 50 * time.Millisecond}, h,
		NewMetrics(), slog.New(slog.NewTextHandler(io.Discard, nil)))
	ts := httptest.NewServer(s.Handler())
	t.Cleanup(func() { h.Close(); ts.Close() })
	return ts, h, s
}

// readEvents reads SSE frames until n `filing` events have arrived.
func readEvents(t *testing.T, body io.Reader, n int) (ids []string, raw string) {
	t.Helper()
	scanner := bufio.NewScanner(body)
	var b strings.Builder
	for len(ids) < n && scanner.Scan() {
		line := scanner.Text()
		b.WriteString(line + "\n")
		if id, ok := strings.CutPrefix(line, "id: "); ok {
			ids = append(ids, id)
		}
	}
	return ids, b.String()
}

func get(t *testing.T, url string, header map[string]string) *http.Response {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	for k, v := range header {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func TestStreamResumesFromLastEventIDAndSince(t *testing.T) {
	ts, h, _ := newServer(t)
	for i, id := range []string{"a", "b", "c"} {
		h.Publish(filing(i, id))
	}
	afterA := filing(0, "a").ID()

	resp := get(t, ts.URL+"/v1/stream", map[string]string{"Last-Event-ID": afterA})
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("content type %q", ct)
	}
	ids, raw := readEvents(t, resp.Body, 2)
	if len(ids) != 2 || ids[0] != filing(1, "b").ID() || ids[1] != filing(2, "c").ID() {
		t.Fatalf("ids = %v", ids)
	}
	if !strings.HasPrefix(raw, "retry: 5000\n") || !strings.Contains(raw, "event: filing\ndata: {\"id\":\"b\"") {
		t.Fatalf("unexpected frames:\n%s", raw)
	}

	// ?since= with the REST API's timestamp format (Z) works too; the header wins over the query.
	since := "2026-10-02T22:01:00Z|b"
	resp = get(t, ts.URL+"/v1/stream?since="+since, nil)
	if ids, _ := readEvents(t, resp.Body, 1); ids[0] != filing(2, "c").ID() {
		t.Fatalf("since ids = %v", ids)
	}
}

func TestStreamDeliversLiveEventsAndHeartbeats(t *testing.T) {
	ts, h, s := newServer(t)
	h.Publish(filing(0, "old"))
	resp := get(t, ts.URL+"/v1/stream", nil) // no position: only events newer than now
	reader := bufio.NewReader(resp.Body)
	waitFor(t, func() bool { return h.Subscribers() == 1 })
	h.Publish(hub.Event{Position: hub.Position{Seen: time.Now().Add(time.Second), Record: "new"},
		Data: []byte(`{"id":"new"}`), Produced: time.Now()})
	ids, raw := readEvents(t, reader, 1)
	if len(ids) != 1 || !strings.HasSuffix(ids[0], "|new") {
		t.Fatalf("ids = %v\n%s", ids, raw)
	}
	line, _ := reader.ReadString('\n')
	for !strings.HasPrefix(line, ": ping") {
		line, _ = reader.ReadString('\n')
	}
	if got := testutilValue(t, s, "atlas_stream_clients"); got != "1" {
		t.Fatalf("clients gauge = %s", got)
	}
}

func TestStreamRejectsBadPosition(t *testing.T) {
	ts, _, _ := newServer(t)
	resp := get(t, ts.URL+"/v1/stream?since=garbage", nil)
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusBadRequest || resp.Header.Get("Content-Type") != "application/problem+json" ||
		!strings.Contains(string(body), "Invalid stream position") {
		t.Fatalf("status %d body %s", resp.StatusCode, body)
	}
}

func TestShutdownClosesStreams(t *testing.T) {
	ts, h, _ := newServer(t)
	resp := get(t, ts.URL+"/v1/stream", nil)
	waitFor(t, func() bool { return h.Subscribers() == 1 })
	h.Close()
	if _, err := io.ReadAll(resp.Body); err != nil {
		t.Fatalf("stream should end cleanly: %v", err)
	}
}

func TestProbesMetricsAndCORS(t *testing.T) {
	ts, _, s := newServer(t)
	if r := get(t, ts.URL+"/healthz", nil); r.StatusCode != 200 {
		t.Fatalf("healthz %d", r.StatusCode)
	}
	if r := get(t, ts.URL+"/readyz", nil); r.StatusCode != 503 {
		t.Fatalf("readyz before ready %d", r.StatusCode)
	}
	s.SetReady(true)
	if r := get(t, ts.URL+"/readyz", nil); r.StatusCode != 200 {
		t.Fatalf("readyz %d", r.StatusCode)
	}
	body, _ := io.ReadAll(get(t, ts.URL+"/metrics", nil).Body)
	for _, name := range []string{"atlas_stream_clients", "atlas_stream_events_total", "atlas_stream_event_delay_seconds", "go_goroutines"} {
		if !strings.Contains(string(body), name) {
			t.Fatalf("metrics missing %s", name)
		}
	}

	req, _ := http.NewRequest(http.MethodOptions, ts.URL+"/v1/stream", nil)
	req.Header.Set("Origin", "http://localhost:3000")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 204 || resp.Header.Get("Access-Control-Allow-Origin") != "http://localhost:3000" ||
		!strings.Contains(resp.Header.Get("Access-Control-Allow-Headers"), "Last-Event-ID") {
		t.Fatalf("preflight %d %v", resp.StatusCode, resp.Header)
	}
	other := get(t, ts.URL+"/healthz", map[string]string{"Origin": "https://evil.example"})
	if other.Header.Get("Access-Control-Allow-Origin") != "" {
		t.Fatal("unknown origin allowed")
	}
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("condition not met")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func testutilValue(t *testing.T, s *Server, name string) string {
	t.Helper()
	families, err := s.metrics.Registry.Gather()
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range families {
		if f.GetName() == name {
			return fmt.Sprint(f.GetMetric()[0].GetGauge().GetValue())
		}
	}
	return ""
}
