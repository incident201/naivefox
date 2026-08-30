// Copyright 2026 NaiveFox contributors.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// Diagnostic overlay for the pinned forwardproxy module, not a default server.
package forwardproxy

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	mathrand "math/rand"
	"net"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/caddyserver/caddy/v2/modules/caddyhttp"
)

const finiteBytes = 64 * 1024
const finiteUploads = 2
const finiteDownloads = 4
const finiteMaxSessions = 64
const finiteIdleTimeout = 2 * time.Minute

func finiteNormalizeAuth(r *http.Request) {
	if r.Header.Get("X-Naivefox-Finite") != "" {
		// The finite endpoint is an ordinary origin request, not CONNECT.
		// Reuse the stock credential verifier without forwarding origin auth.
		r.Header.Set("Proxy-Authorization", r.Header.Get("Authorization"))
		r.Header.Del("Authorization")
	}
}

var finiteRegistry = struct {
	sync.Mutex
	sessions map[string]*finiteSession
}{sessions: make(map[string]*finiteSession)}

// Sequencing bounds the number of waiters and rejects replay. Neither byte
// delivery nor response completion depends on a pacing timer or site size.
type finiteLane struct {
	sync.Mutex
	next uint64
	wake chan struct{}
	busy map[uint64]bool
	fin  bool
}

func (l *finiteLane) ordered(ctx context.Context, done <-chan struct{}, seq, window uint64, fn func() error) error {
	l.Lock()
	if l.wake == nil {
		l.wake = make(chan struct{})
		l.busy = make(map[uint64]bool)
	}
	if seq < l.next || seq-l.next >= window || l.busy[seq] {
		l.Unlock()
		return errors.New("invalid finite sequence")
	}
	l.busy[seq] = true
	for seq != l.next {
		wake := l.wake
		l.Unlock()
		select {
		case <-wake:
		case <-ctx.Done():
			l.Lock()
			delete(l.busy, seq)
			l.Unlock()
			return ctx.Err()
		case <-done:
			l.Lock()
			delete(l.busy, seq)
			l.Unlock()
			return io.ErrClosedPipe
		}
		l.Lock()
	}
	l.Unlock()
	err := fn()
	l.Lock()
	delete(l.busy, seq)
	l.next++
	close(l.wake)
	l.wake = make(chan struct{})
	l.Unlock()
	return err
}

type finiteSession struct {
	mu      sync.Mutex
	id      string
	version string
	handler *Handler
	peer    string
	auth    [32]byte
	ctx     context.Context
	cancel  context.CancelFunc
	done    chan struct{}
	closed  bool
	timer   *time.Timer
	target  net.Conn
	upIn    *io.PipeReader
	upOut   *io.PipeWriter
	downIn  *io.PipeReader
	downOut *io.PipeWriter
	up      finiteLane
	down    finiteLane
}

func (s *finiteSession) close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	close(s.done)
	s.cancel()
	if s.timer != nil {
		s.timer.Stop()
	}
	if s.target != nil {
		s.target.Close()
	}
	s.upIn.CloseWithError(io.ErrClosedPipe)
	s.upOut.CloseWithError(io.ErrClosedPipe)
	s.downIn.CloseWithError(io.ErrClosedPipe)
	s.downOut.CloseWithError(io.ErrClosedPipe)
	s.mu.Unlock()
	finiteRegistry.Lock()
	delete(finiteRegistry.sessions, s.id)
	finiteRegistry.Unlock()
}

func (s *finiteSession) touch() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return false
	}
	s.timer.Reset(finiteIdleTimeout)
	return true
}

func (h *Handler) newFinite(ctx context.Context, r *http.Request) (*finiteSession, error) {
	authority := r.Header.Get("X-Naivefox-Target")
	host, port, err := net.SplitHostPort(authority)
	p, portErr := strconv.Atoi(port)
	if err != nil || portErr != nil || host == "" || p < 1 || p > 65535 || len(authority) > 512 {
		return nil, errors.New("invalid finite target authority")
	}
	var token [16]byte
	if _, err := rand.Read(token[:]); err != nil {
		return nil, err
	}
	s := &finiteSession{id: hex.EncodeToString(token[:]), handler: h,
		version: r.Header.Get("X-Naivefox-Finite"),
		peer:    r.RemoteAddr, auth: sha256.Sum256([]byte(r.Header.Get("Proxy-Authorization"))),
		done: make(chan struct{})}
	s.ctx, s.cancel = context.WithCancel(ctx)
	s.upIn, s.upOut = io.Pipe()
	s.downIn, s.downOut = io.Pipe()
	finiteRegistry.Lock()
	if len(finiteRegistry.sessions) >= finiteMaxSessions {
		finiteRegistry.Unlock()
		s.cancel()
		return nil, errors.New("finite session capacity exceeded")
	}
	finiteRegistry.sessions[s.id] = s
	finiteRegistry.Unlock()
	s.timer = time.AfterFunc(finiteIdleTimeout, s.close)
	// Preserve stock Fast Open: the open response does not wait for target
	// DNS, ACL evaluation or dial. A later failure closes the finite session.
	go func() {
		conn, err := h.dialContextCheckACL(s.ctx, "tcp", authority)
		if err != nil || conn == nil {
			s.close()
			return
		}
		s.mu.Lock()
		if s.closed {
			s.mu.Unlock()
			conn.Close()
			return
		}
		s.target = conn
		s.mu.Unlock()
		// Keep exactly the existing Variant-1 codec instance per direction.
		// io.Pipe adds no unbounded queue, and exchange boundaries do not
		// reset padding or imply a record boundary.
		err = dualStream(conn, s.upIn, s.downOut, true)
		if errors.Is(err, io.EOF) {
			err = nil
		}
		s.downOut.CloseWithError(err)
	}()
	return s, nil
}

func finiteHeaders(w http.ResponseWriter, s *finiteSession, seq uint64) {
	w.Header().Set("X-Naivefox-Finite", s.version)
	w.Header().Set("X-Naivefox-Session", s.id)
	w.Header().Set("X-Naivefox-Sequence", strconv.FormatUint(seq, 10))
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/octet-stream")
}

func finitePadding() string {
	padding := make([]byte, mathrand.Intn(32)+30)
	bits := mathrand.Uint64()
	for i := 0; i < 16; i++ {
		padding[i] = "!#$()+<>?@[]^`{}"[bits&15]
		bits >>= 4
	}
	for i := 16; i < len(padding); i++ {
		padding[i] = '~'
	}
	return string(padding)
}

func (h *Handler) serveFinite(w http.ResponseWriter, r *http.Request, ctx context.Context) error {
	bad := func() error {
		return caddyhttp.Error(http.StatusBadRequest, errors.New("invalid finite exchange"))
	}
	version := r.Header.Get("X-Naivefox-Finite")
	if r.ProtoMajor != 2 || (version != "1" && version != "2") {
		return bad()
	}
	op := r.Header.Get("X-Naivefox-Operation")
	seqText := r.Header.Get("X-Naivefox-Sequence")
	seq, err := strconv.ParseUint(seqText, 10, 64)
	if err != nil || strconv.FormatUint(seq, 10) != seqText ||
		(op == "down" && r.Method != "GET") || (op != "down" && r.Method != "POST") {
		return bad()
	}
	if r.ContentLength < 0 || r.ContentLength > finiteBytes ||
		(op != "up" && r.ContentLength != 0) {
		return bad()
	}
	if op == "open" {
		if seq != 0 || r.Header.Get("X-Naivefox-Session") != "" || r.Header.Get("Padding") == "" {
			return bad()
		}
		s, err := h.newFinite(ctx, r)
		if err != nil {
			return bad()
		}
		finiteHeaders(w, s, seq)
		w.Header().Set("Padding", finitePadding())
		w.Header().Set("Content-Length", "0")
		w.WriteHeader(http.StatusOK)
		return nil
	}
	if op != "up" && op != "down" && op != "close" {
		return bad()
	}
	finiteRegistry.Lock()
	s := finiteRegistry.sessions[r.Header.Get("X-Naivefox-Session")]
	finiteRegistry.Unlock()
	if s == nil || s.version != version || s.handler != h || s.peer != r.RemoteAddr ||
		s.auth != sha256.Sum256([]byte(r.Header.Get("Proxy-Authorization"))) || !s.touch() {
		return caddyhttp.Error(http.StatusConflict, errors.New("finite session unavailable"))
	}
	finiteHeaders(w, s, seq)
	if op == "close" {
		s.close()
		w.Header().Set("Content-Length", "0")
		w.WriteHeader(http.StatusOK)
		return nil
	}
	// Cancellation must interrupt an active pipe read/write too, not only a
	// sequence waiter. Stop watching before a successful HTTP response finishes;
	// its context cancellation must not close the logical tunnel.
	stop := context.AfterFunc(r.Context(), s.close)
	defer stop()
	if op == "up" {
		fin := r.Header.Get("X-Naivefox-Fin")
		if (fin != "" && fin != "1") || (fin == "1" && r.ContentLength != 0) {
			return bad()
		}
		err = s.up.ordered(r.Context(), s.done, seq, finiteUploads, func() error {
			if s.up.fin {
				return errors.New("upload after FIN")
			}
			if s.version == "2" && fin != "1" {
				if r.ContentLength == 0 {
					return errors.New("empty upload without FIN")
				}
				n, copyErr := io.Copy(s.upOut, http.MaxBytesReader(w, r.Body, finiteBytes))
				if copyErr != nil || n != r.ContentLength {
					return errors.New("invalid streaming upload body")
				}
				return nil
			}
			body, readErr := io.ReadAll(http.MaxBytesReader(w, r.Body, finiteBytes))
			if readErr != nil || int64(len(body)) != r.ContentLength {
				return errors.New("invalid upload body")
			}
			if fin == "1" {
				s.up.fin = true
				return s.upOut.Close()
			}
			if len(body) == 0 {
				return errors.New("empty upload without FIN")
			}
			_, writeErr := s.upOut.Write(body)
			return writeErr
		})
		stop()
		if err == nil {
			w.Header().Set("Content-Length", "0")
			w.WriteHeader(http.StatusOK)
			return nil
		}
	} else {
		var body []byte
		var eof bool
		err = s.down.ordered(r.Context(), s.done, seq, finiteDownloads, func() error {
			body = make([]byte, finiteBytes)
			n, readErr := s.downIn.Read(body)
			body = body[:n]
			eof = n == 0 && errors.Is(readErr, io.EOF)
			if n > 0 || eof {
				return nil
			}
			return readErr
		})
		stop()
		if err == nil {
			if eof {
				w.WriteHeader(http.StatusNoContent)
				return nil
			}
			if len(body) == 0 {
				s.close()
				return bad()
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(body)))
			w.WriteHeader(http.StatusOK)
			_, err = w.Write(body)
			if err != nil {
				s.close()
			}
			return err
		}
	}
	s.close()
	return caddyhttp.Error(http.StatusBadGateway, errors.New("finite stream failed"))
}
