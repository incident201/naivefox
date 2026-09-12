(() => {
  "use strict";

  const manifest = __MANIFEST_JSON__;
  const manifestSHA = "__MANIFEST_SHA256__";
  const encoder = new TextEncoder();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const catalog = [];
  let assetInventory = [];
  let consumer = null;
  const assetSizes = new Map([
    ["/assets/site.css", 12288], ["/assets/app.js", 24576],
    ...[1, 2, 3, 4].map(index => ["/assets/image-" + index + ".svg", 8192]),
  ]);
  const stageResults = [];
  let deadline = null;
  let terminal = false;
  let running = null;
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
    if (deadline !== null) clearTimeout(deadline);
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

  async function httpStatus(path) {
    const response = await fetch(path, { method: "POST", credentials: "same-origin",
      cache: "no-store", signal: aborter.signal });
    check(response.status === 204, "workload_lifecycle_failure");
  }

  async function runJob(spec) {
    const upload = spec.kind === "download" ? null : generated(spec.id, spec.bytes);
    const ioStart = performance.now();
    const response = await fetch("/app/api/work/job/" + spec.id, {
      method: upload ? "POST" : "GET", credentials: "same-origin",
      cache: "no-store", signal: aborter.signal,
      headers: upload ? { "Content-Type": "application/octet-stream" } : {},
      body: upload,
    });
    check(response.status === 200 && response.body, "job_http_failure");
    const expected = spec.kind === "upload" ? 0 : spec.bytes;
    const bytes = new Uint8Array(expected);
    const reader = response.body.getReader();
    let received = 0;
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        check(received + part.value.byteLength <= expected, "job_response_overflow");
        bytes.set(part.value, received);
        received += part.value.byteLength;
      }
    } finally { reader.releaseLock(); }
    const ioEnd = performance.now();
    check(received === expected && response.headers.get("X-Content-SHA256") === spec.sha256,
          "job_response_mismatch");
    if (expected) check(await digest(bytes) === spec.sha256, "job_hash_mismatch");
    const verified = performance.now();
    const entries = performance.getEntriesByName(new URL("/app/api/work/job/" + spec.id, location.href).href);
    check(entries.length === 1 && entries[0].responseStatus === 200 &&
          entries[0].decodedBodySize === expected, "job_native_http_timing_missing");
    return { ...spec, io_start_ms: ioStart, io_end_ms: ioEnd, verified_ms: verified,
      sent_bytes: upload ? spec.bytes : 0, received_bytes: received,
      next_hop_protocol: entries[0].nextHopProtocol };
  }

  async function runStage(stage) {
    const prepared = stage.job_ids.map(id => manifest.jobs.find(job => job.id === id));
    const ioStart = performance.now();
    const jobs = [];
    if (stage.parallel) jobs.push(...await Promise.all(prepared.map(runJob)));
    else for (const spec of prepared) jobs.push(await runJob(spec));
    check(!terminal, "stage_after_failure");
    stageResults.push({
      name: stage.name, io_start_ms: ioStart,
      io_end_ms: Math.max(...jobs.map(job => job.io_end_ms)),
      verified_ms: Math.max(...jobs.map(job => job.verified_ms)),
      useful_bytes: jobs.reduce((sum, job) => sum + job.bytes, 0),
      sent_bytes: jobs.reduce((sum, job) => sum + job.sent_bytes, 0),
      received_bytes: jobs.reduce((sum, job) => sum + job.received_bytes, 0), jobs,
    });
    status("Verified stage: " + stage.name);
  }

  function wait(milliseconds) { return new Promise(resolve => setTimeout(resolve, milliseconds)); }

  async function runApplication() {
    deadline = setTimeout(() => stop(new AppError("application_timeout")), 120000);
    await bootstrap();
    await httpStatus("/app/api/work/start");
    const opened = performance.now();
    await wait(manifest.idle_before_ms);
    for (const stage of manifest.stages) {
      if (stage.name === "wake") await wait(manifest.idle_wake_ms);
      await runStage(stage);
    }
    await httpStatus("/app/api/work/end");
    const closed = performance.now();
    if (deadline !== null) clearTimeout(deadline);
    const jobs = stageResults.flatMap(stage => stage.jobs);
    window.__NFB_RESULT__ = {
      manifest_sha256: manifestSHA, time_origin_ms: performance.timeOrigin,
      uploaded_bytes: jobs.reduce((sum, job) => sum + job.sent_bytes, 0),
      downloaded_bytes: jobs.reduce((sum, job) => sum + job.received_bytes, 0),
      app_sha256: assetInventory.find(asset => asset.path === "/assets/app.js").sha256,
      assets: assetInventory, consumer, stages: stageResults,
      http: { opened: 1, closed: 1, open_ms: opened, close_ms: closed, requests: 13 },
    };
    status("All HTTP archive jobs verified");
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
    check(manifest.version === 1 && manifest.protocol === "nfbench.http" &&
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
