// Package hub fans live filing events out to subscribers and keeps a bounded replay buffer, so a client that
// reconnects with Last-Event-ID (or ?since=) receives what it missed while it was away.
package hub

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

// Position is a place in the live stream: the SSE event id "<first_seen_at>|<record id>", ordered by time
// and then by record id, exactly like the Python API's /v1/stream.
type Position struct {
	Seen   time.Time
	Record string
}

// After reports whether p comes strictly after q.
func (p Position) After(q Position) bool {
	if !p.Seen.Equal(q.Seen) {
		return p.Seen.After(q.Seen)
	}
	return p.Record > q.Record
}

// String formats the position like Python's datetime.isoformat() on a UTC timestamp, so event ids are
// byte-identical whether a client is served by the gateway or by the Python API.
func (p Position) String() string {
	t := p.Seen.UTC()
	s := t.Format("2006-01-02T15:04:05")
	if us := t.Nanosecond() / 1000; us != 0 {
		s += fmt.Sprintf(".%06d", us)
	}
	return s + "+00:00|" + p.Record
}

// ParsePosition accepts "<RFC 3339 time>|<record id>" (Z or +00:00 offsets).
func ParsePosition(value string) (Position, error) {
	seen, record, ok := strings.Cut(value, "|")
	if !ok || record == "" {
		return Position{}, errors.New("stream position must look like <timestamp>|<record id>")
	}
	// A '+' in an unencoded query string arrives as a space.
	t, err := time.Parse(time.RFC3339Nano, strings.ReplaceAll(seen, " ", "+"))
	if err != nil {
		return Position{}, fmt.Errorf("invalid stream timestamp: %w", err)
	}
	return Position{Seen: t, Record: record}, nil
}

// Event is one filing as delivered to browsers: its position and the JSON payload (the API's Transaction).
type Event struct {
	Position
	Data []byte
}

// ID is the SSE event id.
func (e Event) ID() string { return e.Position.String() }

// Decode builds an Event from a filing.published.v1 payload.
func Decode(value []byte) (Event, error) {
	var head struct {
		ID          string    `json:"id"`
		FirstSeenAt time.Time `json:"first_seen_at"`
	}
	if err := json.Unmarshal(value, &head); err != nil {
		return Event{}, fmt.Errorf("decode filing: %w", err)
	}
	if head.ID == "" || head.FirstSeenAt.IsZero() {
		return Event{}, errors.New("decode filing: id and first_seen_at are required")
	}
	// SSE data must be one line: compact the JSON (the publisher already sends it compact).
	var out bytes.Buffer
	if err := json.Compact(&out, value); err != nil {
		return Event{}, fmt.Errorf("decode filing: %w", err)
	}
	return Event{Position: Position{Seen: head.FirstSeenAt, Record: head.ID}, Data: out.Bytes()}, nil
}

// Subscriber receives live events on C. C is closed when the subscriber is too slow (its buffer filled up)
// or the hub shuts down; the client then reconnects and resumes from its last event id.
type Subscriber struct {
	C    chan Event
	done bool
}

// Hub is safe for concurrent use.
type Hub struct {
	mu       sync.Mutex
	capacity int
	sendBuf  int
	buffer   []Event // sorted by Position, at most capacity long
	ids      map[string]struct{}
	subs     map[*Subscriber]struct{}
	closed   bool
}

// New returns a hub replaying up to capacity events; each subscriber may lag by up to sendBuf events.
func New(capacity, sendBuf int) *Hub {
	return &Hub{capacity: capacity, sendBuf: sendBuf, ids: map[string]struct{}{}, subs: map[*Subscriber]struct{}{}}
}

// Publish adds an event, dropping duplicates (at-least-once delivery upstream) and the oldest event when
// the buffer is full, and forwards it to every subscriber. It returns false for a duplicate.
func (h *Hub) Publish(e Event) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.closed {
		return false
	}
	if _, dup := h.ids[e.Record]; dup {
		return false
	}
	// Events arrive almost in order, so the insertion point is nearly always the end.
	i := len(h.buffer)
	for i > 0 && h.buffer[i-1].After(e.Position) {
		i--
	}
	h.buffer = append(h.buffer, Event{})
	copy(h.buffer[i+1:], h.buffer[i:])
	h.buffer[i] = e
	h.ids[e.Record] = struct{}{}
	if len(h.buffer) > h.capacity {
		delete(h.ids, h.buffer[0].Record)
		h.buffer = append(h.buffer[:0:0], h.buffer[1:]...)
	}
	for sub := range h.subs {
		select {
		case sub.C <- e:
		default: // too slow: disconnect, the client resumes with Last-Event-ID
			h.drop(sub)
		}
	}
	return true
}

// Subscribe registers a subscriber and returns, atomically, the buffered events after `after` (all
// buffered events when after is nil), so nothing is missed or duplicated between replay and live delivery.
func (h *Hub) Subscribe(after *Position) (*Subscriber, []Event) {
	h.mu.Lock()
	defer h.mu.Unlock()
	sub := &Subscriber{C: make(chan Event, h.sendBuf)}
	if h.closed {
		close(sub.C)
		sub.done = true
		return sub, nil
	}
	start := 0
	if after != nil {
		start = sort.Search(len(h.buffer), func(i int) bool { return h.buffer[i].After(*after) })
	}
	backlog := append([]Event(nil), h.buffer[start:]...)
	h.subs[sub] = struct{}{}
	return sub, backlog
}

// Unsubscribe removes a subscriber; it is safe to call more than once.
func (h *Hub) Unsubscribe(sub *Subscriber) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.drop(sub)
}

func (h *Hub) drop(sub *Subscriber) {
	if sub.done {
		return
	}
	sub.done = true
	delete(h.subs, sub)
	close(sub.C)
}

// Close disconnects every subscriber (graceful shutdown) and rejects new events.
func (h *Hub) Close() {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.closed = true
	for sub := range h.subs {
		h.drop(sub)
	}
}

// Subscribers is the number of connected subscribers.
func (h *Hub) Subscribers() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.subs)
}

// Len is the number of buffered events.
func (h *Hub) Len() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.buffer)
}
