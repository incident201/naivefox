package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

type packetGap struct {
	sequence uint64
	until    time.Time
	receipts map[uint64]bool
	tailLost bool
}

type fixture struct {
	mode           string
	h1, h2         [2]*http.Transport
	originProtocol string
	next           atomic.Uint64
	mu             sync.Mutex
	cookies        map[string]string
	stats          map[string]int
	faults         map[string]bool
	packetGaps     map[string]*packetGap
}

func (f *fixture) count(key string) { f.mu.Lock(); f.stats[key]++; f.mu.Unlock() }
func (f *fixture) round(r *http.Request) (*http.Response, error) {
	transports := f.h1
	if f.originProtocol == "h2" && !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
		transports = f.h2
	}
	response, err := transports[f.next.Add(1)%2].RoundTrip(r)
	if err == nil {
		f.count("origin_" + response.Proto)
	}
	return response, err
}

func (f *fixture) once(key string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.faults == nil {
		f.faults = make(map[string]bool)
	}
	if f.faults[key] || len(f.faults) >= 256 {
		return false
	}
	f.faults[key] = true
	return true
}
func (f *fixture) gapUpload(r *http.Request, body []byte) (*http.Response, error) {
	prefix, suffix, ok := strings.Cut(r.URL.Path, "/upload/")
	sequence, err := strconv.ParseUint(suffix, 10, 64)
	if !ok || err != nil {
		return nil, errors.New("invalid packet upload path")
	}
	f.mu.Lock()
	if f.packetGaps == nil {
		f.packetGaps = make(map[string]*packetGap)
	}
	gap := f.packetGaps[prefix]
	first := false
	if gap == nil && len(body) >= 32768 && len(f.packetGaps) < 32 {
		gap = &packetGap{sequence: sequence, until: time.Now().Add(1200 * time.Millisecond), receipts: make(map[uint64]bool)}
		f.packetGaps[prefix] = gap
		first = true
		f.stats["packet_gap_held"]++
	}
	if gap == nil || sequence < gap.sequence {
		f.mu.Unlock()
		return f.round(r)
	}
	if gap.receipts[sequence] {
		f.stats["packet_gap_redundant_posts"]++
		f.stats["packet_gap_redundant_bytes"] += len(body)
	}
	if sequence == gap.sequence && !first {
		f.stats["packet_gap_head_retries"]++
	}
	if sequence == gap.sequence+1 && gap.tailLost && !gap.receipts[sequence] {
		f.stats["packet_gap_lost_tail_retries"]++
	}
	if time.Now().Before(gap.until) {
		distance := int(sequence - gap.sequence)
		f.stats["packet_gap_furthest"] = max(f.stats["packet_gap_furthest"], distance)
		if distance >= 8 {
			f.stats["packet_gap_outside_window"]++
		}
	}
	f.mu.Unlock()
	if first {
		timer := time.NewTimer(time.Until(gap.until))
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-r.Context().Done():
			return nil, r.Context().Err()
		}
	}
	response, err := f.round(r)
	if err != nil || response.StatusCode != http.StatusOK {
		return response, err
	}
	reply, err := io.ReadAll(io.LimitReader(response.Body, 49))
	response.Body.Close()
	if err != nil || len(reply) != 48 || binary.BigEndian.Uint64(reply[8:16]) != sequence {
		return nil, errors.New("invalid packet receipt")
	}
	response.Body = io.NopCloser(bytes.NewReader(reply))
	f.mu.Lock()
	drop := first
	if first {
		f.stats["packet_gap_lost_head"]++
	} else if sequence == gap.sequence+1 && !gap.tailLost {
		gap.tailLost = true
		f.stats["packet_gap_lost_tail"]++
		drop = true
	} else if sequence > gap.sequence && sequence < gap.sequence+8 &&
		binary.BigEndian.Uint64(reply[:8]) == gap.sequence {
		if !gap.receipts[sequence] {
			f.stats["packet_gap_receipts"]++
		}
		gap.receipts[sequence] = true
	}
	f.mu.Unlock()
	if drop {
		response.Body.Close()
		return nil, errors.New("injected lost packet receipt")
	}
	return response, nil
}

func (f *fixture) packetRound(r *http.Request) (*http.Response, error) {
	if r.Method != http.MethodPost {
		return f.round(r)
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 65537))
	r.Body.Close()
	if err != nil {
		return nil, err
	}
	r.Body = io.NopCloser(bytes.NewReader(body))
	r.ContentLength = -1
	r.Header.Del("Content-Length")
	f.count("packet_reframed_uploads")
	if f.mode == "packet-gap" && strings.Contains(r.URL.Path, "/upload/") {
		return f.gapUpload(r, body)
	}
	if strings.Contains(r.URL.Path, "/upload/") {
		f.count("packet_uploads")
		if strings.HasSuffix(r.URL.Path, "/2") {
			time.Sleep(25 * time.Millisecond)
			f.count("packet_reordered")
			if f.mode == "packet-tamper" && len(body) > 0 {
				body[0] ^= 1
				f.count("packet_tampered")
			}
		}
	}
	response, err := f.round(r)
	if err != nil {
		return nil, err
	}
	setup := r.URL.Path == "/api/packet" || strings.HasSuffix(r.URL.Path, "/auth")
	if f.mode == "packet-loss" && response.StatusCode == 200 &&
		(setup || strings.HasSuffix(r.URL.Path, "/upload/2")) && f.once("post "+r.URL.Path) {
		_, err := io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if err != nil {
			return nil, err
		}
		f.count("packet_lost_responses")
		return nil, errors.New("injected lost packet response")
	}
	return response, nil
}

type cutBody struct {
	io.Reader
	io.Closer
}

func (f *fixture) RoundTrip(r *http.Request) (*http.Response, error) {
	if strings.HasPrefix(f.mode, "packet-") && strings.HasPrefix(r.URL.Path, "/api/packet") {
		return f.packetRound(r)
	}
	startup := r.URL.Path == "/api/sync" || r.URL.Query().Has("seq")
	if !startup || f.mode != "replay" {
		return f.round(r)
	}
	var body []byte
	var err error
	if r.Body != nil {
		body, err = io.ReadAll(io.LimitReader(r.Body, 4097))
		r.Body.Close()
		if err != nil {
			return nil, err
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
	}
	first, err := f.round(r)
	if err != nil {
		return nil, err
	}
	saved, err := io.ReadAll(io.LimitReader(first.Body, 65537))
	first.Body.Close()
	if err != nil {
		return nil, err
	}
	repeat := r.Clone(r.Context())
	if r.Body != nil {
		repeat.Body = io.NopCloser(bytes.NewReader(body))
	}
	second, err := f.round(repeat)
	if err != nil {
		return nil, err
	}
	replayed, err := io.ReadAll(io.LimitReader(second.Body, 65537))
	second.Body.Close()
	if err != nil {
		return nil, err
	}
	if first.StatusCode != second.StatusCode || sha256.Sum256(saved) != sha256.Sum256(replayed) {
		f.count("replay_mismatch")
		return nil, errors.New("startup replay changed")
	}
	f.count("replays")
	second.Body = io.NopCloser(bytes.NewReader(replayed))
	return second, nil
}

type fragments struct{ io.ReadCloser }

func (r fragments) Read(p []byte) (int, error) {
	if len(p) > 97 {
		p = p[:97]
	}
	return r.ReadCloser.Read(p)
}
func nonce() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}

func main() {
	listen := flag.String("listen", "", "loopback listener")
	origin := flag.String("origin", "", "HTTPS origin")
	cert := flag.String("cert", "", "fixture certificate")
	key := flag.String("key", "", "fixture key")
	ca := flag.String("ca", "", "fixture CA")
	mode := flag.String("mode", "normal", "fault scenario")
	protocol := flag.String("origin-protocol", "h2", "origin startup protocol")
	statsFile := flag.String("stats", "", "aggregate result")
	flag.Parse()
	originURL, err := url.Parse(*origin)
	if err != nil || originURL.Scheme != "https" {
		log.Fatal("invalid origin")
	}
	pem, err := os.ReadFile(*ca)
	if err != nil {
		log.Fatal(err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(pem) {
		log.Fatal("invalid CA")
	}
	f := &fixture{mode: *mode, originProtocol: *protocol, cookies: map[string]string{}, stats: map[string]int{}}
	for i := range 2 {
		dial := (&net.Dialer{Timeout: 5 * time.Second, LocalAddr: &net.TCPAddr{IP: net.ParseIP(fmt.Sprintf("127.0.0.%d", i+2))}}).DialContext
		for _, h2 := range []bool{false, true} {
			t := &http.Transport{DialContext: dial, TLSClientConfig: &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12},
				ForceAttemptHTTP2: h2, DisableCompression: true, MaxConnsPerHost: 32, ResponseHeaderTimeout: 10 * time.Second}
			if !h2 {
				t.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
				f.h1[i] = t
			} else {
				f.h2[i] = t
			}
		}
	}
	var connMu sync.Mutex
	conns := map[net.Conn]bool{}
	proxy := &httputil.ReverseProxy{
		Rewrite: func(r *httputil.ProxyRequest) {
			r.SetURL(originURL)
			r.Out.Host = r.In.Host
			r.Out.Header.Set("CF-Connecting-IP", "198.51.100.10")
			r.Out.Header.Set("X-Forwarded-For", "203.0.113.99")
			r.Out.Header.Set("Accept-Encoding", "br, gzip")
		},
		Transport: f, FlushInterval: -1,
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			f.count("proxy_errors")
			http.Error(w, "fixture upstream failure", 502)
		},
		ModifyResponse: func(r *http.Response) error {
			if f.mode == "packet-loss" && strings.HasSuffix(r.Request.URL.Path, "/download") &&
				r.Request.URL.Query().Get("generation") == "2" && r.StatusCode == 200 &&
				f.once("connection "+r.Request.URL.Path) {
				remote := r.Request.RemoteAddr
				time.AfterFunc(10*time.Millisecond, func() {
					connMu.Lock()
					for connection := range conns {
						if connection.RemoteAddr().String() == remote {
							connection.Close()
							f.count("packet_reset_connections")
							break
						}
					}
					connMu.Unlock()
				})
			}

			if f.mode == "packet-loss" && strings.HasSuffix(r.Request.URL.Path, "/download") &&
				r.StatusCode == 200 && f.once("get "+r.Request.URL.Path) {
				r.Body = cutBody{io.LimitReader(r.Body, 91), r.Body}
				f.count("packet_cut_downloads")
			}
			if r.Request.URL.Path == "/" && r.StatusCode == 200 {
				session := ""
				for _, c := range r.Cookies() {
					if c.Name == "session" {
						session = c.Value
					}
				}
				if session == "" {
					return errors.New("missing origin session")
				}
				value := nonce()
				f.mu.Lock()
				f.cookies[session] = value
				f.mu.Unlock()
				original := append([]string(nil), r.Header.Values("Set-Cookie")...)
				r.Header.Del("Set-Cookie")
				r.Header.Add("Set-Cookie", "edge="+value+"; Secure; HttpOnly; Path=/")
				for _, c := range original {
					r.Header.Add("Set-Cookie", c)
				}
				r.Header.Add("Set-Cookie", "scoped=yes; Secure; Path=/api")
				if f.mode == "cookie-overflow" {
					r.Header.Add("Set-Cookie", "huge="+strings.Repeat("a", 4097))
				}
			}
			if r.Request.URL.Path == "/api/sync" && r.StatusCode == 204 {
				c, _ := r.Request.Cookie("session")
				value := nonce()
				f.mu.Lock()
				f.cookies[c.Value] = value
				f.mu.Unlock()
				r.Header.Add("Set-Cookie", "edge="+value+"; Secure; HttpOnly; Path=/")
				f.count("cookie_updates")
			}
			if r.StatusCode == 101 {
				f.count("websockets")
				r.Header.Add("Set-Cookie", "ws=received; Secure; Path=/")
				return nil
			}
			if r.StatusCode == 200 {
				if !strings.Contains(r.Header.Get("Cache-Control"), "no-transform") {
					return errors.New("trusted origin response lacks no-transform")
				}
				r.Header.Del("Content-Length")
				r.ContentLength = -1
				f.count("length_removed")
				if r.Request.URL.Query().Get("seq") == "0" {
					switch f.mode {
					case "mime":
						r.Header.Set("Content-Type", "text/html")
					case "encoding":
						r.Header.Set("Content-Encoding", "gzip")
					case "truncated", "extra":
						body, e := io.ReadAll(r.Body)
						r.Body.Close()
						if e != nil {
							return e
						}
						if f.mode == "truncated" {
							body = body[:100]
						} else {
							body = append(body, 0)
						}
						r.Body = io.NopCloser(bytes.NewReader(body))
					}
				}
				if f.mode == "snapshot" && strings.HasSuffix(r.Request.URL.Path, ".js") {
					body, e := io.ReadAll(r.Body)
					r.Body.Close()
					if e != nil {
						return e
					}
					body = append(body, []byte("\n/* transformed */")...)
					r.Body = io.NopCloser(bytes.NewReader(body))
				}
				r.Body = fragments{r.Body}
			}
			return nil
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.count("edge_" + r.Proto)
		if strings.HasPrefix(f.mode, "packet-") && r.Header.Get("Upgrade") != "" {
			f.count("packet_rejected_upgrade")
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		if r.URL.Path == "/" {
			switch f.mode {
			case "challenge":
				w.Header().Set("Content-Type", "text/html")
				w.WriteHeader(403)
				io.WriteString(w, "challenge")
				return
			case "redirect":
				http.Redirect(w, r, "/challenge", 302)
				return
			case "rate":
				w.WriteHeader(429)
				return
			case "server-error":
				w.WriteHeader(503)
				return
			}
		} else {
			session, e := r.Cookie("session")
			edge, ee := r.Cookie("edge")
			f.mu.Lock()
			want := ""
			if session != nil {
				want = f.cookies[session.Value]
			}
			f.mu.Unlock()
			if e != nil || ee != nil || want == "" || edge.Value != want {
				f.count("cookie_errors")
				http.Error(w, "cookie mismatch", 400)
				return
			}
			scoped, err := r.Cookie("scoped")
			if (strings.HasPrefix(r.URL.Path, "/api/") && (err != nil || scoped.Value != "yes")) ||
				(!strings.HasPrefix(r.URL.Path, "/api/") && err == nil) {
				f.count("cookie_path_errors")
				w.WriteHeader(400)
				return
			}
		}
		proxy.ServeHTTP(w, r)
	})
	server := &http.Server{Addr: *listen, Handler: handler, ReadHeaderTimeout: 10 * time.Second,
		ConnState: func(c net.Conn, s http.ConnState) {
			connMu.Lock()
			defer connMu.Unlock()
			if s == http.StateClosed {
				delete(conns, c)
			} else {
				conns[c] = true
			}
		}}
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	done := make(chan struct{})
	go func() {
		<-sig
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		server.Shutdown(ctx)
		connMu.Lock()
		for c := range conns {
			c.Close()
		}
		connMu.Unlock()
		f.mu.Lock()
		data, _ := json.Marshal(f.stats)
		f.mu.Unlock()
		os.WriteFile(*statsFile, data, 0600)
		close(done)
	}()
	err = server.ListenAndServeTLS(*cert, *key)
	if !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
	<-done
}
