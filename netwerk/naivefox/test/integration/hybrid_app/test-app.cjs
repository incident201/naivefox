#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { performance } = require("node:perf_hooks");

const manifestBytes = fs.readFileSync(path.join(__dirname, "manifest.json"));
const manifest = JSON.parse(manifestBytes);
const manifestSHA = crypto.createHash("sha256").update(manifestBytes).digest("hex");
const script = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
assert.equal(Buffer.byteLength(script), 24576);
assert(script.includes(manifestSHA));

function data(id, offset, length) {
  const bytes = new Uint8Array(length);
  for (let index = 0; index < length; ++index) {
    const position = offset + index;
    bytes[index] = (id * 17 + position * 31 + Math.floor(position / 256)) % 256;
  }
  return bytes;
}

for (const job of manifest.jobs) {
  const hash = crypto.createHash("sha256");
  for (let offset = 0; offset < job.bytes; offset += manifest.chunk_bytes) {
    hash.update(data(job.id, offset, Math.min(manifest.chunk_bytes, job.bytes - offset)));
  }
  assert.equal(hash.digest("hex"), job.sha256);
}

async function until(condition) {
  const end = performance.now() + 10000;
  while (!condition()) {
    assert(performance.now() < end, "test timed out");
    await new Promise(resolve => setImmediate(resolve));
  }
}

async function exercise({ hold = false, malformed = null, delayLoad = false, badDigest = false } = {}) {
  const state = { bootstrap: 0, websockets: 0, clientControls: 0, serverControls: 0,
                  clientBinary: 0, serverBinary: 0, hashes: 0, peakJobs: 0, done: [],
                  jobs: new Map(), loadListeners: [], errors: [] };
  let deliveredFault = false;

  class Socket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    constructor(url, protocol) {
      assert.equal(state.bootstrap, 40);
      assert.equal(new URL(url).pathname, "/api/realtime");
      assert.equal(protocol, manifest.protocol);
      state.websockets += 1;
      this.protocol = protocol;
      this.readyState = Socket.CONNECTING;
      this.bufferedAmount = 0;
      setImmediate(() => { this.readyState = Socket.OPEN; this.onopen(); });
    }
    emit(value) {
      if (typeof value === "string") state.serverControls += 1;
      else state.serverBinary += 1;
      setImmediate(() => {
        if (this.readyState === Socket.OPEN) this.onmessage({ data: value });
      });
    }
    control(value) { this.emit(JSON.stringify(value)); }
    frame(job, bytes) {
      const frame = new ArrayBuffer(16 + bytes.byteLength);
      const head = new DataView(frame);
      head.setUint8(0, 1);
      head.setUint32(4, job.id);
      head.setUint32(8, job.sent);
      head.setUint32(12, bytes.byteLength);
      new Uint8Array(frame, 16).set(bytes);
      if (malformed && !deliveredFault) {
        deliveredFault = true;
        if (malformed === "offset") head.setUint32(8, job.sent + 1);
        else new Uint8Array(frame)[16] ^= 1;
      }
      job.sent += bytes.byteLength;
      job.credit -= bytes.byteLength;
      this.emit(frame);
    }
    pumpDownload(job) {
      while (job.sent < job.bytes) {
        const length = Math.min(manifest.chunk_bytes, job.bytes - job.sent);
        if (job.credit < length) break;
        this.frame(job, data(job.id, job.sent, length));
      }
      if (job.sent === job.bytes && !job.completed) {
        job.completed = true;
        this.control({ op: "complete", id: job.id, bytes: job.bytes, sha256: job.sha256 });
      }
    }
    create(id) {
      assert(!state.jobs.has(id));
      const spec = manifest.jobs.find(value => value.id === id);
      const job = { ...spec, received: 0, sent: 0, credit: manifest.receive_window,
                    completed: false, hash: crypto.createHash("sha256") };
      state.jobs.set(id, job);
      state.peakJobs = Math.max(state.peakJobs, state.jobs.size);
      return job;
    }
    ready(job) {
      this.control({ op: "ready", id: job.id, kind: job.kind, bytes: job.bytes,
                     credit: manifest.receive_window });
      if (job.kind === "download") this.pumpDownload(job);
    }
    receive(value) {
      if (typeof value !== "string") {
        state.clientBinary += 1;
        const head = new DataView(value);
        const job = state.jobs.get(head.getUint32(4));
        assert(job && job.kind !== "download");
        const offset = head.getUint32(8);
        const length = head.getUint32(12);
        assert.equal(head.getUint32(0), 0x01000000);
        assert.equal(offset, job.received);
        assert.equal(value.byteLength, length + 16);
        assert.equal(length, Math.min(manifest.chunk_bytes, job.bytes - offset));
        const payload = new Uint8Array(value, 16);
        assert.deepEqual(payload, data(job.id, offset, length));
        job.hash.update(payload);
        job.received += length;
        this.control({ op: "credit", id: job.id, bytes: length });
        if (job.kind === "echo") this.frame(job, payload);
        return;
      }
      state.clientControls += 1;
      const message = JSON.parse(value);
      if (message.op === "open_batch") {
        assert.deepEqual(message.ids, [3, 4, 5, 6]);
        const jobs = message.ids.map(id => this.create(id));
        for (const job of jobs) this.ready(job);
        return;
      }
      if (message.op === "open") {
        assert(![3, 4, 5, 6].includes(message.id));
        const job = this.create(message.id);
        assert.equal(message.kind, job.kind);
        assert.equal(message.bytes, job.bytes);
        this.ready(job);
        return;
      }
      const job = state.jobs.get(message.id);
      assert(job, "control for unknown job");
      if (message.op === "credit") {
        job.credit += message.bytes;
        assert(job.credit <= manifest.receive_window);
        if (job.kind === "download") this.pumpDownload(job);
      } else if (message.op === "fin") {
        assert.equal(job.received, job.bytes);
        assert.equal(message.sha256, job.sha256);
        assert.equal(job.hash.digest("hex"), job.sha256);
        job.completed = true;
        this.control({ op: "complete", id: job.id, bytes: job.bytes, sha256: job.sha256 });
      } else if (message.op === "done") {
        assert(job.completed);
        assert.equal(message.bytes, job.bytes);
        assert.equal(message.sha256, job.sha256);
        state.done.push(job.id);
        state.jobs.delete(job.id);
      } else {
        assert.fail("unexpected client control");
      }
    }
    send(value) {
      assert.equal(this.readyState, Socket.OPEN);
      const length = typeof value === "string" ? Buffer.byteLength(value) : value.byteLength;
      this.bufferedAmount += length;
      assert(this.bufferedAmount <= 2 * manifest.receive_window);
      setImmediate(() => {
        this.bufferedAmount -= length;
        if (this.readyState !== Socket.OPEN) return;
        try { this.receive(value); } catch (error) { state.errors.push(error); }
      });
    }
    close(code) {
      this.readyState = Socket.CLOSING;
      setImmediate(() => {
        this.readyState = Socket.CLOSED;
        this.onclose({ code, wasClean: code === 1000 });
      });
    }
  }

  function response(value) {
    let read = false;
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    return { status: 200, body: { getReader() { return {
      async read() {
        if (read) return { done: true };
        read = true;
        return { done: false, value: bytes };
      },
      async cancel() {},
      releaseLock() {},
    }; } } };
  }

  const window = { addEventListener(name, listener) {
    assert.equal(name, "load");
    state.loadListeners.push(listener);
  } };
  const document = { readyState: delayLoad ? "loading" : "complete", getElementById() { return null; } };
  const context = {
    window, document, performance,
    location: { href: "https://app.invalid/" + (hold ? "#hold" : ""), protocol: "https:", hash: hold ? "#hold" : "" },
    URL, WebSocket: Socket, TextEncoder, TextDecoder, Uint8Array, ArrayBuffer, DataView,
    AbortController, Map, Set, console,
    crypto: { subtle: { digest(...args) {
      state.hashes += 1;
      if (badDigest && state.hashes === 2) return Promise.resolve(new ArrayBuffer(32));
      return crypto.webcrypto.subtle.digest(...args);
    } } },
    setTimeout(callback, milliseconds) { return setTimeout(callback, milliseconds < 120000 ? 0 : milliseconds); },
    clearTimeout,
    async fetch(url, options) {
      const round = Number(url.split("/").pop());
      const cursor = round * manifest.catalog_records_per_round;
      assert.equal(round, Math.floor(state.bootstrap / 2));
      const post = state.bootstrap % 2 === 0;
      assert.equal(options.method === "POST", post);
      state.bootstrap += 1;
      if (post) {
        assert.deepEqual(JSON.parse(options.body), {
          cursor, preferences: { order: "ascending", page_size: 64 }, manifest_sha256: manifestSHA,
        });
        const accepted = { accepted_cursor: cursor, count: 64, manifest_sha256: manifestSHA };
        if (round === 0) {
          accepted.assets = [
            ["/assets/site.css", 12288], ["/assets/app.js", 24576],
            ...[1, 2, 3, 4].map(index => ["/assets/image-" + index + ".svg", 8192]),
          ].map(([assetPath, bytes]) => ({
            path: assetPath, bytes,
            sha256: crypto.createHash("sha256").update(assetPath === "/assets/app.js" ? script : assetPath).digest("hex"),
          }));
        }
        return response(accepted);
      }
      return response({
        cursor, next_cursor: cursor + 64, manifest_sha256: manifestSHA,
        records: Array.from({ length: 64 }, (_, index) => {
          const id = cursor + index + 1;
          return { id, title: "Archive item " + String(id).padStart(6, "0"), group: id % 8,
                   revision: 1 + id % 97, chunk_bytes: 65536, source_job: ((id - 1) % 11) + 1 };
        }),
      });
    },
  };
  vm.runInNewContext(script, context, { filename: "app.js" });
  if (delayLoad) {
    await until(() => state.loadListeners.length || window.__NFB_ERROR__);
    assert.equal(state.bootstrap, 0);
    assert.equal(window.__NFB_READY__, false);
    document.readyState = "complete";
    for (const listener of state.loadListeners) listener();
  }
  if (hold) {
    await until(() => window.__NFB_READY__ || window.__NFB_ERROR__);
    assert.equal(state.bootstrap, 0);
    assert.equal(state.websockets, 0);
    const first = window.__NFB_RUN__();
    assert.equal(window.__NFB_RUN__(), first);
    first.catch(() => {});
  }
  await until(() => window.__NFB_RESULT__ || window.__NFB_ERROR__ || state.errors.length);
  assert.deepEqual(state.errors, []);
  if (malformed || badDigest) {
    assert.equal(window.__NFB_ERROR__, badDigest ? "job_digest_mismatch" :
      malformed === "offset" ? "binary_offset_or_credit_invalid" : "binary_payload_invalid");
    assert.equal(window.__NFB_RESULT__, null);
    assert.equal(state.done.length, 0);
    return;
  }
  assert.equal(window.__NFB_ERROR__, null);
  const result = window.__NFB_RESULT__;
  assert.equal(result.manifest_sha256, manifestSHA);
  assert.equal(result.app_sha256, crypto.createHash("sha256").update(script).digest("hex"));
  assert.equal(result.assets.length, 6);
  assert.equal(result.uploaded_bytes, 1069056);
  assert.equal(result.downloaded_bytes, 10506240);
  assert.equal(result.websocket.opened, 1);
  assert.equal(result.websocket.closed, 1);
  assert.equal(result.websocket.close_code, 1000);
  assert.equal(result.websocket.clean, true);
  assert.equal(result.stages.length, 5);
  assert.equal(result.stages.flatMap(stage => stage.jobs).length, 11);
  assert.equal(state.bootstrap, 40);
  assert.equal(state.websockets, 1);
  assert.equal(state.peakJobs, 4);
  assert.equal(state.hashes, 12);
  assert.equal(state.done.length, 11);
  assert.equal(state.clientControls, 190);
  assert.equal(state.serverControls, 43);
  assert.equal(state.clientBinary, 21);
  assert.equal(state.serverBinary, 165);
  for (const stage of result.stages) {
    assert(stage.io_start_ms <= stage.io_end_ms && stage.io_end_ms <= stage.verified_ms);
    for (const job of stage.jobs) {
      assert(job.io_start_ms <= job.io_end_ms && job.io_end_ms <= job.verified_ms);
      assert.equal(job.sha256, manifest.jobs.find(value => value.id === job.id).sha256);
    }
  }
}

(async () => {
  await exercise({ delayLoad: true });
  await exercise({ hold: true });
  await exercise({ malformed: "offset" });
  await exercise({ malformed: "payload" });
  await exercise({ badDigest: true });
  console.log("PASS shared application: manifest/payload hashes, load milestone, one-shot hold, atomic parallel jobs, exact protocol counts, bounded writer, timestamps, malformed data rejection");
})().catch(error => { console.error(error); process.exitCode = 1; });
