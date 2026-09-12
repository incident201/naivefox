package main

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

func (b *backend) httpWork(w http.ResponseWriter, r *http.Request) {
	b.mu.Lock()
	var session *appSession
	if cookie, err := r.Cookie("nfbench_session"); err == nil {
		session = b.sessions[cookie.Value]
	}
	if b.closed || session == nil || session.step != 40 || session.inflight ||
		session.assetCookieHash != sourceCookieHash(r) || !b.assetsCompleted(session.assetCookieHash) {
		b.mu.Unlock()
		b.reject(w)
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/app/api/work/")
	if path == "start" {
		if r.Method != http.MethodPost || session.attached || r.ContentLength != 0 {
			b.mu.Unlock()
			b.reject(w)
			return
		}
		session.attached = true
		session.start = time.Now()
		session.jobs = make(map[uint32]bool)
		session.batch = make(chan struct{})
		b.stats.HTTPStarted++
		session.work = &connectionStats{ID: uint32(b.stats.HTTPStarted), BootstrapPairs: 20,
			HTTPStarted: 1, AssetCookieHash: session.assetCookieHash}
		for _, spec := range b.manifest.Jobs {
			if spec.Kind != "download" {
				session.work.ExpectedDataBytesIn += uint64(spec.Bytes)
			}
			if spec.Kind != "upload" {
				session.work.ExpectedDataBytesOut += uint64(spec.Bytes)
			}
		}
		err := writeAtomicJSON(b.statsPath, b.stats)
		b.mu.Unlock()
		if err != nil {
			b.reject(w)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if !session.attached || session.work == nil || session.work.HTTPEnded != 0 {
		b.mu.Unlock()
		b.reject(w)
		return
	}
	if path == "end" {
		if r.Method != http.MethodPost || r.ContentLength != 0 || session.active != 0 ||
			len(session.work.Jobs) != len(b.manifest.Jobs) || session.work.PeakJobs != 4 ||
			session.work.DataBytesIn != session.work.ExpectedDataBytesIn ||
			session.work.DataBytesOut != session.work.ExpectedDataBytesOut {
			b.mu.Unlock()
			b.reject(w)
			return
		}
		for _, job := range session.work.Jobs {
			if !job.Verified {
				b.mu.Unlock()
				b.reject(w)
				return
			}
		}
		session.work.HTTPEnded, session.work.NormalClose, session.work.CloseCode = 1, true, 204
		sort.Slice(session.work.Jobs, func(i, j int) bool { return session.work.Jobs[i].ID < session.work.Jobs[j].ID })
		b.stats.HTTPEnded++
		b.stats.NormalCloses++
		b.stats.Connections = append(b.stats.Connections, *session.work)
		err := writeAtomicJSON(b.statsPath, b.stats)
		b.mu.Unlock()
		if err != nil {
			b.reject(w)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		w.WriteHeader(http.StatusNoContent)
		return
	}
	id, err := strconv.ParseUint(strings.TrimPrefix(path, "job/"), 10, 32)
	var spec jobSpec
	for _, candidate := range b.manifest.Jobs {
		if candidate.ID == uint32(id) {
			spec = candidate
			break
		}
	}
	method := http.MethodGet
	if spec.Kind != "download" {
		method = http.MethodPost
	}
	_, seen := session.jobs[spec.ID]
	if err != nil || !strings.HasPrefix(path, "job/") || spec.ID == 0 || seen ||
		r.Method != method || session.active >= 4 || (method == http.MethodGet && r.ContentLength != 0) {
		b.mu.Unlock()
		b.reject(w)
		return
	}
	session.jobs[spec.ID] = false
	session.active++
	session.work.PeakJobs = max(session.work.PeakJobs, session.active)
	session.work.OpenOrder = append(session.work.OpenOrder, spec.ID)
	batch := spec.ID >= 3 && spec.ID <= 6
	if batch {
		session.batchCount++
		if session.batchCount == 4 {
			session.work.ParallelBatches = 1
			session.work.ParallelJobCount = 4
			close(session.batch)
		}
	}
	b.wg.Add(1)
	b.mu.Unlock()
	job := jobStats{ID: spec.ID, Kind: spec.Kind, Bytes: spec.Bytes, SHA256: spec.SHA256}
	defer func() {
		b.mu.Lock()
		session.active--
		session.jobs[spec.ID] = job.Verified
		session.work.Jobs = append(session.work.Jobs, job)
		session.work.DataBytesIn += uint64(job.Received)
		session.work.DataBytesOut += uint64(job.Sent)
		b.mu.Unlock()
		b.wg.Done()
	}()
	controller := http.NewResponseController(w)
	_ = controller.SetReadDeadline(time.Now().Add(30 * time.Second))
	_ = controller.SetWriteDeadline(time.Now().Add(30 * time.Second))
	if batch {
		select {
		case <-session.batch:
		case <-r.Context().Done():
			return
		case <-b.ctx.Done():
			return
		case <-time.After(10 * time.Second):
			b.reject(w)
			return
		}
	}
	elapsed := func() float64 { return float64(time.Since(session.start)) / float64(time.Millisecond) }
	if spec.Kind != "download" {
		hash := sha256.New()
		job.FirstReceiveMS = elapsed()
		n, err := io.Copy(hash, io.LimitReader(r.Body, int64(spec.Bytes)+1))
		r.Body.Close()
		job.LastReceiveMS = elapsed()
		job.Received = uint32(n)
		if err != nil || n != int64(spec.Bytes) || hex.EncodeToString(hash.Sum(nil)) != spec.SHA256 {
			b.reject(w)
			return
		}
		job.Validated = uint32(n)
	}
	length := spec.Bytes
	if spec.Kind == "upload" {
		length = 0
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-SHA256", spec.SHA256)
	w.Header().Set("Content-Length", strconv.Itoa(int(length)))
	w.WriteHeader(http.StatusOK)
	buffer := make([]byte, chunkBytes)
	job.FirstSendMS = elapsed()
	for job.Sent < length {
		n := min(uint32(len(buffer)), length-job.Sent)
		fillPayload(buffer[:n], spec.ID, job.Sent)
		written, err := w.Write(buffer[:n])
		job.Sent += uint32(written)
		if err != nil || written != int(n) {
			return
		}
		if err := controller.Flush(); err != nil {
			return
		}
	}
	job.LastSendMS = elapsed()
	job.Verified = true
	job.VerifiedMS = elapsed()
}
