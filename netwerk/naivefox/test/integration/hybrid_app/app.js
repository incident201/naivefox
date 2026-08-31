(() => {
"use strict";

const manifest = {"bootstrap_rounds":20,"catalog_records_per_round":64,"chunk_bytes":65536,"idle_before_ms":2000,"idle_wake_ms":2000,"jobs":[{"bytes":8388608,"id":1,"kind":"download","sha256":"651c5cd36d6fad96ce3e6420a26876214ff384da29890e2aeeab7ebf89283041"},{"bytes":1048576,"id":2,"kind":"upload","sha256":"e35d674f5de24ea0c9557d03e8b5463dd5466336cbb2fb1867e8e152a82ab640"},{"bytes":524288,"id":3,"kind":"download","sha256":"776b2c281daba414865c0ea895a03b08f201fb6f0f0da24202971c18de8066e9"},{"bytes":524288,"id":4,"kind":"download","sha256":"795fca6f51b4cd1a30214f2bd2fdfd4843b68cf3bb5716b910d31ba362373ad6"},{"bytes":524288,"id":5,"kind":"download","sha256":"5994e8eec2c5da4bb498644413e4de1d054d5f948d94a3f85e7bedc409ae3e73"},{"bytes":524288,"id":6,"kind":"download","sha256":"92198f0ccc3a566a6d0eae99f596e68df7a1cdd10f91a490f1e13092778ce89c"},{"bytes":4096,"id":7,"kind":"echo","sha256":"f5cceb71f4558ca74183f89d4d0f12ff6e417eb477a2d0108d46fd2341d45749"},{"bytes":4096,"id":8,"kind":"echo","sha256":"a8ea093f033c876ce4a0a23293d3a1fa8c871bdeb329d85b26e048b5390582c9"},{"bytes":4096,"id":9,"kind":"echo","sha256":"16a817b07dda0d2916595525fa465e77a17c742dd2b760b722ec63421dd02cc3"},{"bytes":4096,"id":10,"kind":"echo","sha256":"ff5a00a18b06910fc7ed7aaaa1e150bddd6b4cef960a5f9d7ff58a66f28676e4"},{"bytes":4096,"id":11,"kind":"echo","sha256":"a1388648f821d1a5df821613984c0da357e8b38fd9a66126bd3f398cba23b8db"}],"max_jobs":4,"name":"nfbench.app","payload_algorithm":"u8(id*17+offset*31+(offset>>8))","protocol":"nfbench.app.v1","receive_window":524288,"stages":[{"job_ids":[1],"name":"download","parallel":false},{"job_ids":[2],"name":"upload","parallel":false},{"job_ids":[3,4,5,6],"name":"parallel","parallel":true},{"job_ids":[7,8,9,10],"name":"small","parallel":false},{"job_ids":[11],"name":"wake","parallel":false}],"version":1};
const manifestSHA = "3caeae3d8a8509d1453bcebda06150a63fe39b72c255f8a14ffd838abb1ce525";
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const active = new Map();
const controls = [];
const catalog = [];
let assetInventory = [];
let consumer = null;
const assetSizes = new Map([
  ["/assets/site.css", 12288], ["/assets/app.js", 24576],
  ...[1, 2, 3, 4].map(index => ["/assets/image-" + index + ".svg", 8192]),
]);
const stageResults = [];
const websocket = {
  opened: 0, closed: 0, open_ms: null, close_ms: null, close_code: null, clean: false,
  binary_messages_sent: 0, binary_messages_received: 0,
  control_messages_sent: 0, control_messages_received: 0,
};
const wireBudget = manifest.receive_window * 2;
let socket = null;
let pumpTimer = null;
let deadline = null;
let pumping = false;
let terminal = false;
let finished = false;
let running = null;
let resolveOpen;
let rejectOpen;
let resolveClose;
let rejectClose;
const aborter = new AbortController();

window.__NFB_READY__ = false;
window.__NFB_ERROR__ = null;
window.__NFB_RESULT__ = null;

class AppError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function check(condition, code) {
  if (!condition) throw new AppError(code);
}

function object(value, keys, code) {
  check(value !== null && typeof value === "object" && !Array.isArray(value), code);
  const actual = Object.keys(value).sort();
  const expected = keys.slice().sort();
  check(actual.length === expected.length && actual.every((key, index) => key === expected[index]), code);
}

function integer(value, minimum, maximum, code) {
  check(Number.isSafeInteger(value) && value >= minimum && value <= maximum, code);
}

function status(text) {
  const target = document.getElementById("nfbench-status");
  if (target) target.textContent = text;
}

function stop(error) {
  if (terminal) return;
  terminal = true;
  const failure = error instanceof AppError ? error : new AppError("application_failure");
  window.__NFB_ERROR__ = failure.code;
  status("Application failed");
  aborter.abort();
  if (pumpTimer !== null) clearTimeout(pumpTimer);
  if (deadline !== null) clearTimeout(deadline);
  pumpTimer = null;
  deadline = null;
  controls.length = 0;
  if (rejectOpen) rejectOpen(failure);
  if (rejectClose) rejectClose(failure);
  for (const job of active.values()) job.reject(failure);
  active.clear();
  if (socket && socket.readyState < WebSocket.CLOSING) {
    try { socket.close(1002, "application protocol failure"); } catch (_) {}
  }
}

function hexadecimal(buffer) {
  return Array.from(new Uint8Array(buffer), byte => byte.toString(16).padStart(2, "0")).join("");
}

async function digest(bytes) {
  return hexadecimal(await crypto.subtle.digest("SHA-256", bytes));
}

function byteAt(id, offset) {
  return (id * 17 + offset * 31 + (offset >>> 8)) & 255;
}

function generated(id, length) {
  const bytes = new Uint8Array(length);
  for (let offset = 0; offset < length; ++offset) bytes[offset] = byteAt(id, offset);
  return bytes;
}

async function jsonBody(response) {
  check(response.status === 200 && response.body !== null, "bootstrap_http_failure");
  const reader = response.body.getReader();
  const parts = [];
  let length = 0;
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      length += item.value.byteLength;
      if (length > 65536) {
        await reader.cancel();
        throw new AppError("bootstrap_body_limit");
      }
      parts.push(item.value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    bytes.set(part, offset);
    offset += part.byteLength;
  }
  try { return JSON.parse(decoder.decode(bytes)); }
  catch (_) { throw new AppError("bootstrap_json_invalid"); }
}

async function bootstrap() {
  status("Loading archive catalog");
  for (let round = 0; round < manifest.bootstrap_rounds; ++round) {
    const cursor = round * manifest.catalog_records_per_round;
    const url = "/app/api/bootstrap/" + round;
    const accepted = await jsonBody(await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      signal: aborter.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cursor,
        preferences: { order: "ascending", page_size: manifest.catalog_records_per_round },
        manifest_sha256: manifestSHA,
      }),
    }));
    object(accepted, round === 0
      ? ["accepted_cursor", "count", "manifest_sha256", "assets"]
      : ["accepted_cursor", "count", "manifest_sha256"], "bootstrap_post_shape");
    if (round === 0) {
      check(Array.isArray(accepted.assets) && accepted.assets.length === assetSizes.size, "asset_inventory_shape");
      const paths = new Set();
      assetInventory = accepted.assets.map(asset => {
        object(asset, ["path", "bytes", "sha256"], "asset_inventory_entry");
        check(assetSizes.get(asset.path) === asset.bytes && !paths.has(asset.path) &&
              typeof asset.sha256 === "string" && /^[a-f0-9]{64}$/.test(asset.sha256), "asset_inventory_mismatch");
        paths.add(asset.path);
        return { path: asset.path, bytes: asset.bytes, sha256: asset.sha256 };
      });
      check(paths.size === assetSizes.size, "asset_inventory_incomplete");
    }
    check(accepted.accepted_cursor === cursor &&
          accepted.count === manifest.catalog_records_per_round &&
          accepted.manifest_sha256 === manifestSHA, "bootstrap_post_mismatch");
    const page = await jsonBody(await fetch(url, {
      credentials: "same-origin", cache: "no-store", signal: aborter.signal,
    }));
    object(page, ["cursor", "records", "next_cursor", "manifest_sha256"], "bootstrap_get_shape");
    check(page.cursor === cursor && page.next_cursor === cursor + manifest.catalog_records_per_round &&
          page.manifest_sha256 === manifestSHA && Array.isArray(page.records) &&
          page.records.length === manifest.catalog_records_per_round, "bootstrap_get_mismatch");
    for (let index = 0; index < page.records.length; ++index) {
      const record = page.records[index];
      const id = cursor + index + 1;
      object(record, ["id", "title", "group", "revision", "chunk_bytes", "source_job"], "catalog_record_shape");
      check(record.id === id && record.title === "Archive item " + String(id).padStart(6, "0") &&
            record.group === id % 8 && record.revision === 1 + id % 97 &&
            record.chunk_bytes === manifest.chunk_bytes &&
            record.source_job === ((id - 1) % manifest.jobs.length) + 1, "catalog_record_mismatch");
      catalog.push(record);
    }
    check(catalog.length === page.next_cursor, "catalog_cursor_mismatch");
    status("Archive catalog: " + catalog.length + " records");
  }
  check(catalog.length === manifest.bootstrap_rounds * manifest.catalog_records_per_round, "catalog_incomplete");
}

function canTransmit(job) {
  if (job.phase !== "ready" || job.kind === "download" || job.finQueued) return false;
  const length = Math.min(manifest.chunk_bytes, job.bytes - job.txOffset);
  return length > 0 && job.txCredit >= length;
}

function schedulePump() {
  if (terminal || pumpTimer !== null) return;
  pumpTimer = setTimeout(() => {
    pumpTimer = null;
    pump();
  }, 2);
}

function sendControl(value, sent) {
  check(!terminal && socket !== null && socket.readyState === WebSocket.OPEN, "control_after_close");
  const text = JSON.stringify(value);
  const length = encoder.encode(text).byteLength;
  check(length <= 1024 && controls.length < 64, "control_queue_limit");
  controls.push({ text, length, sent });
  pump();
}

function pump() {
  if (terminal || pumping || !socket || socket.readyState !== WebSocket.OPEN) return;
  pumping = true;
  try {
    while (!terminal) {
      while (controls.length) {
        const item = controls[0];
        if (socket.bufferedAmount + item.length > wireBudget) {
          schedulePump();
          return;
        }
        controls.shift();
        socket.send(item.text);
        websocket.control_messages_sent += 1;
        if (item.sent) item.sent();
      }
      let progress = false;
      for (const job of active.values()) {
        if (!canTransmit(job)) continue;
        const length = Math.min(manifest.chunk_bytes, job.bytes - job.txOffset);
        if (socket.bufferedAmount + length + 16 > wireBudget) {
          schedulePump();
          return;
        }
        const frame = new ArrayBuffer(16 + length);
        const head = new DataView(frame);
        head.setUint8(0, 1);
        head.setUint32(4, job.id);
        head.setUint32(8, job.txOffset);
        head.setUint32(12, length);
        new Uint8Array(frame, 16).set(job.tx.subarray(job.txOffset, job.txOffset + length));
        job.txCredit -= length;
        job.txOffset += length;
        socket.send(frame);
        websocket.binary_messages_sent += 1;
        progress = true;
        if (job.txOffset === job.bytes) {
          job.finQueued = true;
          sendControl({ op: "fin", id: job.id, bytes: job.bytes, sha256: job.sha256 },
                      () => { job.finSent = true; });
        }
      }
      if (!progress && !controls.length) break;
    }
  } catch (error) {
    stop(error);
  } finally {
    pumping = false;
  }
}

function receiveBinary(frame) {
  check(frame instanceof ArrayBuffer && frame.byteLength >= 17 &&
        frame.byteLength <= manifest.chunk_bytes + 16, "binary_size_invalid");
  const head = new DataView(frame);
  check(head.getUint8(0) === 1 && head.getUint8(1) === 0 &&
        head.getUint8(2) === 0 && head.getUint8(3) === 0, "binary_header_invalid");
  const id = head.getUint32(4);
  const offset = head.getUint32(8);
  const length = head.getUint32(12);
  const job = active.get(id);
  check(job && job.phase === "ready" && job.kind !== "upload", "binary_job_invalid");
  check(length > 0 && length <= manifest.chunk_bytes && frame.byteLength === 16 + length &&
        offset === job.rxOffset && length <= job.bytes - offset &&
        length <= job.rxCredit, "binary_offset_or_credit_invalid");
  const bytes = new Uint8Array(frame, 16);
  job.rxCredit -= length;
  for (let index = 0; index < length; ++index) {
    check(bytes[index] === byteAt(id, offset + index), "binary_payload_invalid");
  }
  job.rx.set(bytes, offset);
  job.rxOffset += length;
  websocket.binary_messages_received += 1;
  job.rxCredit += length;
  check(job.rxCredit <= manifest.receive_window, "receive_credit_overflow");
  sendControl({ op: "credit", id, bytes: length });
}

async function verifyComplete(job) {
  const bytes = job.kind === "upload" ? job.tx : job.rx;
  const actual = await digest(bytes);
  check(!terminal && actual === job.sha256, "job_digest_mismatch");
  const result = {
    id: job.id, kind: job.kind, bytes: job.bytes, sha256: actual,
    sent_bytes: job.txOffset,
    received_bytes: job.rxOffset,
    io_start_ms: job.ioStart, io_end_ms: job.ioEnd,
    verified_ms: performance.now(),
  };
  sendControl({ op: "done", id: job.id, bytes: job.bytes, sha256: actual });
  active.delete(job.id);
  job.tx = null;
  job.rx = null;
  job.resolve(result);
}

function receiveControl(text) {
  check(text.length <= 1024 && encoder.encode(text).byteLength <= 1024, "control_size_invalid");
  let value;
  try { value = JSON.parse(text); }
  catch (_) { throw new AppError("control_json_invalid"); }
  check(value !== null && typeof value === "object" && !Array.isArray(value), "control_shape_invalid");
  integer(value.id, 1, manifest.jobs.length, "control_id_invalid");
  const job = active.get(value.id);
  check(job, "control_job_unknown");
  websocket.control_messages_received += 1;
  if (value.op === "ready") {
    object(value, ["op", "id", "kind", "bytes", "credit"], "ready_shape_invalid");
    check(job.phase === "opening" && value.kind === job.kind && value.bytes === job.bytes &&
          value.credit === manifest.receive_window, "ready_mismatch");
    job.phase = "ready";
    job.txCredit = value.credit;
    pump();
  } else if (value.op === "credit") {
    object(value, ["op", "id", "bytes"], "credit_shape_invalid");
    integer(value.bytes, 1, manifest.receive_window, "credit_size_invalid");
    check(job.phase !== "opening" && job.kind !== "download" &&
          job.txCredit + value.bytes <= manifest.receive_window, "credit_window_invalid");
    job.txCredit += value.bytes;
    pump();
  } else if (value.op === "complete") {
    object(value, ["op", "id", "bytes", "sha256"], "complete_shape_invalid");
    check(job.phase === "ready" && value.bytes === job.bytes &&
          value.sha256 === job.sha256, "complete_mismatch");
    check((job.kind === "upload" || job.rxOffset === job.bytes) &&
          (job.kind === "download" || (job.txOffset === job.bytes && job.finSent)), "complete_before_io");
    job.ioEnd = performance.now();
    job.phase = "verifying";
    verifyComplete(job).catch(stop);
  } else {
    throw new AppError("control_operation_invalid");
  }
}

function openWebSocket() {
  const opened = new Promise((resolve, reject) => { resolveOpen = resolve; rejectOpen = reject; });
  const closed = new Promise((resolve, reject) => { resolveClose = resolve; rejectClose = reject; });
  closed.catch(() => {});
  const url = new URL("/api/realtime", location.href);
  url.protocol = "wss:";
  socket = new WebSocket(url.href, manifest.protocol);
  socket.binaryType = "arraybuffer";
  socket.onopen = () => {
    try {
      check(socket.protocol === manifest.protocol && websocket.opened === 0, "websocket_protocol_invalid");
      websocket.opened = 1;
      websocket.open_ms = performance.now();
      resolveOpen();
    } catch (error) { stop(error); }
  };
  socket.onmessage = event => {
    if (terminal) return;
    try {
      if (typeof event.data === "string") receiveControl(event.data);
      else receiveBinary(event.data);
    } catch (error) { stop(error); }
  };
  socket.onerror = () => stop(new AppError("websocket_error"));
  socket.onclose = event => {
    websocket.closed += 1;
    websocket.close_ms = performance.now();
    websocket.close_code = event.code;
    websocket.clean = event.wasClean;
    if (terminal) return;
    if (!finished || active.size || controls.length || event.code !== 1000 ||
        !event.wasClean || websocket.closed !== 1) {
      stop(new AppError("websocket_close_invalid"));
      return;
    }
    resolveClose();
  };
  return { opened, closed };
}

function prepareJob(spec) {
  check(catalog.some(record => record.source_job === spec.id), "job_not_in_catalog");
  return {
    ...spec, phase: "opening", txCredit: 0, rxCredit: manifest.receive_window,
    txOffset: 0, rxOffset: 0, finQueued: false, finSent: false,
    tx: spec.kind === "download" ? null : generated(spec.id, spec.bytes),
    rx: spec.kind === "upload" ? null : new Uint8Array(spec.bytes),
  };
}

function registerJob(job) {
  check(!terminal && active.size < manifest.max_jobs && !active.has(job.id), "active_job_limit");
  const result = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
  active.set(job.id, job);
  job.ioStart = performance.now();
  return result;
}

function startJob(job) {
  const result = registerJob(job);
  sendControl({ op: "open", id: job.id, kind: job.kind, bytes: job.bytes });
  return result;
}

async function runStage(stage) {
  const prepared = stage.job_ids.map(id => prepareJob(manifest.jobs.find(job => job.id === id)));
  const ioStart = performance.now();
  let jobs;
  if (stage.parallel) {
    const pending = prepared.map(registerJob);
    sendControl({ op: "open_batch", ids: stage.job_ids });
    jobs = await Promise.all(pending);
  } else {
    jobs = [];
    for (const job of prepared) jobs.push(await startJob(job));
  }
  check(!terminal, "stage_after_failure");
  const result = {
    name: stage.name, io_start_ms: ioStart,
    io_end_ms: Math.max(...jobs.map(job => job.io_end_ms)),
    verified_ms: Math.max(...jobs.map(job => job.verified_ms)),
    useful_bytes: jobs.reduce((sum, job) => sum + job.bytes, 0),
    sent_bytes: jobs.reduce((sum, job) => sum + job.sent_bytes, 0),
    received_bytes: jobs.reduce((sum, job) => sum + job.received_bytes, 0),
    jobs,
  };
  stageResults.push(result);
  status("Verified stage: " + stage.name);
}

function wait(milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds));
}

async function runApplication() {
  deadline = setTimeout(() => stop(new AppError("application_timeout")), 120000);
  await bootstrap();
  check(!terminal, "bootstrap_cancelled");
  const connection = openWebSocket();
  await connection.opened;
  await wait(manifest.idle_before_ms);
  for (const stage of manifest.stages) {
    if (stage.name === "wake") await wait(manifest.idle_wake_ms);
    await runStage(stage);
  }
  check(active.size === 0 && !terminal, "application_jobs_incomplete");
  while (controls.length || socket.bufferedAmount) {
    pump();
    await wait(2);
    check(!terminal, "application_flush_failure");
  }
  finished = true;
  socket.close(1000);
  await connection.closed;
  if (deadline !== null) clearTimeout(deadline);
  if (pumpTimer !== null) clearTimeout(pumpTimer);
  deadline = null;
  pumpTimer = null;
  const observedJobs = stageResults.flatMap(stage => stage.jobs);
  const uploaded = observedJobs.reduce((sum, job) => sum + job.sent_bytes, 0);
  const downloaded = observedJobs.reduce((sum, job) => sum + job.received_bytes, 0);
  const expectedUp = manifest.jobs.reduce((sum, job) => sum + (job.kind === "download" ? 0 : job.bytes), 0);
  const expectedDown = manifest.jobs.reduce((sum, job) => sum + (job.kind === "upload" ? 0 : job.bytes), 0);
  check(observedJobs.length === manifest.jobs.length &&
        new Set(observedJobs.map(job => job.id)).size === manifest.jobs.length &&
        stageResults.length === manifest.stages.length &&
        stageResults.every((stage, index) => stage.name === manifest.stages[index].name) &&
        uploaded === expectedUp && downloaded === expectedDown &&
        assetInventory.length === 6, "application_totals_mismatch");
  window.__NFB_RESULT__ = {
    manifest_sha256: manifestSHA, time_origin_ms: performance.timeOrigin,
    uploaded_bytes: uploaded, downloaded_bytes: downloaded,
    app_sha256: assetInventory.find(asset => asset.path === "/assets/app.js").sha256,
    assets: assetInventory, consumer,
    stages: stageResults, websocket: { ...websocket },
  };
  status("All archive jobs verified");
  return window.__NFB_RESULT__;
}

async function consumerProof() {
  check(typeof performance.getEntriesByType === "function", "consumer_timing_unavailable");
  const timing = (entry, size) => {
    check(entry && typeof entry.responseStatus === "number" &&
      typeof entry.decodedBodySize === "number" && typeof entry.nextHopProtocol === "string",
      "consumer_timing_unavailable");
    check(entry.responseStatus === 200 && entry.decodedBodySize === size &&
      entry.nextHopProtocol.length > 0, "consumer_body_incomplete");
    return { decoded_body_size: entry.decodedBodySize, response_status: entry.responseStatus,
      next_hop_protocol: entry.nextHopProtocol };
  };
  const path = name => {
    const url = new URL(name);
    check(url.origin === location.origin, "consumer_origin_mismatch");
    return url.pathname;
  };
  const navigation = performance.getEntriesByType("navigation");
  check(navigation.length === 1 && path(navigation[0].name) === "/", "consumer_navigation_invalid");
  const resources = performance.getEntriesByType("resource").filter(entry => {
    const url = new URL(entry.name);
    return url.origin === location.origin && assetSizes.has(url.pathname);
  });
  check(resources.length === 6 && new Set(resources.map(entry => path(entry.name))).size === 6,
    "consumer_resources_incomplete");
  const images = Array.from(document.images).filter(image => {
    const url = new URL(image.currentSrc);
    return url.origin === location.origin && /^\/assets\/image-[1-4]\.svg$/.test(url.pathname);
  });
  check(images.length === 4 && new Set(images.map(image => path(image.currentSrc))).size === 4,
    "consumer_images_incomplete");
  const decoded = [];
  for (const image of images) {
    check(image.complete && image.naturalWidth > 0 && image.naturalHeight > 0 &&
      typeof image.decode === "function", "consumer_image_incomplete");
    await image.decode();
    decoded.push({ path: path(image.currentSrc), complete: image.complete,
      natural_width: image.naturalWidth, natural_height: image.naturalHeight, decoded: true });
  }
  const styles = Array.from(document.querySelectorAll("link[rel~=stylesheet]")).filter(
    link => path(link.href) === "/assets/site.css");
  check(styles.length === 1 && !styles[0].disabled && styles[0].sheet &&
    styles[0].sheet.cssRules.length > 0, "consumer_stylesheet_incomplete");
  return { navigation: timing(navigation[0], 4096),
    resources: resources.map(entry => ({ path: path(entry.name), ...timing(entry, assetSizes.get(path(entry.name))) })),
    images: decoded, stylesheet_loaded: true, collected_ms: performance.now() };
}

const initialized = (async () => {
  check(location.protocol === "https:" && crypto.subtle, "secure_context_required");
  check(manifest.version === 1 && manifest.protocol === "nfbench.app.v1" &&
        manifest.chunk_bytes === 65536 && manifest.receive_window === 524288 &&
        manifest.max_jobs === 4 && manifest.jobs.length === 11, "manifest_contract_invalid");
  check(await digest(encoder.encode(JSON.stringify(manifest) + "\n")) === manifestSHA,
        "manifest_digest_mismatch");
  if (document.readyState !== "complete") {
    await new Promise(resolve => window.addEventListener("load", resolve, { once: true }));
  }
  consumer = await consumerProof();
  window.__NFB_READY__ = true;
  status("Application ready");
})();

window.__NFB_RUN__ = () => {
  if (!running) {
    running = initialized.then(runApplication);
    running.catch(stop);
  }
  return running;
};
initialized.then(() => {
  if (location.hash !== "#hold") window.__NFB_RUN__().catch(stop);
}).catch(stop);
})();

/*...............................................................................................................................................................................................................................................................................................................................................................................................................................................................
*/
