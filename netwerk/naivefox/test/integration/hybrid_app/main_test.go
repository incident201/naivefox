package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"hash"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

type appFixture struct {
	t       *testing.T
	backend *backend
	server  *httptest.Server
	client  *http.Client
}

func newAppFixture(t *testing.T) *appFixture {
	t.Helper()
	b, err := newBackend(filepath.Join(t.TempDir(), "stats.json"), ".")
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewTLSServer(b)
	client := server.Client()
	client.Jar, _ = cookiejar.New(nil)
	client.Timeout = 5 * time.Second
	origin, _ := url.Parse(server.URL)
	client.Jar.SetCookies(origin, []*http.Cookie{{Name: "app_session", Value: strings.Repeat("a", 64), Path: "/", Secure: true}})
	f := &appFixture{t, b, server, client}
	t.Cleanup(func() { b.Close(); server.Close() })
	return f
}

func (f *appFixture) bootstrap() {
	f.fetchAssets()
	f.bootstrapAPI()
}

func (f *appFixture) fetchAssets() {
	f.t.Helper()
	for _, asset := range f.backend.assetInventory() {
		response, err := f.client.Get(f.server.URL + asset.Path)
		if err != nil {
			f.t.Fatal(err)
		}
		body, err := io.ReadAll(response.Body)
		response.Body.Close()
		digest := sha256.Sum256(body)
		if err != nil || response.StatusCode != 200 || len(body) != asset.Bytes || hex.EncodeToString(digest[:]) != asset.SHA256 {
			f.t.Fatal("immutable asset response")
		}
	}
}

func (f *appFixture) bootstrapAPI() {
	f.t.Helper()
	for round := range 20 {
		body, err := json.Marshal(map[string]any{"cursor": round * 64, "preferences": map[string]any{"order": "ascending", "page_size": 64}, "manifest_sha256": f.backend.manifestSHA})
		if err != nil {
			f.t.Fatal(err)
		}
		path := f.server.URL + "/app/api/bootstrap/" + strconv.Itoa(round)
		response, err := f.client.Post(path, "application/json", bytes.NewReader(body))
		if err != nil {
			f.t.Fatal(err)
		}
		io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if response.StatusCode != 200 {
			f.t.Fatal("semantic POST")
		}
		response, err = f.client.Get(path)
		if err != nil {
			f.t.Fatal(err)
		}
		var page struct {
			Cursor  int             `json:"cursor"`
			Records []catalogRecord `json:"records"`
			Next    int             `json:"next_cursor"`
			SHA     string          `json:"manifest_sha256"`
		}
		err = json.NewDecoder(response.Body).Decode(&page)
		response.Body.Close()
		if response.StatusCode != 200 || err != nil || page.Cursor != round*64 || page.Next != (round+1)*64 || page.SHA != f.backend.manifestSHA || len(page.Records) != 64 {
			f.t.Fatal("semantic GET")
		}
		for i, record := range page.Records {
			if record != catalog(round)[i] {
				f.t.Fatal("catalog content")
			}
		}
	}
}

func (f *appFixture) dial() (*websocket.Conn, *http.Response, error) {
	dialer := websocket.Dialer{TLSClientConfig: f.client.Transport.(*http.Transport).TLSClientConfig.Clone(), Jar: f.client.Jar, Subprotocols: []string{appProtocol}}
	return dialer.Dial("wss"+strings.TrimPrefix(f.server.URL, "https")+"/api/realtime", http.Header{"Origin": []string{f.server.URL}})
}

func writeControl(t *testing.T, conn *websocket.Conn, value control) {
	t.Helper()
	conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
	if err := conn.WriteJSON(value); err != nil {
		t.Fatal(err)
	}
}

func readMessage(t *testing.T, conn *websocket.Conn) (int, []byte) {
	t.Helper()
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	kind, body, err := conn.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	return kind, body
}

type clientJob struct {
	spec                         jobSpec
	ready                        bool
	credit, uploaded, downloaded uint32
	fin                          bool
	digest                       hash.Hash
}

func runJobs(t *testing.T, conn *websocket.Conn, manifest appManifest, ids []uint32) {
	t.Helper()
	jobs := make(map[uint32]*clientJob)
	for _, id := range ids {
		jobs[id] = &clientJob{spec: manifest.Jobs[id-1], digest: sha256.New()}
	}
	if len(ids) == 4 {
		writeControl(t, conn, control{Op: "open_batch", IDs: ids})
	} else {
		spec := jobs[ids[0]].spec
		writeControl(t, conn, control{Op: "open", ID: spec.ID, Kind: spec.Kind, Bytes: spec.Bytes})
	}
	for len(jobs) > 0 {
		for _, id := range ids {
			job := jobs[id]
			if job == nil || !job.ready || job.spec.Kind == "download" || job.fin {
				continue
			}
			for job.uploaded < job.spec.Bytes {
				length := min(uint32(chunkBytes), job.spec.Bytes-job.uploaded)
				if job.credit < length {
					break
				}
				payload := make([]byte, length)
				fillPayload(payload, id, job.uploaded)
				conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
				if err := conn.WriteMessage(websocket.BinaryMessage, dataMessage(id, job.uploaded, payload)); err != nil {
					t.Fatal(err)
				}
				job.credit -= length
				job.uploaded += length
			}
			if job.uploaded == job.spec.Bytes {
				writeControl(t, conn, control{Op: "fin", ID: id, Bytes: job.spec.Bytes, SHA256: job.spec.SHA256})
				job.fin = true
			}
		}
		kind, body := readMessage(t, conn)
		if kind == websocket.TextMessage {
			var message control
			if decodeJSON(body, &message) != nil {
				t.Fatal("server control")
			}
			job := jobs[message.ID]
			if job == nil {
				t.Fatal("unknown server job")
			}
			switch message.Op {
			case "ready":
				if job.ready || message.Bytes != job.spec.Bytes || message.Kind != job.spec.Kind || message.Credit != creditWindow {
					t.Fatal("ready contract")
				}
				job.ready = true
				job.credit = message.Credit
			case "credit":
				if message.Bytes == 0 || message.Bytes > creditWindow-job.credit {
					t.Fatal("server credit overflow")
				}
				job.credit += message.Bytes
			case "complete":
				if message.Bytes != job.spec.Bytes || message.SHA256 != job.spec.SHA256 {
					t.Fatal("completion hash")
				}
				if job.spec.Kind != "upload" && (job.downloaded != job.spec.Bytes || hex.EncodeToString(job.digest.Sum(nil)) != job.spec.SHA256) {
					t.Fatal("download integrity")
				}
				if job.spec.Kind != "download" && job.uploaded != job.spec.Bytes {
					t.Fatal("upload completeness")
				}
				writeControl(t, conn, control{Op: "done", ID: job.spec.ID, Bytes: job.spec.Bytes, SHA256: job.spec.SHA256})
				delete(jobs, job.spec.ID)
			default:
				t.Fatal("unexpected server control")
			}
		} else {
			if kind != websocket.BinaryMessage || len(body) < 17 || body[0] != 1 || body[1] != 0 || body[2] != 0 || body[3] != 0 {
				t.Fatal("server data framing")
			}
			id, offset, length := binary.BigEndian.Uint32(body[4:8]), binary.BigEndian.Uint32(body[8:12]), binary.BigEndian.Uint32(body[12:16])
			job := jobs[id]
			if job == nil || !job.ready || offset != job.downloaded || length != uint32(len(body)-16) || length > chunkBytes || length > job.spec.Bytes-job.downloaded {
				t.Fatal("server data bounds")
			}
			expected := make([]byte, length)
			fillPayload(expected, id, offset)
			if !bytes.Equal(body[16:], expected) {
				t.Fatal("generated payload")
			}
			job.digest.Write(body[16:])
			job.downloaded += length
			writeControl(t, conn, control{Op: "credit", ID: id, Bytes: length})
		}
	}
}

func waitConnectionStats(t *testing.T, b *backend) connectionStats {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b.mu.Lock()
		if len(b.stats.Connections) > 0 {
			result := b.stats.Connections[0]
			b.mu.Unlock()
			return result
		}
		b.mu.Unlock()
		time.Sleep(time.Millisecond)
	}
	t.Fatal("connection did not terminate")
	return connectionStats{}
}

func TestMatchedApplicationWholeManifest(t *testing.T) {
	f := newAppFixture(t)
	f.bootstrap()
	conn, _, err := f.dial()
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	for _, ids := range [][]uint32{{1}, {2}, {3, 4, 5, 6}, {7}, {8}, {9}, {10}, {11}} {
		runJobs(t, conn, f.backend.manifest, ids)
	}
	if err := conn.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(1000, ""), time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, _, err = conn.ReadMessage()
	if !websocket.IsCloseError(err, 1000) {
		t.Fatalf("normal application close: %v", err)
	}
	stats := waitConnectionStats(t, f.backend)
	if !stats.NormalClose || stats.WSOpened != 1 || stats.WSClosed != 1 || stats.BootstrapPairs != 20 || stats.PeakJobs != 4 || stats.ParallelBatches != 1 || stats.ParallelJobCount != 4 || len(stats.Jobs) != 11 {
		t.Fatal("workload admission")
	}
	if stats.DataBytesIn != stats.ExpectedDataBytesIn || stats.DataBytesOut != stats.ExpectedDataBytesOut || stats.DataBytesIn != 1069056 || stats.DataBytesOut != 10506240 {
		t.Fatal("useful workload totals")
	}
	if stats.DataMessagesIn != 21 || stats.DataMessagesOut != 165 || stats.ControlMessagesIn != 190 || stats.ControlMessagesOut != 43 {
		t.Fatalf("message counts: in=%d/%d out=%d/%d", stats.DataMessagesIn, stats.ControlMessagesIn, stats.DataMessagesOut, stats.ControlMessagesOut)
	}
	for _, report := range stats.Jobs {
		if !report.Verified || report.SHA256 != f.backend.manifest.Jobs[report.ID-1].SHA256 {
			t.Fatal("job verification")
		}
	}
	latestStart, earliestEnd := float64(0), float64(1e30)
	for _, report := range stats.Jobs {
		if report.ID >= 3 && report.ID <= 6 {
			latestStart = max(latestStart, report.FirstSendMS)
			earliestEnd = min(earliestEnd, report.LastSendMS)
		}
	}
	if latestStart >= earliestEnd {
		t.Fatal("parallel dataset sending did not overlap")
	}
	f.backend.mu.Lock()
	defer f.backend.mu.Unlock()
	if f.backend.stats.APIPosts != 20 || f.backend.stats.APIGets != 20 || f.backend.stats.CatalogRecords != 1280 || len(f.backend.stats.API) != 40 || f.backend.stats.BootstrapCompleted != 1 || f.backend.stats.NormalCloses != 1 || f.backend.stats.Rejected != 0 {
		t.Fatal("semantic bootstrap accounting")
	}
	for _, observation := range f.backend.stats.API {
		if len(observation.RequestSHA256) != 64 || len(observation.ResponseSHA256) != 64 || observation.ResponseBytes == 0 || observation.AssetCookieHash != stats.AssetCookieHash {
			t.Fatal("response hash inventory")
		}
	}
	group := f.backend.stats.AssetGroups[stats.AssetCookieHash]
	if group == nil || len(group.Responses) != 6 || f.backend.stats.AssetFailures != 0 {
		t.Fatal("application asset association")
	}
	for _, response := range group.Responses {
		if response.Requests != 1 || response.Completed != 1 || response.WrittenBytes != uint64(response.Bytes) {
			t.Fatal("actual asset delivery")
		}
	}
	if path := os.Getenv("NFB_TEST_BACKEND_TRACE"); path != "" {
		if err := writeAtomicJSON(path, f.backend.stats); err != nil {
			t.Fatal(err)
		}
	}
}

func TestWebSocketOpenIsPublishedBeforeAnyJobs(t *testing.T) {
	f := newAppFixture(t)
	f.bootstrap()
	conn, _, err := f.dial()
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		body, err := os.ReadFile(f.backend.statsPath)
		if err == nil {
			var stats backendStats
			if json.Unmarshal(body, &stats) != nil {
				t.Fatal("partially published stats")
			}
			if stats.WSOpened == 1 {
				if stats.WSClosed != 0 || len(stats.Connections) != 0 || stats.BootstrapCompleted != 1 || stats.APIPosts != 20 || stats.APIGets != 20 {
					t.Fatal("open routing proof includes completed workload")
				}
				return
			}
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("open websocket was not published before active jobs")
}

func TestBootstrapBeforeWebSocketAndCookieBinding(t *testing.T) {
	f := newAppFixture(t)
	if conn, response, err := f.dial(); err == nil {
		conn.Close()
		t.Fatal("early websocket accepted")
	} else if response == nil || response.StatusCode != 400 {
		t.Fatal("unexpected early rejection")
	}
	f.bootstrap()
	f.client.Jar, _ = cookiejar.New(nil)
	if conn, _, err := f.dial(); err == nil {
		conn.Close()
		t.Fatal("websocket accepted without bootstrap cookie")
	}
}

func TestCreditWindowStopsGenerationUntilDelivery(t *testing.T) {
	f := newAppFixture(t)
	f.bootstrap()
	conn, _, err := f.dial()
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	spec := f.backend.manifest.Jobs[0]
	writeControl(t, conn, control{Op: "open", ID: 1, Kind: spec.Kind, Bytes: spec.Bytes})
	kind, body := readMessage(t, conn)
	var ready control
	if kind != websocket.TextMessage || decodeJSON(body, &ready) != nil || ready.Op != "ready" {
		t.Fatal("ready")
	}
	for range creditWindow / chunkBytes {
		kind, body = readMessage(t, conn)
		if kind != websocket.BinaryMessage || len(body) != chunkBytes+16 {
			t.Fatal("full credit prefix")
		}
	}
	read := make(chan int, 1)
	go func() {
		kind, body, err := conn.ReadMessage()
		if err != nil || kind != websocket.BinaryMessage {
			read <- 0
		} else {
			read <- len(body)
		}
	}()
	select {
	case <-read:
		t.Fatal("server exceeded granted credit")
	case <-time.After(30 * time.Millisecond):
	}
	writeControl(t, conn, control{Op: "credit", ID: 1, Bytes: chunkBytes})
	select {
	case length := <-read:
		if length != chunkBytes+16 {
			t.Fatal("credit did not resume one chunk")
		}
	case <-time.After(time.Second):
		t.Fatal("credit wake stalled")
	}
}

func TestTruncationUnknownDatasetAndCreditFailures(t *testing.T) {
	for _, which := range []string{"truncated", "credit-overflow", "unknown-dataset", "corrupt-data", "offset", "reused-job", "oversize-message", "idle-close"} {
		t.Run(which, func(t *testing.T) {
			f := newAppFixture(t)
			f.bootstrap()
			conn, _, err := f.dial()
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			if which == "idle-close" {
				conn.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(1000, ""), time.Now().Add(time.Second))
			} else if which == "unknown-dataset" {
				writeControl(t, conn, control{Op: "open", ID: 99, Kind: "download", Bytes: 1})
			} else if which == "oversize-message" {
				conn.WriteMessage(websocket.BinaryMessage, make([]byte, chunkBytes+17))
			} else {
				spec := f.backend.manifest.Jobs[1]
				writeControl(t, conn, control{Op: "open", ID: 2, Kind: spec.Kind, Bytes: spec.Bytes})
				readMessage(t, conn)
				switch which {
				case "truncated":
					writeControl(t, conn, control{Op: "fin", ID: 2, Bytes: spec.Bytes, SHA256: spec.SHA256})
				case "credit-overflow":
					writeControl(t, conn, control{Op: "credit", ID: 2, Bytes: 1})
				case "reused-job":
					writeControl(t, conn, control{Op: "open", ID: 2, Kind: spec.Kind, Bytes: spec.Bytes})
				case "corrupt-data", "offset":
					payload := make([]byte, 16)
					fillPayload(payload, 2, 0)
					offset := uint32(0)
					if which == "corrupt-data" {
						payload[0] ^= 1
					} else {
						offset = 1
					}
					conn.WriteMessage(websocket.BinaryMessage, dataMessage(2, offset, payload))
				}
			}
			conn.SetReadDeadline(time.Now().Add(5 * time.Second))
			for {
				_, _, err := conn.ReadMessage()
				if err != nil {
					break
				}
			}
			stats := waitConnectionStats(t, f.backend)
			if stats.NormalClose || stats.Failure == "" {
				t.Fatal("invalid workload admitted")
			}
		})
	}
}

func TestAtomicBatchAndRetiredCreditRemainBounded(t *testing.T) {
	b, err := newBackend("", ".")
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	a := &application{backend: b, start: time.Now(), jobs: make(map[uint32]*activeJob), seen: make(map[uint32]bool), reports: make(map[uint32]*jobStats), retiredCredit: make(map[uint32]uint32)}
	body, _ := json.Marshal(control{Op: "open_batch", IDs: []uint32{3, 4, 5, 6}})
	if err := a.handleControl(body); err != nil || len(a.jobs) != 4 || a.stats.PeakJobs != 4 || len(a.controls) != 4 {
		t.Fatal("batch was not atomic")
	}
	if err := a.handleControl(body); err == nil {
		t.Fatal("duplicate batch accepted")
	}
	extra := b.manifest.Jobs[6]
	body, _ = json.Marshal(control{Op: "open", ID: extra.ID, Kind: extra.Kind, Bytes: extra.Bytes})
	if err := a.handleControl(body); err == nil || len(a.jobs) != 4 {
		t.Fatal("fifth active job admitted")
	}
	job := a.jobs[3]
	job.readySent = true
	job.completeSent = true
	job.outCredit = creditWindow - 4096
	body, _ = json.Marshal(control{Op: "done", ID: 3, Bytes: job.spec.Bytes, SHA256: job.spec.SHA256})
	if err := a.handleControl(body); err != nil {
		t.Fatal(err)
	}
	body, _ = json.Marshal(control{Op: "credit", ID: 3, Bytes: 4096})
	if err := a.handleControl(body); err != nil {
		t.Fatal("valid late delivery credit rejected")
	}
	if err := a.handleControl(body); err == nil {
		t.Fatal("retired credit overflow accepted")
	}
	if len(a.retiredCredit) != 1 || len(a.jobs) != 3 {
		t.Fatal("retirement state bound")
	}
}

func TestCoverAssetsCannotSatisfyApplicationAssetGate(t *testing.T) {
	f := newAppFixture(t)
	f.fetchAssets()
	origin, _ := url.Parse(f.server.URL)
	f.client.Jar.SetCookies(origin, []*http.Cookie{{Name: "app_session", Value: strings.Repeat("b", 64), Path: "/", Secure: true}})
	f.bootstrapAPI()
	if conn, _, err := f.dial(); err == nil {
		conn.Close()
		t.Fatal("cover asset group admitted application websocket")
	}
	f.fetchAssets()
	conn, _, err := f.dial()
	if err != nil {
		t.Fatal("application's own assets did not admit websocket")
	}
	conn.Close()
}

func TestAssetsRemainImmutableAfterProducerFilesChange(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{"site.css", "image.svg", "app.js"} {
		body, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, name), body, 0600); err != nil {
			t.Fatal(err)
		}
	}
	b, err := newBackend("", dir)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	expected := b.assets["/assets/site.css"].descriptor
	if err := os.WriteFile(filepath.Join(dir, "site.css"), []byte("different bytes"), 0600); err != nil {
		t.Fatal(err)
	}
	w := httptest.NewRecorder()
	r := httptest.NewRequest("GET", "https://localhost/assets/site.css", nil)
	b.ServeHTTP(w, r)
	digest := sha256.Sum256(w.Body.Bytes())
	if w.Code != 200 || w.Body.Len() != expected.Bytes || hex.EncodeToString(digest[:]) != expected.SHA256 {
		t.Fatal("asset changed during session")
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	observed := b.stats.AssetGroups["none"].Responses[expected.Path]
	if observed.Completed != 1 || observed.WrittenBytes != uint64(expected.Bytes) || observed.SHA256 != expected.SHA256 {
		t.Fatal("asset delivery inventory")
	}
}
