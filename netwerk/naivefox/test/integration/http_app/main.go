package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

//go:embed manifest.json
var manifestBytes []byte

const (
	appProtocol  = "nfbench.http"
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
	ID             uint32 `json:"id"`
	BootstrapPairs int    `json:"bootstrap_pairs"`
	HTTPStarted    int    `json:"http_started"`
	HTTPEnded      int    `json:"http_ended"`
	NormalClose    bool   `json:"normal_close"`
	CloseCode      int    `json:"close_code"`
	Failure        string `json:"failure,omitempty"`

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
	HTTPStarted        uint64                 `json:"http_started"`
	HTTPEnded          uint64                 `json:"http_ended"`
	NormalCloses       uint64                 `json:"normal_closes"`
	Connections        []connectionStats      `json:"connections"`
	API                []apiObservation       `json:"api"`
	AssetGroups        map[string]*assetGroup `json:"asset_groups"`
	AssetFailures      uint64                 `json:"asset_failures"`
}

type appSession struct {
	work            *connectionStats
	jobs            map[uint32]bool
	active          int
	batch           chan struct{}
	batchCount      int
	start           time.Time
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
		ctx: ctx, cancel: cancel, assets: assets}, nil
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
	cookie, err := r.Cookie("session")
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
	b.mu.Unlock()
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
	if strings.HasPrefix(r.URL.Path, "/app/api/work/") {
		b.httpWork(w, r)
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
