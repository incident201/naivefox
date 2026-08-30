// Copyright 2026 NaiveFox contributors. MPL-2.0.
package forwardproxy

import (
	"bytes"
	"context"
	"crypto/sha256"
	"io"
	"net/http"
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
	s := &finiteSession{id: "0123456789abcdef0123456789abcdef", version: "1", handler: h,
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
	for _, kind := range []string{"protocol", "version", "size", "peer", "malformed-peer", "auth", "sequence", "unknown"} {
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
			case "version":
				r.Header.Set("X-Naivefox-Finite", "2")
			case "size":
				r.ContentLength = finiteBytes + 1
			case "peer":
				r.RemoteAddr = "127.0.0.2:5678"
			case "malformed-peer":
				r.RemoteAddr = "127.0.0.1"
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
	for _, version := range []string{"1", "3"} {
		t.Run(version, func(t *testing.T) { testFiniteCancelInterruptsBlockedRead(t, version) })
	}
}

func TestFiniteSessionSurvivesOuterPortMigration(t *testing.T) {
	for _, version := range []string{"1", "2", "3"} {
		t.Run(version, func(t *testing.T) {
			h, s := testFiniteSession(t)
			s.version = version
			r := httptest.NewRequest("POST", "https://localhost/", nil)
			r.ProtoMajor = 2
			r.RemoteAddr = "127.0.0.1:5678"
			r.Header.Set("X-Naivefox-Finite", version)
			r.Header.Set("X-Naivefox-Operation", "close")
			r.Header.Set("X-Naivefox-Session", s.id)
			r.Header.Set("X-Naivefox-Sequence", "0")
			w := httptest.NewRecorder()
			if err := h.serveFinite(w, r, context.Background()); err != nil || w.Code != http.StatusOK {
				t.Fatal("authenticated owner rejected after native pool changed source port", err)
			}
			select {
			case <-s.done:
			default:
				t.Fatal("migrated request did not release its session")
			}
		})
	}
}

func testFiniteCancelInterruptsBlockedRead(t *testing.T, version string) {
	t.Helper()
	h, s := testFiniteSession(t)
	s.version = version
	ctx, cancel := context.WithCancel(context.Background())
	r := httptest.NewRequest("GET", "https://localhost/", nil).WithContext(ctx)
	r.ProtoMajor = 2
	r.RemoteAddr = s.peer
	r.Header.Set("X-Naivefox-Finite", version)
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

func TestFiniteUploadPrefixBeforeRequestEOF(t *testing.T) {
	for _, version := range []string{"2", "3"} {
		t.Run(version, func(t *testing.T) { testFiniteUploadPrefixBeforeRequestEOF(t, version) })
	}
}

func testFiniteUploadPrefixBeforeRequestEOF(t *testing.T, version string) {
	t.Helper()
	h, s := testFiniteSession(t)
	s.version = version
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	body, producer := io.Pipe()
	defer body.Close()
	defer producer.Close()
	prefix := []byte("prefix before request EOF")
	tail := bytes.Repeat([]byte("x"), finiteBytes-len(prefix))
	r := httptest.NewRequest("POST", "https://localhost/", body).WithContext(ctx)
	r.ProtoMajor = 2
	r.ContentLength = finiteBytes
	r.RemoteAddr = s.peer
	r.Header.Set("X-Naivefox-Finite", version)
	r.Header.Set("X-Naivefox-Operation", "up")
	r.Header.Set("X-Naivefox-Session", s.id)
	r.Header.Set("X-Naivefox-Sequence", "0")
	w := httptest.NewRecorder()
	finished := make(chan error, 1)
	go func() { finished <- h.serveFinite(w, r, context.Background()) }()
	release := make(chan struct{})
	go func() {
		if _, err := producer.Write(prefix); err != nil {
			return
		}
		select {
		case <-release:
		case <-ctx.Done():
			return
		}
		producer.Write(tail)
		producer.Close()
	}()
	gotPrefix := make([]byte, len(prefix))
	gotTail := make([]byte, len(tail))
	prefixRead := make(chan error, 1)
	tailRead := make(chan error, 1)
	go func() {
		_, err := io.ReadFull(s.upIn, gotPrefix)
		prefixRead <- err
		_, err = io.ReadFull(s.upIn, gotTail)
		tailRead <- err
	}()
	select {
	case err := <-prefixRead:
		if err != nil || !bytes.Equal(gotPrefix, prefix) {
			t.Fatal("prefix forwarding failed", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("upload waited for the withheld request suffix")
	}
	close(release)
	select {
	case err := <-finished:
		if err != nil || w.Code != 200 || w.Header().Get("X-Naivefox-Finite") != version {
			t.Fatal("streaming upload did not complete normally", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("streaming upload did not finish")
	}
	if err := <-tailRead; err != nil || !bytes.Equal(gotTail, tail) {
		t.Fatal("streaming upload tail corrupted", err)
	}
}

func TestFiniteStreamingUploadRejectsLengthMismatch(t *testing.T) {
	for _, version := range []string{"2", "3"} {
		t.Run(version, func(t *testing.T) { testFiniteStreamingUploadRejectsLengthMismatch(t, version) })
	}
}

func testFiniteStreamingUploadRejectsLengthMismatch(t *testing.T, version string) {
	t.Helper()
	for _, lengths := range [][2]int{{3, 4}, {5, 4}, {finiteBytes + 1, finiteBytes}} {
		t.Run(strconv.Itoa(lengths[0]), func(t *testing.T) {
			h, s := testFiniteSession(t)
			s.version = version
			r := httptest.NewRequest("POST", "https://localhost/", bytes.NewReader(bytes.Repeat([]byte("x"), lengths[0])))
			r.ProtoMajor = 2
			r.ContentLength = int64(lengths[1])
			r.RemoteAddr = s.peer
			r.Header.Set("X-Naivefox-Finite", version)
			r.Header.Set("X-Naivefox-Operation", "up")
			r.Header.Set("X-Naivefox-Session", s.id)
			r.Header.Set("X-Naivefox-Sequence", "0")
			drained := make(chan int64, 1)
			go func() {
				n, _ := io.Copy(io.Discard, s.upIn)
				drained <- n
			}()
			if err := h.serveFinite(httptest.NewRecorder(), r, context.Background()); err == nil {
				t.Fatal("accepted streaming body length mismatch")
			}
			select {
			case n := <-drained:
				if n > finiteBytes {
					t.Fatal("forwarded more than the body bound")
				}
			case <-time.After(2 * time.Second):
				t.Fatal("malformed streaming body did not close the session")
			}
		})
	}
}

type finiteCompletionWriter struct {
	*httptest.ResponseRecorder
	cancel context.CancelFunc
	closed <-chan struct{}
}

func (w *finiteCompletionWriter) Write(body []byte) (int, error) {
	n, err := w.ResponseRecorder.Write(body)
	w.cancel()
	select {
	case <-w.closed:
	case <-time.After(50 * time.Millisecond):
	}
	return n, err
}

func TestFiniteCompletedResponseContextDoesNotCloseSession(t *testing.T) {
	h, s := testFiniteSession(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	body := []byte("a completed response is not tunnel EOF")
	go func() { s.downOut.Write(body) }()
	r := httptest.NewRequest("GET", "https://localhost/", nil).WithContext(ctx)
	r.ProtoMajor = 2
	r.RemoteAddr = s.peer
	r.Header.Set("X-Naivefox-Finite", "1")
	r.Header.Set("X-Naivefox-Operation", "down")
	r.Header.Set("X-Naivefox-Session", s.id)
	r.Header.Set("X-Naivefox-Sequence", "0")
	w := &finiteCompletionWriter{httptest.NewRecorder(), cancel, s.done}
	if err := h.serveFinite(w, r, context.Background()); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(w.Body.Bytes(), body) {
		t.Fatal("finite response body corrupted")
	}
	select {
	case <-s.done:
		t.Fatal("completed response context cancelled the logical tunnel")
	default:
	}
}

type finiteFlushWriter struct {
	*httptest.ResponseRecorder
	flushed chan int
}

func (w *finiteFlushWriter) Flush() {
	w.ResponseRecorder.Flush()
	select {
	case w.flushed <- w.Body.Len():
	default:
	}
}

func budgetedRequest(s *finiteSession, seq int, ctx context.Context) *http.Request {
	r := httptest.NewRequest("GET", "https://localhost/", nil).WithContext(ctx)
	r.ProtoMajor = 2
	r.RemoteAddr = s.peer
	r.Header.Set("X-Naivefox-Finite", "3")
	r.Header.Set("X-Naivefox-Operation", "down")
	r.Header.Set("X-Naivefox-Session", s.id)
	r.Header.Set("X-Naivefox-Sequence", strconv.Itoa(seq))
	return r
}

func TestFiniteBudgetedPrefixRotationAndEOF(t *testing.T) {
	h, s := testFiniteSession(t)
	s.version = "3"
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	prefix := []byte("flushed before the withheld suffix")
	payload := append(append([]byte(nil), prefix...), bytes.Repeat([]byte("x"), finiteBytes+13-len(prefix))...)
	release := make(chan struct{})
	producer := make(chan error, 1)
	go func() {
		if _, err := s.downOut.Write(prefix); err != nil {
			producer <- err
			return
		}
		select {
		case <-release:
		case <-ctx.Done():
			producer <- ctx.Err()
			return
		}
		_, err := s.downOut.Write(payload[len(prefix):])
		s.downOut.CloseWithError(err)
		producer <- err
	}()
	w := &finiteFlushWriter{httptest.NewRecorder(), make(chan int, 1)}
	finished := make(chan error, 1)
	go func() { finished <- h.serveFinite(w, budgetedRequest(s, 0, ctx), context.Background()) }()
	select {
	case n := <-w.flushed:
		if n != len(prefix) || !bytes.Equal(w.Body.Bytes(), prefix) {
			t.Fatal("prefix was not flushed immediately")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("budgeted response waited for target suffix")
	}
	select {
	case <-finished:
		t.Fatal("budgeted response ended after a short first read")
	default:
	}
	close(release)
	select {
	case err := <-finished:
		if err != nil || w.Code != 200 || w.Body.Len() != finiteBytes || !bytes.Equal(w.Body.Bytes(), payload[:finiteBytes]) {
			t.Fatal("budgeted response did not end at its exact bound", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("full budget waited for target EOF")
	}
	if w.Header().Get("Content-Length") != "" {
		t.Fatal("budgeted response fabricated a content length")
	}
	tail := httptest.NewRecorder()
	if err := h.serveFinite(tail, budgetedRequest(s, 1, ctx), context.Background()); err != nil || tail.Code != 200 || !bytes.Equal(tail.Body.Bytes(), payload[finiteBytes:]) {
		t.Fatal("short final response corrupted", err)
	}
	if err := <-producer; err != nil {
		t.Fatal(err)
	}
	eof := httptest.NewRecorder()
	if err := h.serveFinite(eof, budgetedRequest(s, 2, ctx), context.Background()); err != nil || eof.Code != 204 {
		t.Fatal("missing budgeted EOF", err)
	}
}

func finiteAbortResult(fn func() error) (result any) {
	defer func() {
		if recovered := recover(); recovered != nil {
			result = recovered
		}
	}()
	return fn()
}

func TestFiniteBudgetedCancellationAbortsPartialResponse(t *testing.T) {
	h, s := testFiniteSession(t)
	s.version = "3"
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	w := &finiteFlushWriter{httptest.NewRecorder(), make(chan int, 1)}
	finished := make(chan any, 1)
	go func() {
		finished <- finiteAbortResult(func() error {
			return h.serveFinite(w, budgetedRequest(s, 0, ctx), context.Background())
		})
	}()
	go func() { s.downOut.Write([]byte("partial")) }()
	select {
	case <-w.flushed:
	case <-time.After(time.Second):
		t.Fatal("no partial response")
	}
	cancel()
	select {
	case err := <-finished:
		if err != http.ErrAbortHandler {
			t.Fatal("partial response did not abort HTTP stream", err)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled response stayed blocked")
	}
	select {
	case <-s.done:
	default:
		t.Fatal("cancelled logical tunnel remained open")
	}
}

type finiteBrokenWriter struct{ *httptest.ResponseRecorder }

func (w *finiteBrokenWriter) Write([]byte) (int, error) { return 0, io.ErrClosedPipe }

func TestFiniteBudgetedWriteFailureAbortsStream(t *testing.T) {
	h, s := testFiniteSession(t)
	s.version = "3"
	go func() { s.downOut.Write([]byte("unwritable")) }()
	err := finiteAbortResult(func() error {
		return h.serveFinite(&finiteBrokenWriter{httptest.NewRecorder()}, budgetedRequest(s, 0, context.Background()), context.Background())
	})
	if err != http.ErrAbortHandler {
		t.Fatal("write failure completed as a valid response", err)
	}
	select {
	case <-s.done:
	default:
		t.Fatal("write failure retained session")
	}
}

func TestFiniteBudgetedCompletionKeepsSession(t *testing.T) {
	h, s := testFiniteSession(t)
	s.version = "3"
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	body := bytes.Repeat([]byte("x"), finiteBytes)
	go func() { s.downOut.Write(body) }()
	w := &finiteCompletionWriter{httptest.NewRecorder(), cancel, s.done}
	if err := h.serveFinite(w, budgetedRequest(s, 0, ctx), context.Background()); err != nil || !bytes.Equal(w.Body.Bytes(), body) {
		t.Fatal("completed budgeted response failed", err)
	}
	select {
	case <-s.done:
		t.Fatal("completed budgeted response cancelled session")
	default:
	}
}

func TestFiniteBudgetedPartialErrorResetsRealH2Response(t *testing.T) {
	_, s := testFiniteSession(t)
	s.version = "3"
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		finiteHeaders(w, s, 0)
		if err := s.budgetedDownload(w, r, 0); err != nil {
			http.Error(w, "finite failure", http.StatusBadGateway)
		}
	}))
	server.EnableHTTP2 = true
	server.StartTLS()
	defer server.Close()
	client := server.Client()
	client.Timeout = 2 * time.Second
	prefix := []byte("usable bytes before target error")
	go func() { s.downOut.Write(prefix) }()
	response, err := client.Get(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.ProtoMajor != 2 || response.StatusCode != http.StatusOK {
		t.Fatal("test did not establish a successful H2 stream")
	}
	got := make([]byte, len(prefix))
	if _, err := io.ReadFull(response.Body, got); err != nil || !bytes.Equal(got, prefix) {
		t.Fatal("streaming prefix was unavailable", err)
	}
	s.downOut.CloseWithError(io.ErrUnexpectedEOF)
	if _, err := io.ReadAll(response.Body); err == nil || err == io.EOF {
		t.Fatal("truncated response ended as a successful H2 body")
	}
	select {
	case <-s.done:
	default:
		t.Fatal("target failure did not close the session")
	}
}
