package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"hash"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
)

//go:embed manifest.json
var manifestBytes []byte

const (
	appProtocol  = "nfbench.app.v1"
	chunkBytes   = 65536
	creditWindow = 524288
	maxJobs      = 4
	maxEvents    = 8
	maxControls  = 16
)

type jobSpec struct {
	ID     uint32 `json:"id"`
	Kind   string `json:"kind"`
	Bytes  uint32 `json:"bytes"`
	SHA256 string `json:"sha256"`
}

type appManifest struct {
	Protocol               string    `json:"protocol"`
	ChunkBytes             int       `json:"chunk_bytes"`
	ReceiveWindow          int       `json:"receive_window"`
	MaxJobs                int       `json:"max_jobs"`
	BootstrapRounds        int       `json:"bootstrap_rounds"`
	CatalogRecordsPerRound int       `json:"catalog_records_per_round"`
	Jobs                   []jobSpec `json:"jobs"`
}

func fillPayload(body []byte, id, offset uint32) {
	for index := range body {
		position := offset + uint32(index)
		body[index] = byte(id*17 + position*31 + (position >> 8))
	}
}

func payloadHash(spec jobSpec) string {
	digest := sha256.New()
	body := make([]byte, chunkBytes)
	for offset := uint32(0); offset < spec.Bytes; {
		length := min(uint32(len(body)), spec.Bytes-offset)
		fillPayload(body[:length], spec.ID, offset)
		digest.Write(body[:length])
		offset += length
	}
	return hex.EncodeToString(digest.Sum(nil))
}

func loadManifest() (appManifest, string, error) {
	var manifest appManifest
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		return manifest, "", err
	}
	if manifest.Protocol != appProtocol || manifest.ChunkBytes != chunkBytes || manifest.ReceiveWindow != creditWindow || manifest.MaxJobs != maxJobs || manifest.BootstrapRounds != 20 || manifest.CatalogRecordsPerRound != 64 || len(manifest.Jobs) != 11 {
		return manifest, "", errors.New("unsupported application manifest")
	}
	for index, spec := range manifest.Jobs {
		expected := uint32(4096)
		kind := "echo"
		switch {
		case index == 0:
			expected, kind = 8*1024*1024, "download"
		case index == 1:
			expected, kind = 1024*1024, "upload"
		case index >= 2 && index <= 5:
			expected, kind = 512*1024, "download"
		}
		if spec.ID != uint32(index+1) || spec.Bytes != expected || spec.Kind != kind || payloadHash(spec) != spec.SHA256 {
			return manifest, "", errors.New("manifest dataset mismatch")
		}
	}
	digest := sha256.Sum256(manifestBytes)
	return manifest, hex.EncodeToString(digest[:]), nil
}

type jobStats struct {
	ID             uint32  `json:"id"`
	Kind           string  `json:"kind"`
	Bytes          uint32  `json:"bytes"`
	Received       uint32  `json:"received"`
	Sent           uint32  `json:"sent"`
	Validated      uint32  `json:"validated"`
	SHA256         string  `json:"sha256"`
	Verified       bool    `json:"verified"`
	FirstReceiveMS float64 `json:"first_receive_ms"`
	LastReceiveMS  float64 `json:"last_receive_ms"`
	FirstSendMS    float64 `json:"first_send_ms"`
	LastSendMS     float64 `json:"last_send_ms"`
	VerifiedMS     float64 `json:"verified_ms"`
}

type connectionStats struct {
	ID                   uint32     `json:"id"`
	BootstrapPairs       int        `json:"bootstrap_pairs"`
	WSOpened             int        `json:"ws_opened"`
	WSClosed             int        `json:"ws_closed"`
	NormalClose          bool       `json:"normal_close"`
	CloseCode            int        `json:"close_code"`
	Failure              string     `json:"failure,omitempty"`
	MessagesIn           uint64     `json:"messages_in"`
	MessagesOut          uint64     `json:"messages_out"`
	DataMessagesIn       uint64     `json:"data_messages_in"`
	DataMessagesOut      uint64     `json:"data_messages_out"`
	ControlMessagesIn    uint64     `json:"control_messages_in"`
	ControlMessagesOut   uint64     `json:"control_messages_out"`
	DataBytesIn          uint64     `json:"data_bytes_in"`
	DataBytesOut         uint64     `json:"data_bytes_out"`
	ExpectedDataBytesIn  uint64     `json:"expected_data_bytes_in"`
	ExpectedDataBytesOut uint64     `json:"expected_data_bytes_out"`
	ParallelBatches      int        `json:"parallel_batches"`
	ParallelJobCount     int        `json:"parallel_job_count"`
	PeakJobs             int        `json:"peak_jobs"`
	OpenOrder            []uint32   `json:"open_order"`
	Jobs                 []jobStats `json:"jobs"`
	AssetCookieHash      string     `json:"asset_cookie_hash"`
}

type assetDescriptor struct {
	Path   string `json:"path"`
	Bytes  int    `json:"bytes"`
	SHA256 string `json:"sha256"`
}

type immutableAsset struct {
	descriptor assetDescriptor
	body       []byte
	mime       string
}

type assetObservation struct {
	assetDescriptor
	Requests     uint64 `json:"requests"`
	Completed    uint64 `json:"completed"`
	WrittenBytes uint64 `json:"written_bytes"`
}

type assetGroup struct {
	Responses map[string]*assetObservation `json:"responses"`
}

type apiObservation struct {
	Method          string `json:"method"`
	Path            string `json:"path"`
	RequestBytes    int    `json:"request_bytes"`
	RequestSHA256   string `json:"request_sha256"`
	ResponseBytes   int    `json:"response_bytes"`
	ResponseSHA256  string `json:"response_sha256"`
	AssetCookieHash string `json:"asset_cookie_hash"`
}

type backendStats struct {
	ManifestSHA256     string                 `json:"manifest_sha256"`
	APIPosts           uint64                 `json:"api_posts"`
	APIGets            uint64                 `json:"api_gets"`
	CatalogRecords     uint64                 `json:"catalog_records"`
	BootstrapCompleted uint64                 `json:"bootstrap_completed"`
	Rejected           uint64                 `json:"rejected"`
	WSOpened           uint64                 `json:"ws_opened"`
	WSClosed           uint64                 `json:"ws_closed"`
	NormalCloses       uint64                 `json:"normal_closes"`
	Connections        []connectionStats      `json:"connections"`
	API                []apiObservation       `json:"api"`
	AssetGroups        map[string]*assetGroup `json:"asset_groups"`
	AssetFailures      uint64                 `json:"asset_failures"`
}

type appSession struct {
	step            int
	inflight        bool
	attached        bool
	assetCookieHash string
}

type backend struct {
	manifest    appManifest
	manifestSHA string
	statsPath   string
	mu          sync.Mutex
	stats       backendStats
	sessions    map[string]*appSession
	connections map[*websocket.Conn]bool
	closed      bool
	wg          sync.WaitGroup
	ctx         context.Context
	cancel      context.CancelFunc
	assets      map[string]*immutableAsset
}

func newBackend(statsPath, assetDir string) (*backend, error) {
	manifest, digest, err := loadManifest()
	if err != nil {
		return nil, err
	}
	assets, err := loadAssets(assetDir)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	return &backend{manifest: manifest, manifestSHA: digest, statsPath: statsPath,
		stats: backendStats{ManifestSHA256: digest, AssetGroups: make(map[string]*assetGroup)}, sessions: make(map[string]*appSession),
		connections: make(map[*websocket.Conn]bool), ctx: ctx, cancel: cancel, assets: assets}, nil
}

func loadAssets(directory string) (map[string]*immutableAsset, error) {
	assets := make(map[string]*immutableAsset)
	for _, path := range []string{"/assets/site.css", "/assets/app.js", "/assets/image-1.svg", "/assets/image-2.svg", "/assets/image-3.svg", "/assets/image-4.svg"} {
		file, size, mime := "image.svg", 8192, "image/svg+xml"
		if path == "/assets/site.css" {
			file, size, mime = "site.css", 12288, "text/css"
		}
		if path == "/assets/app.js" {
			file, size, mime = "app.js", 24576, "text/javascript"
		}
		body, err := os.ReadFile(filepath.Join(directory, file))
		if err != nil {
			return nil, err
		}
		if len(body) > size || (file == "app.js" && len(body) != size) {
			return nil, errors.New("immutable asset size")
		}
		body = append(body, bytes.Repeat([]byte{' '}, size-len(body))...)
		digest := sha256.Sum256(body)
		assets[path] = &immutableAsset{descriptor: assetDescriptor{path, size, hex.EncodeToString(digest[:])}, body: body, mime: mime}
	}
	return assets, nil
}

func sourceCookieHash(r *http.Request) string {
	cookie, err := r.Cookie("app_session")
	if err != nil || cookie.Value == "" {
		return "none"
	}
	digest := sha256.Sum256([]byte(cookie.Value))
	return hex.EncodeToString(digest[:])
}

func (b *backend) assetInventory() []assetDescriptor {
	items := make([]assetDescriptor, 0, len(b.assets))
	for _, asset := range b.assets {
		items = append(items, asset.descriptor)
	}
	sort.Slice(items, func(i, j int) bool { return items[i].Path < items[j].Path })
	return items
}

func (b *backend) assetsCompleted(cookie string) bool {
	group := b.stats.AssetGroups[cookie]
	if group == nil {
		return false
	}
	for path := range b.assets {
		observed := group.Responses[path]
		if observed == nil || observed.Completed == 0 || observed.Completed != observed.Requests {
			return false
		}
	}
	return true
}

func (b *backend) serveAsset(w http.ResponseWriter, r *http.Request, asset *immutableAsset) {
	if r.Method != "GET" || r.URL.RawQuery != "" {
		b.reject(w)
		return
	}
	cookie := sourceCookieHash(r)
	b.mu.Lock()
	group := b.stats.AssetGroups[cookie]
	if b.closed || (group == nil && len(b.stats.AssetGroups) >= 32) {
		b.mu.Unlock()
		b.reject(w)
		return
	}
	if group == nil {
		group = &assetGroup{Responses: make(map[string]*assetObservation)}
		b.stats.AssetGroups[cookie] = group
	}
	observed := group.Responses[r.URL.Path]
	if observed == nil {
		observed = &assetObservation{assetDescriptor: asset.descriptor}
		group.Responses[r.URL.Path] = observed
	}
	observed.Requests++
	b.mu.Unlock()
	w.Header().Set("Content-Type", asset.mime)
	w.Header().Set("Content-Length", strconv.Itoa(len(asset.body)))
	w.Header().Set("Cache-Control", "public, max-age=3600")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	n, err := w.Write(asset.body)
	b.mu.Lock()
	observed.WrittenBytes += uint64(n)
	if err == nil && n == len(asset.body) {
		observed.Completed++
	} else {
		b.stats.AssetFailures++
	}
	b.mu.Unlock()
}

func writeAtomicJSON(path string, value any) error {
	if path == "" {
		return nil
	}
	body, err := json.Marshal(value)
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".nfbench-json-")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name)
	if err := file.Chmod(0600); err != nil {
		file.Close()
		return err
	}
	if _, err := file.Write(append(body, '\n')); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}

func (b *backend) Close() error {
	b.mu.Lock()
	b.closed = true
	b.cancel()
	connections := make([]*websocket.Conn, 0, len(b.connections))
	for conn := range b.connections {
		connections = append(connections, conn)
	}
	b.mu.Unlock()
	for _, conn := range connections {
		conn.Close()
	}
	b.wg.Wait()
	b.mu.Lock()
	defer b.mu.Unlock()
	return writeAtomicJSON(b.statsPath, b.stats)
}

func (b *backend) reject(w http.ResponseWriter) {
	b.mu.Lock()
	b.stats.Rejected++
	b.mu.Unlock()
	http.Error(w, "application request rejected", http.StatusBadRequest)
}

func (b *backend) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if asset := b.assets[r.URL.Path]; asset != nil {
		b.serveAsset(w, r, asset)
		return
	}
	if r.URL.Path == "/api/realtime" {
		b.websocket(w, r)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/app/api/bootstrap/") {
		b.bootstrap(w, r)
		return
	}
	http.NotFound(w, r)
}

type catalogRecord struct {
	ID         uint32 `json:"id"`
	Title      string `json:"title"`
	Group      uint32 `json:"group"`
	Revision   uint32 `json:"revision"`
	ChunkBytes uint32 `json:"chunk_bytes"`
	SourceJob  uint32 `json:"source_job"`
}

func catalog(round int) []catalogRecord {
	records := make([]catalogRecord, 64)
	for index := range records {
		id := uint32(round*64 + index + 1)
		records[index] = catalogRecord{id, fmt.Sprintf("Archive item %06d", id), id % 8, 1 + id%97, chunkBytes, (id-1)%11 + 1}
	}
	return records
}

func decodeJSON(body []byte, value any) error {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return errors.New("trailing JSON")
	}
	return nil
}

func (b *backend) bootstrap(w http.ResponseWriter, r *http.Request) {
	part := strings.TrimPrefix(r.URL.Path, "/app/api/bootstrap/")
	round, err := strconv.Atoi(part)
	if err != nil || strconv.Itoa(round) != part || round < 0 || round >= 20 || (r.Method != "GET" && r.Method != "POST") || r.URL.RawQuery != "" {
		b.reject(w)
		return
	}
	var requestBody []byte
	if r.Method == "POST" {
		var input struct {
			Cursor      int `json:"cursor"`
			Preferences struct {
				Order    string `json:"order"`
				PageSize int    `json:"page_size"`
			} `json:"preferences"`
			ManifestSHA256 string `json:"manifest_sha256"`
		}
		requestBody, err = io.ReadAll(io.LimitReader(r.Body, 1025))
		if err != nil || len(requestBody) > 1024 || decodeJSON(requestBody, &input) != nil || input.Cursor != round*64 || input.Preferences.Order != "ascending" || input.Preferences.PageSize != 64 || input.ManifestSHA256 != b.manifestSHA {
			b.reject(w)
			return
		}
	}
	b.mu.Lock()
	var session *appSession
	if cookie, err := r.Cookie("nfbench_session"); err == nil {
		session = b.sessions[cookie.Value]
	}
	if session == nil && r.Method == "POST" && round == 0 && len(b.sessions) < 8 && !b.closed {
		token := make([]byte, 32)
		if _, err := rand.Read(token); err != nil {
			b.mu.Unlock()
			b.reject(w)
			return
		}
		id := hex.EncodeToString(token)
		if sourceCookieHash(r) == "none" {
			b.mu.Unlock()
			b.reject(w)
			return
		}
		session = &appSession{assetCookieHash: sourceCookieHash(r)}
		b.sessions[id] = session
		http.SetCookie(w, &http.Cookie{Name: "nfbench_session", Value: id, Path: "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteStrictMode})
	}
	expected := 2 * round
	if r.Method == "GET" {
		expected++
	}
	if b.closed || session == nil || session.inflight || session.attached || session.step != expected || session.assetCookieHash != sourceCookieHash(r) {
		b.mu.Unlock()
		b.reject(w)
		return
	}
	session.inflight = true
	b.mu.Unlock()
	var response any
	if r.Method == "POST" {
		var assets []assetDescriptor
		if round == 0 {
			assets = b.assetInventory()
		}
		response = struct {
			Cursor int               `json:"accepted_cursor"`
			Count  int               `json:"count"`
			SHA    string            `json:"manifest_sha256"`
			Assets []assetDescriptor `json:"assets,omitempty"`
		}{round * 64, 64, b.manifestSHA, assets}
	} else {
		response = struct {
			Cursor  int             `json:"cursor"`
			Records []catalogRecord `json:"records"`
			Next    int             `json:"next_cursor"`
			SHA     string          `json:"manifest_sha256"`
		}{round * 64, catalog(round), (round + 1) * 64, b.manifestSHA}
	}
	body, err := json.Marshal(response)
	if err == nil {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
		var count int
		count, err = w.Write(body)
		if count != len(body) && err == nil {
			err = io.ErrShortWrite
		}
	}
	b.mu.Lock()
	session.inflight = false
	if err == nil {
		requestDigest, responseDigest := sha256.Sum256(requestBody), sha256.Sum256(body)
		b.stats.API = append(b.stats.API, apiObservation{r.Method, r.URL.Path, len(requestBody), hex.EncodeToString(requestDigest[:]), len(body), hex.EncodeToString(responseDigest[:]), session.assetCookieHash})
		session.step++
		if r.Method == "POST" {
			b.stats.APIPosts++
		} else {
			b.stats.APIGets++
			b.stats.CatalogRecords += 64
		}
		if session.step == 40 {
			b.stats.BootstrapCompleted++
		}
	} else {
		session.attached = true
		b.stats.Rejected++
	}
	b.mu.Unlock()
}

type control struct {
	Op     string   `json:"op"`
	ID     uint32   `json:"id"`
	Kind   string   `json:"kind,omitempty"`
	Bytes  uint32   `json:"bytes,omitempty"`
	SHA256 string   `json:"sha256,omitempty"`
	Credit uint32   `json:"credit,omitempty"`
	IDs    []uint32 `json:"ids,omitempty"`
}

type event struct {
	kind int
	body []byte
	err  error
	at   time.Time
}

type activeJob struct {
	spec           jobSpec
	stats          *jobStats
	inBudget       uint32
	outCredit      uint32
	readySent      bool
	clientFin      bool
	completeQueued bool
	completeSent   bool
	hasher         hash.Hash
	echo           []byte
}

type application struct {
	backend       *backend
	conn          *websocket.Conn
	ctx           context.Context
	start         time.Time
	stats         connectionStats
	jobs          map[uint32]*activeJob
	seen          map[uint32]bool
	reports       map[uint32]*jobStats
	order         []uint32
	cursor        int
	controls      []control
	retiredCredit map[uint32]uint32
}

func (a *application) elapsed(at time.Time) float64 {
	return float64(at.Sub(a.start)) / float64(time.Millisecond)
}

func (a *application) queue(message control) error {
	if len(a.controls) >= maxControls {
		return errors.New("control_queue_bound")
	}
	a.controls = append(a.controls, message)
	return nil
}

func (a *application) openJob(spec jobSpec) error {
	report := &jobStats{ID: spec.ID, Kind: spec.Kind, Bytes: spec.Bytes}
	job := &activeJob{spec: spec, stats: report, inBudget: creditWindow, outCredit: creditWindow, hasher: sha256.New()}
	if spec.Kind == "echo" {
		job.echo = make([]byte, 0, spec.Bytes)
	}
	a.jobs[spec.ID], a.seen[spec.ID], a.reports[spec.ID] = job, true, report
	a.order = append(a.order, spec.ID)
	a.stats.OpenOrder = append(a.stats.OpenOrder, spec.ID)
	a.stats.PeakJobs = max(a.stats.PeakJobs, len(a.jobs))
	return a.queue(control{Op: "ready", ID: spec.ID, Kind: spec.Kind, Bytes: spec.Bytes, Credit: creditWindow})
}

func (a *application) handleControl(body []byte) error {
	var message control
	if len(body) > 1024 || decodeJSON(body, &message) != nil || message.Credit != 0 {
		return errors.New("invalid_control")
	}
	if message.Op == "open_batch" {
		if message.ID != 0 || message.Kind != "" || message.Bytes != 0 || message.SHA256 != "" || len(message.IDs) != 4 || len(a.jobs) != 0 || a.stats.ParallelBatches != 0 {
			return errors.New("invalid_batch")
		}
		for index, id := range message.IDs {
			if id != uint32(index+3) || a.seen[id] {
				return errors.New("invalid_batch_ids")
			}
		}
		for _, id := range message.IDs {
			if err := a.openJob(a.backend.manifest.Jobs[id-1]); err != nil {
				return err
			}
		}
		a.stats.ParallelBatches++
		a.stats.ParallelJobCount = 4
		return nil
	}
	if message.ID == 0 || len(message.IDs) != 0 {
		return errors.New("invalid_control_id")
	}
	job := a.jobs[message.ID]
	if message.Op == "open" {
		if job != nil || a.seen[message.ID] || len(a.jobs) >= maxJobs || message.ID > uint32(len(a.backend.manifest.Jobs)) || message.SHA256 != "" || (message.ID >= 3 && message.ID <= 6) {
			return errors.New("job_bound_or_reuse")
		}
		spec := a.backend.manifest.Jobs[message.ID-1]
		if message.Kind != spec.Kind || message.Bytes != spec.Bytes {
			return errors.New("unknown_dataset")
		}
		return a.openJob(spec)
	}
	if debt, retired := a.retiredCredit[message.ID]; job == nil && retired && message.Op == "credit" {
		if message.Kind != "" || message.SHA256 != "" || message.Bytes == 0 || message.Bytes > debt {
			return errors.New("retired_credit_overflow")
		}
		a.retiredCredit[message.ID] = debt - message.Bytes
		return nil
	}
	if job == nil || !job.readySent || message.Kind != "" {
		return errors.New("unknown_or_unready_job")
	}
	switch message.Op {
	case "credit":
		if job.spec.Kind == "upload" || message.SHA256 != "" || message.Bytes == 0 || message.Bytes > creditWindow-job.outCredit {
			return errors.New("credit_overflow")
		}
		job.outCredit += message.Bytes
		return nil
	case "fin":
		if job.spec.Kind == "download" || job.clientFin || job.stats.Received != job.spec.Bytes || message.Bytes != job.spec.Bytes || message.SHA256 != job.spec.SHA256 {
			return errors.New("early_or_invalid_fin")
		}
		digest := hex.EncodeToString(job.hasher.Sum(nil))
		if digest != job.spec.SHA256 {
			return errors.New("upload_hash")
		}
		job.stats.SHA256, job.clientFin = digest, true
		if job.spec.Kind == "upload" {
			job.completeQueued = true
			return a.queue(control{Op: "complete", ID: job.spec.ID, Bytes: job.spec.Bytes, SHA256: digest})
		}
		return nil
	case "done":
		if !job.completeSent || message.Bytes != job.spec.Bytes || message.SHA256 != job.spec.SHA256 {
			return errors.New("invalid_done")
		}
		job.stats.Verified, job.stats.VerifiedMS = true, a.elapsed(time.Now())
		if job.spec.Kind != "upload" {
			a.retiredCredit[message.ID] = creditWindow - job.outCredit
		}
		delete(a.jobs, message.ID)
		for index, id := range a.order {
			if id == message.ID {
				a.order = append(a.order[:index], a.order[index+1:]...)
				if index < a.cursor {
					a.cursor--
				}
				break
			}
		}
		return nil
	default:
		return errors.New("unknown_control")
	}
}

func (a *application) handleData(body []byte, at time.Time) error {
	if len(body) < 17 || len(body) > chunkBytes+16 || body[0] != 1 || body[1] != 0 || body[2] != 0 || body[3] != 0 {
		return errors.New("data_header")
	}
	id, offset, length := binary.BigEndian.Uint32(body[4:8]), binary.BigEndian.Uint32(body[8:12]), binary.BigEndian.Uint32(body[12:16])
	job := a.jobs[id]
	if job == nil || !job.readySent || job.spec.Kind == "download" || job.clientFin || length != uint32(len(body)-16) || length > chunkBytes || offset != job.stats.Received || length > job.spec.Bytes-job.stats.Received || length > job.inBudget {
		return errors.New("data_sequence_or_credit")
	}
	payload := body[16:]
	for index, value := range payload {
		position := offset + uint32(index)
		if value != byte(id*17+position*31+(position>>8)) {
			return errors.New("payload_mismatch")
		}
	}
	if job.stats.Received == 0 {
		job.stats.FirstReceiveMS = a.elapsed(at)
	}
	job.stats.LastReceiveMS = a.elapsed(at)
	job.inBudget -= length
	job.stats.Received += length
	job.stats.Validated += length
	job.hasher.Write(payload)
	if job.spec.Kind == "echo" {
		job.echo = append(job.echo, payload...)
	}
	a.stats.DataBytesIn += uint64(length)
	return a.queue(control{Op: "credit", ID: id, Bytes: length})
}

func dataMessage(id, offset uint32, payload []byte) []byte {
	body := make([]byte, 16+len(payload))
	body[0] = 1
	binary.BigEndian.PutUint32(body[4:8], id)
	binary.BigEndian.PutUint32(body[8:12], offset)
	binary.BigEndian.PutUint32(body[12:16], uint32(len(payload)))
	copy(body[16:], payload)
	return body
}

func (a *application) writeMessage(kind int, body []byte) error {
	a.conn.SetWriteDeadline(time.Now().Add(30 * time.Second))
	if err := a.conn.WriteMessage(kind, body); err != nil {
		return err
	}
	a.stats.MessagesOut++
	if kind == websocket.BinaryMessage {
		a.stats.DataMessagesOut++
	} else if kind == websocket.TextMessage {
		a.stats.ControlMessagesOut++
	}
	return nil
}

func (a *application) writeOne() (bool, error) {
	if len(a.controls) != 0 {
		message := a.controls[0]
		a.controls = a.controls[1:]
		body, err := json.Marshal(message)
		if err != nil {
			return false, err
		}
		if err := a.writeMessage(websocket.TextMessage, body); err != nil {
			return false, err
		}
		if job := a.jobs[message.ID]; job != nil {
			if message.Op == "credit" {
				job.inBudget += message.Bytes
			}
			if message.Op == "ready" {
				job.readySent = true
			}
			if message.Op == "complete" {
				job.completeSent = true
			}
		}
		return true, nil
	}
	for checked := 0; checked < len(a.order); checked++ {
		a.cursor %= len(a.order)
		job := a.jobs[a.order[a.cursor]]
		a.cursor++
		if job.spec.Kind == "upload" || !job.readySent || job.completeQueued || (job.spec.Kind == "echo" && !job.clientFin) {
			continue
		}
		if job.stats.Sent == job.spec.Bytes {
			digest := job.stats.SHA256
			if job.spec.Kind == "download" {
				digest = hex.EncodeToString(job.hasher.Sum(nil))
			}
			if digest != job.spec.SHA256 {
				return false, errors.New("download_hash")
			}
			job.stats.SHA256 = digest
			job.completeQueued = true
			return true, a.queue(control{Op: "complete", ID: job.spec.ID, Bytes: job.spec.Bytes, SHA256: digest})
		}
		length := min(uint32(chunkBytes), job.spec.Bytes-job.stats.Sent, job.outCredit)
		if length == 0 {
			continue
		}
		payload := make([]byte, length)
		if job.spec.Kind == "echo" {
			copy(payload, job.echo[job.stats.Sent:job.stats.Sent+length])
		} else {
			fillPayload(payload, job.spec.ID, job.stats.Sent)
		}
		if err := a.writeMessage(websocket.BinaryMessage, dataMessage(job.spec.ID, job.stats.Sent, payload)); err != nil {
			return false, err
		}
		at := time.Now()
		if job.stats.Sent == 0 {
			job.stats.FirstSendMS = a.elapsed(at)
		}
		job.stats.LastSendMS = a.elapsed(at)
		job.stats.Sent += length
		job.outCredit -= length
		if job.spec.Kind == "download" {
			job.hasher.Write(payload)
		}
		a.stats.DataBytesOut += uint64(length)
		return true, nil
	}
	return false, nil
}

func (a *application) consume(in event) error {
	if in.err != nil {
		return in.err
	}
	a.stats.MessagesIn++
	switch in.kind {
	case websocket.TextMessage:
		a.stats.ControlMessagesIn++
		return a.handleControl(in.body)
	case websocket.BinaryMessage:
		a.stats.DataMessagesIn++
		return a.handleData(in.body, in.at)
	case websocket.PingMessage:
		return a.conn.WriteControl(websocket.PongMessage, in.body, time.Now().Add(5*time.Second))
	default:
		return errors.New("unexpected_message")
	}
}

func (a *application) run(events <-chan event) error {
	for {
		select {
		case <-a.ctx.Done():
			return a.ctx.Err()
		case in := <-events:
			if err := a.consume(in); err != nil {
				return err
			}
		default:
		}
		written, err := a.writeOne()
		if err != nil {
			return err
		}
		if written {
			continue
		}
		select {
		case <-a.ctx.Done():
			return a.ctx.Err()
		case in := <-events:
			if err := a.consume(in); err != nil {
				return err
			}
		}
	}
}

func (b *backend) websocket(w http.ResponseWriter, r *http.Request) {
	if r.Method != "GET" || r.ProtoMajor != 1 || len(websocket.Subprotocols(r)) != 1 || websocket.Subprotocols(r)[0] != appProtocol {
		b.reject(w)
		return
	}
	b.mu.Lock()
	var session *appSession
	if cookie, err := r.Cookie("nfbench_session"); err == nil {
		session = b.sessions[cookie.Value]
	}
	if b.closed || session == nil || session.step != 40 || session.inflight || session.attached || session.assetCookieHash != sourceCookieHash(r) || !b.assetsCompleted(session.assetCookieHash) {
		b.mu.Unlock()
		b.reject(w)
		return
	}
	session.attached = true
	b.wg.Add(1)
	b.mu.Unlock()
	defer b.wg.Done()
	upgrader := websocket.Upgrader{Subprotocols: []string{appProtocol}, ReadBufferSize: 4096, WriteBufferSize: 4096, EnableCompression: false,
		CheckOrigin: func(request *http.Request) bool {
			origin, err := url.Parse(request.Header.Get("Origin"))
			return err == nil && (origin.Scheme == "http" || origin.Scheme == "https") && origin.Host == request.Host && origin.User == nil && origin.Path == "" && origin.RawQuery == "" && origin.Fragment == ""
		}}
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()
	b.mu.Lock()
	b.stats.WSOpened++
	id := uint32(b.stats.WSOpened)
	b.connections[conn] = true
	b.mu.Unlock()
	ctx, cancel := context.WithCancel(b.ctx)
	defer cancel()
	a := &application{backend: b, conn: conn, ctx: ctx, start: time.Now(), stats: connectionStats{ID: id, BootstrapPairs: 20, WSOpened: 1}, jobs: make(map[uint32]*activeJob), seen: make(map[uint32]bool), reports: make(map[uint32]*jobStats), retiredCredit: make(map[uint32]uint32)}
	a.stats.AssetCookieHash = session.assetCookieHash
	for _, spec := range b.manifest.Jobs {
		if spec.Kind != "download" {
			a.stats.ExpectedDataBytesIn += uint64(spec.Bytes)
		}
		if spec.Kind != "upload" {
			a.stats.ExpectedDataBytesOut += uint64(spec.Bytes)
		}
	}
	events := make(chan event, maxEvents)
	readerDone := make(chan struct{})
	send := func(in event) bool {
		select {
		case events <- in:
			return true
		case <-ctx.Done():
			return false
		}
	}
	conn.SetReadLimit(chunkBytes + 16)
	conn.SetCloseHandler(func(int, string) error { return nil })
	conn.SetPingHandler(func(body string) error {
		if !send(event{kind: websocket.PingMessage, body: []byte(body), at: time.Now()}) {
			return context.Canceled
		}
		return nil
	})
	go func() {
		defer close(readerDone)
		for {
			conn.SetReadDeadline(time.Now().Add(30 * time.Second))
			kind, body, err := conn.ReadMessage()
			if !send(event{kind: kind, body: body, err: err, at: time.Now()}) || err != nil {
				return
			}
		}
	}()
	err = a.run(events)
	var closed *websocket.CloseError
	normal := errors.As(err, &closed) && closed.Code == websocket.CloseNormalClosure && len(a.jobs) == 0 && len(a.reports) == len(b.manifest.Jobs) && a.stats.ParallelBatches == 1 && a.stats.ParallelJobCount == 4 && a.stats.PeakJobs == 4 && a.stats.DataBytesIn == a.stats.ExpectedDataBytesIn && a.stats.DataBytesOut == a.stats.ExpectedDataBytesOut
	for _, report := range a.reports {
		normal = normal && report.Verified
	}
	code := websocket.ClosePolicyViolation
	if normal {
		code = websocket.CloseNormalClosure
	}
	conn.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(code, ""), time.Now().Add(5*time.Second))
	cancel()
	conn.Close()
	<-readerDone
	a.stats.WSClosed, a.stats.NormalClose, a.stats.CloseCode = 1, normal, code
	if !normal {
		a.stats.Failure = "incomplete-or-invalid-application"
	}
	for _, report := range a.reports {
		a.stats.Jobs = append(a.stats.Jobs, *report)
	}
	sort.Slice(a.stats.Jobs, func(i, j int) bool { return a.stats.Jobs[i].ID < a.stats.Jobs[j].ID })
	b.mu.Lock()
	delete(b.connections, conn)
	b.stats.WSClosed++
	if normal {
		b.stats.NormalCloses++
	}
	b.stats.Connections = append(b.stats.Connections, a.stats)
	_ = writeAtomicJSON(b.statsPath, b.stats)
	b.mu.Unlock()
}

func run() error {
	listen := flag.String("listen", "127.0.0.1:0", "numeric loopback listener")
	statsPath := flag.String("stats", "", "private aggregate JSON path")
	readyPath := flag.String("ready", "", "private ready JSON path")
	assetDir := flag.String("asset-dir", "", "immutable application assets directory")
	flag.Parse()
	host, port, err := net.SplitHostPort(*listen)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		return errors.New("listener must use numeric loopback")
	}
	number, err := strconv.Atoi(port)
	if err != nil || number < 0 || number > 65535 {
		return errors.New("invalid listener port")
	}
	if *statsPath == "" || *readyPath == "" || !filepath.IsAbs(*statsPath) || !filepath.IsAbs(*readyPath) {
		return errors.New("absolute private stats and ready paths required")
	}
	if !filepath.IsAbs(*assetDir) {
		return errors.New("absolute immutable asset directory required")
	}
	b, err := newBackend(*statsPath, *assetDir)
	if err != nil {
		return err
	}
	listener, err := net.Listen("tcp", *listen)
	if err != nil {
		return err
	}
	defer listener.Close()
	server := &http.Server{Handler: b, ReadHeaderTimeout: 5 * time.Second, MaxHeaderBytes: 16384}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() { <-ctx.Done(); server.Close(); b.Close() }()
	if err := writeAtomicJSON(*readyPath, map[string]any{"port": listener.Addr().(*net.TCPAddr).Port, "manifest_sha256": b.manifestSHA}); err != nil {
		return err
	}
	err = server.Serve(listener)
	if errors.Is(err, http.ErrServerClosed) {
		err = nil
	}
	closeErr := b.Close()
	if err != nil {
		return err
	}
	return closeErr
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "application fixture failed")
		os.Exit(1)
	}
}
