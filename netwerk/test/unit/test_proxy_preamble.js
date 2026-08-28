/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const { NodeHTTPServer, NodeHTTP2ProxyServer } = ChromeUtils.importESModule(
  "resource://testing-common/NodeServer.sys.mjs"
);

add_setup(function () {
  do_get_profile();
  Services.prefs.setBoolPref("network.proxy.allow_hijacking_localhost", true);
  registerCleanupFunction(() => {
    Services.prefs.clearUserPref("network.proxy.allow_hijacking_localhost");
  });
});

add_task(async function test_proxy_preamble_is_an_ordinary_h2_get() {
  const proxy = new NodeHTTP2ProxyServer();
  await proxy.start();
  registerCleanupFunction(async () => proxy.stop());

  await proxy.execute(`
    global.preambleHeaders = null;
    global.connectHeaders = null;
    global.proxySessionCount = 0;
    global.proxy.on("session", () => global.proxySessionCount++);
    global.proxy.removeAllListeners("stream");
    global.proxy.on("stream", (stream, headers) => {
      if (headers[":method"] === "GET") {
        global.preambleHeaders = { ...headers };
        stream.respond({ ":status": 200, "content-type": "text/plain" });
        stream.end("preamble-ok");
        return;
      }
      global.connectHeaders = { ...headers };
      const { port } = new URL("https://" + headers[":authority"]);
      const socket = require("net").connect(port, "127.0.0.1", () => {
        stream.respond({ ":status": 200 });
        socket.pipe(stream);
        stream.pipe(socket);
      });
    });
  `);

  const channel = NetUtil.newChannel({
    uri: `${proxy.origin()}/`,
    loadUsingSystemPrincipal: true,
    securityFlags:
      Ci.nsILoadInfo.SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
      Ci.nsILoadInfo.SEC_COOKIES_OMIT,
  }).QueryInterface(Ci.nsIHttpChannel);
  const internal = channel.QueryInterface(Ci.nsIHttpChannelInternal);
  Assert.throws(
    () => internal.setProxyPreambleHandshakeDwell(16),
    /NS_ERROR_INVALID_ARG/,
    "the dwell requires a proxy preamble"
  );
  internal.setProxyPreamble();
  Assert.throws(
    () => internal.setProxyPreambleHandshakeDwell(0),
    /NS_ERROR_INVALID_ARG/,
    "the dwell must be positive"
  );
  Assert.throws(
    () => internal.setProxyPreambleHandshakeDwell(101),
    /NS_ERROR_INVALID_ARG/,
    "the dwell is bounded"
  );
  internal.setProxyPreambleHandshakeDwell(16);

  let body = "";
  await new Promise((resolve, reject) => {
    channel.asyncOpen({
      onStartRequest(request) {
        Assert.equal(request.status, Cr.NS_OK);
        Assert.equal(request.responseStatus, 200);
        Assert.equal(request.protocolVersion, "h2");
      },
      onDataAvailable(_request, input, _offset, count) {
        body += NetUtil.readInputStreamToString(input, count);
      },
      onStopRequest(request, status) {
        if (Components.isSuccessCode(status)) {
          resolve();
        } else {
          reject(new Error(`preamble request failed: ${request.status}`));
        }
      },
      QueryInterface: ChromeUtils.generateQI(["nsIStreamListener"]),
    });
  });

  Assert.equal(body, "preamble-ok");
  const headers = await proxy.execute("global.preambleHeaders");
  Assert.equal(headers[":method"], "GET");
  Assert.equal(headers[":scheme"], "https");
  Assert.equal(headers[":authority"], `localhost:${proxy.port()}`);
  Assert.equal(headers[":path"], "/");

  const target = new NodeHTTPServer();
  await target.start();
  registerCleanupFunction(async () => target.stop());
  const tunnelChannel = NetUtil.newChannel({
    uri: `http://localhost:${target.port()}/`,
    loadUsingSystemPrincipal: true,
  });
  const tunnelInternal = tunnelChannel.QueryInterface(
    Ci.nsIHttpChannelInternal
  );
  tunnelInternal.setConnectOnly(false);
  const tunnel = await new Promise((resolve, reject) => {
    tunnelInternal.HTTPUpgrade("", {
      onTransportAvailable(transport, input, output) {
        resolve({ transport, input, output });
      },
      onUpgradeFailed(errorCode) {
        reject(new Error(`proxy CONNECT failed: ${errorCode}`));
      },
      QueryInterface: ChromeUtils.generateQI(["nsIHttpUpgradeListener"]),
    });
    tunnelChannel.asyncOpen({
      onStartRequest() {},
      onDataAvailable() {},
      onStopRequest() {},
      QueryInterface: ChromeUtils.generateQI(["nsIStreamListener"]),
    });
  });
  Assert.equal(await proxy.execute("global.proxySessionCount"), 1);
  const connectHeaders = await proxy.execute("global.connectHeaders");
  Assert.equal(connectHeaders[":method"], "CONNECT");
  Assert.equal(connectHeaders[":authority"], `localhost:${target.port()}`);

  tunnel.input.close();
  tunnel.output.close();
  tunnel.transport.close(Cr.NS_BINDING_ABORTED);
  Assert.throws(
    () => internal.setProxyPreamble(),
    /NS_ERROR_IN_PROGRESS/,
    "the preamble flag is pre-open only"
  );
  Assert.throws(
    () => internal.setProxyPreambleHandshakeDwell(16),
    /NS_ERROR_IN_PROGRESS/,
    "the preamble dwell is pre-open only"
  );
});
