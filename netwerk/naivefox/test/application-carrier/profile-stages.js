"use strict";
(() => {
  window.__NFC_PROFILE_STAGES__ = {};
  function record(name, started) {
    const elapsed = performance.now() - started;
    const table = window.__NFC_PROFILE_STAGES__;
    const value = table[name] || (table[name] = {count: 0, total_ms: 0, max_ms: 0});
    value.count++; value.total_ms += elapsed; value.max_ms = Math.max(value.max_ms, elapsed);
  }
  const originalFetch = window.fetch;
  window.fetch = async function(input, options) {
    const path = new URL(input, location.href).pathname;
    const method = options && options.method || "GET";
    const label = path === "/api/events/idle" ? "idle" : method === "POST" ? "upload" : "download";
    const started = performance.now();
    try { return await originalFetch.call(this, input, options); }
    finally { record("fetch_headers_" + label, started); }
  };
  const originalRead = Response.prototype.arrayBuffer;
  Response.prototype.arrayBuffer = async function() {
    const started = performance.now();
    try { return await originalRead.call(this); }
    finally { record("body_read", started); }
  };
  const originalSend = WebSocket.prototype.send;
  const sockets = new WeakMap();
  WebSocket.prototype.send = function(body) {
    let state = sockets.get(this);
    if (!state) {
      state = {pending: null}; sockets.set(this, state);
      this.addEventListener("message", event => {
        const bytes = new Uint8Array(event.data);
        if (bytes.length === 1 && bytes[0] === 4) return;
        if (state.pending) { record(state.pending.name, state.pending.started); state.pending = null; }
      }, {capture: true});
    }
    const opcode = body instanceof ArrayBuffer ? new Uint8Array(body)[0] : body[0];
    const label = {1: "take", 2: "deliver", 5: "pressure"}[opcode] || "other";
    state.pending = {name: "ipc_" + label, started: performance.now()};
    return originalSend.call(this, body);
  };
})();
