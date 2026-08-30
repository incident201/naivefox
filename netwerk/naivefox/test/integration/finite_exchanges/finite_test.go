// Copyright 2026 NaiveFox contributors. MPL-2.0.
package forwardproxy

import (
	"bytes"
	"context"
	"crypto/sha256"
	"io"
	"net/http/httptest"
	"strconv"
	"sync"
	"testing"
	"time"
)

func TestFiniteLaneReorderReplayAndBound(t *testing.T) {
	var lane finiteLane
	done := make(chan struct{})
	var order []int
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		if err := lane.ordered(context.Background(), done, 1, 2, func() error {
			order = append(order, 1)
			return nil
		}); err != nil {
			t.Error(err)
		}
	}()
	if err := lane.ordered(context.Background(), done, 2, 2, func() error { return nil }); err == nil {
		t.Fatal("accepted beyond window")
	}
	if err := lane.ordered(context.Background(), done, 0, 2, func() error {
		order = append(order, 0)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	wg.Wait()
	if len(order) != 2 || order[0] != 0 || order[1] != 1 {
		t.Fatal("reordered byte stream")
	}
	if err := lane.ordered(context.Background(), done, 0, 2, func() error { return nil }); err == nil {
		t.Fatal("accepted replay")
	}
}

func TestFiniteLaneCancellation(t *testing.T) {
	var lane finiteLane
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := lane.ordered(ctx, make(chan struct{}), 1, 2, func() error {
		t.Fatal("cancelled waiter ran")
		return nil
	}); err == nil {
		t.Fatal("ignored cancellation")
	}
}

func testFiniteSession(t *testing.T) (*Handler, *finiteSession) {
	t.Helper()
	h := new(Handler)
	s := &finiteSession{id: "0123456789abcdef0123456789abcdef", handler: h,
		peer: "127.0.0.1:1234", auth: sha256.Sum256(nil), done: make(chan struct{})}
	s.ctx, s.cancel = context.WithCancel(context.Background())
	s.upIn, s.upOut = io.Pipe()
	s.downIn, s.downOut = io.Pipe()
	s.timer = time.AfterFunc(time.Minute, s.close)
	finiteRegistry.Lock()
	finiteRegistry.sessions[s.id] = s
	finiteRegistry.Unlock()
	t.Cleanup(s.close)
	return h, s
}

func TestFiniteBodiesAndHalfClose(t *testing.T) {
	h, s := testFiniteSession(t)
	payload := bytes.Repeat([]byte("bounded opaque bytes"), 53)
	request := func(op string, seq int, body []byte, fin bool) *httptest.ResponseRecorder {
		t.Helper()
		method := "POST"
		if op == "down" {
			method = "GET"
		}
		r := httptest.NewRequest(method, "https://localhost/rewritten-cover", bytes.NewReader(body))
		r.ProtoMajor = 2
		r.RemoteAddr = s.peer
		r.Header.Set("X-Naivefox-Finite", "1")
		r.Header.Set("X-Naivefox-Operation", op)
		r.Header.Set("X-Naivefox-Session", s.id)
		r.Header.Set("X-Naivefox-Sequence", strconv.Itoa(seq))
		if fin {
			r.Header.Set("X-Naivefox-Fin", "1")
		}
		w := httptest.NewRecorder()
		if err := h.serveFinite(w, r, context.Background()); err != nil {
			t.Fatal(err)
		}
		return w
	}
	up := make(chan []byte, 1)
	go func() {
		body, _ := io.ReadAll(s.upIn)
		up <- body
	}()
	if w := request("up", 0, payload, false); w.Code != 200 || w.Body.Len() != 0 {
		t.Fatal("upload did not finish normally")
	}
	request("up", 1, nil, true)
	if !bytes.Equal(<-up, payload) {
		t.Fatal("upload or FIN corrupted")
	}
	go func() {
		s.downOut.Write(payload)
		s.downOut.Close()
	}()
	w := request("down", 0, nil, false)
	if w.Code != 200 || !bytes.Equal(w.Body.Bytes(), payload) {
		t.Fatal("downstream after upstream FIN corrupted")
	}
	if request("down", 1, nil, false).Code != 204 {
		t.Fatal("missing downstream EOF")
	}
	request("close", 0, nil, false)
	select {
	case <-s.done:
	default:
		t.Fatal("explicit release did not close session")
	}
}

func TestFiniteRejectsProtocolSizeAndOwnership(t *testing.T) {
	for _, kind := range []string{"protocol", "size", "peer", "auth", "sequence", "unknown"} {
		t.Run(kind, func(t *testing.T) {
			h, s := testFiniteSession(t)
			r := httptest.NewRequest("POST", "https://localhost/", bytes.NewReader(nil))
			r.ProtoMajor = 2
			r.RemoteAddr = s.peer
			r.Header.Set("X-Naivefox-Finite", "1")
			r.Header.Set("X-Naivefox-Operation", "close")
			r.Header.Set("X-Naivefox-Session", s.id)
			r.Header.Set("X-Naivefox-Sequence", "0")
			switch kind {
			case "protocol":
				r.ProtoMajor = 3
			case "size":
				r.ContentLength = finiteBytes + 1
			case "peer":
				r.RemoteAddr = "127.0.0.1:5678"
			case "auth":
				r.Header.Set("Proxy-Authorization", "not-the-owner")
			case "sequence":
				r.Header.Set("X-Naivefox-Sequence", "00")
			case "unknown":
				r.Header.Set("X-Naivefox-Operation", "unknown")
			}
			if err := h.serveFinite(httptest.NewRecorder(), r, context.Background()); err == nil {
				t.Fatal("accepted malformed exchange")
			}
		})
	}
}

func TestFiniteCancelInterruptsBlockedRead(t *testing.T) {
	h, s := testFiniteSession(t)
	ctx, cancel := context.WithCancel(context.Background())
	r := httptest.NewRequest("GET", "https://localhost/", nil).WithContext(ctx)
	r.ProtoMajor = 2
	r.RemoteAddr = s.peer
	r.Header.Set("X-Naivefox-Finite", "1")
	r.Header.Set("X-Naivefox-Operation", "down")
	r.Header.Set("X-Naivefox-Session", s.id)
	r.Header.Set("X-Naivefox-Sequence", "0")
	finished := make(chan error, 1)
	go func() { finished <- h.serveFinite(httptest.NewRecorder(), r, context.Background()) }()
	cancel()
	select {
	case err := <-finished:
		if err == nil {
			t.Fatal("cancelled read succeeded")
		}
	case <-time.After(time.Second):
		t.Fatal("read retained after cancellation")
	}
}
