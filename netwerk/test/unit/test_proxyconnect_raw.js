/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const { NodeHTTPServer, NodeHTTPSProxyServer, NodeHTTP2ProxyServer } =
  ChromeUtils.importESModule("resource://testing-common/NodeServer.sys.mjs");
const { TestUtils } = ChromeUtils.importESModule(
  "resource://testing-common/TestUtils.sys.mjs"
);

const RESPONSE_BODY = "raw-tunnel-ok";

function makeChannel(port) {
  return NetUtil.newChannel({
    uri: `http://localhost:${port}/`,
    loadUsingSystemPrincipal: true,
    securityFlags:
      Ci.nsILoadInfo.SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
      Ci.nsILoadInfo.SEC_DONT_FOLLOW_REDIRECTS |
      Ci.nsILoadInfo.SEC_COOKIES_OMIT,
  });
}

function openRawConnect(channel, upgradeListener) {
  const internal = channel.QueryInterface(Ci.nsIHttpChannelInternal);
  internal.setConnectOnly(false);
  internal.HTTPUpgrade("", upgradeListener);
  channel.asyncOpen({
    onStartRequest() {},
    onDataAvailable() {},
    onStopRequest() {},
    QueryInterface: ChromeUtils.generateQI(["nsIStreamListener"]),
  });
}

add_setup(function () {
  do_get_profile();
  Services.prefs.setBoolPref("network.proxy.allow_hijacking_localhost", true);
  registerCleanupFunction(() => {
    Services.prefs.clearUserPref("network.proxy.allow_hijacking_localhost");
  });
});

add_task(async function test_empty_upgrade_protocol_is_connect_only() {
  const internal = makeChannel(80).QueryInterface(Ci.nsIHttpChannelInternal);
  const listener = {
    onTransportAvailable() {},
    onUpgradeFailed() {},
    QueryInterface: ChromeUtils.generateQI(["nsIHttpUpgradeListener"]),
  };
  Assert.throws(
    () => internal.HTTPUpgrade("", listener),
    /NS_ERROR_ILLEGAL_VALUE/,
    "an empty upgrade protocol is restricted to connect-only channels"
  );
});

add_task(async function test_raw_h2_connect_headers() {
  const target = new NodeHTTPServer();
  await target.start();
  const proxy = new NodeHTTP2ProxyServer();
  await proxy.start();
  await proxy.execute(`
    global.rawConnectHeaders = null;
    global.rawConnectAlpn = null;
    global.proxy.on("stream", (stream, headers) => {
      if (headers[":method"] === "CONNECT") {
        global.rawConnectHeaders = { ...headers };
        global.rawConnectAlpn = stream.session.socket.alpnProtocol;
      }
    });
  `);

  const channel = makeChannel(target.port());
  openRawConnect(channel, {
    onTransportAvailable() {},
    onUpgradeFailed() {},
    QueryInterface: ChromeUtils.generateQI(["nsIHttpUpgradeListener"]),
  });

  await TestUtils.waitForCondition(async () =>
    proxy.execute("global.rawConnectHeaders !== null")
  );
  const headers = await proxy.execute("global.rawConnectHeaders");
  Assert.equal(await proxy.execute("global.rawConnectAlpn"), "h2");
  Assert.equal(headers[":method"], "CONNECT");
  Assert.equal(headers[":authority"], `localhost:${target.port()}`);
  Assert.ok(!("alpn" in headers), "CONNECT has no synthetic ALPN");
  Assert.ok(!("upgrade" in headers), "CONNECT has no Upgrade header");
  Assert.ok(!("connection" in headers), "CONNECT has no Connection header");

  channel.cancel(Cr.NS_BINDING_ABORTED);
  await proxy.stop();
  await target.stop();
});

add_task(async function test_raw_https_proxy_streams() {
  const target = new NodeHTTPServer();
  await target.start();
  await target.registerPathHandler("/raw", (_request, response) => {
    response.setHeader("Content-Type", "text/plain");
    response.end("raw-tunnel-ok");
  });

  const proxy = new NodeHTTPSProxyServer();
  await proxy.start();
  await proxy.registerConnectHandler(function (request, clientSocket, head) {
    global.rawConnectMethod = request.method;
    global.rawConnectTarget = request.url;
    global.rawConnectHeaders = { ...request.headers };
    const { port } = new URL(`https://${request.url}`);
    const net = require("net");
    const serverSocket = net.connect(port, "127.0.0.1", () => {
      clientSocket.write(
        "HTTP/1.1 200 Connection Established\r\n" +
          "Proxy-agent: Node.js-Proxy\r\n\r\n"
      );
      serverSocket.write(head);
      serverSocket.pipe(clientSocket);
      clientSocket.pipe(serverSocket);
    });
  });

  const channel = makeChannel(target.port());
  const proxied = channel.QueryInterface(Ci.nsIProxiedChannel);
  const request =
    `GET /raw HTTP/1.1\r\nHost: localhost:${target.port()}\r\n` +
    "Connection: close\r\n\r\n";
  let responseData = "";
  let writeOffset = 0;
  let tunnelTransport;
  let tunnelInput;
  let tunnelOutput;
  const { promise, resolve, reject } = Promise.withResolvers();

  const inputCallback = {
    onInputStreamReady(input) {
      try {
        const binaryInput = Cc[
          "@mozilla.org/binaryinputstream;1"
        ].createInstance(Ci.nsIBinaryInputStream);
        binaryInput.setInputStream(input);
        responseData += binaryInput.readBytes(input.available());
        if (responseData.includes(RESPONSE_BODY)) {
          resolve();
        } else {
          input.asyncWait(inputCallback, 0, 0, Services.tm.mainThread);
        }
      } catch (error) {
        reject(error);
      }
    },
    QueryInterface: ChromeUtils.generateQI(["nsIInputStreamCallback"]),
  };
  const outputCallback = {
    onOutputStreamReady(output) {
      try {
        writeOffset += output.write(
          request.slice(writeOffset),
          request.length - writeOffset
        );
        if (writeOffset < request.length) {
          output.asyncWait(outputCallback, 0, 0, Services.tm.mainThread);
        } else {
          tunnelInput.asyncWait(inputCallback, 0, 0, Services.tm.mainThread);
        }
      } catch (error) {
        reject(error);
      }
    },
    QueryInterface: ChromeUtils.generateQI(["nsIOutputStreamCallback"]),
  };
  openRawConnect(channel, {
    onTransportAvailable(transport, input, output) {
      tunnelTransport = transport;
      tunnelInput = input;
      tunnelOutput = output;
      tunnelOutput.asyncWait(outputCallback, 0, 0, Services.tm.mainThread);
    },
    onUpgradeFailed(errorCode) {
      reject(new Error(`raw CONNECT upgrade failed: ${errorCode}`));
    },
    QueryInterface: ChromeUtils.generateQI(["nsIHttpUpgradeListener"]),
  });

  await promise;
  Assert.equal(proxied.httpProxyConnectResponseCode, 200);
  Assert.equal(
    proxied.getHttpProxyResponseHeader("Proxy-agent"),
    "Node.js-Proxy"
  );
  Assert.ok(responseData.includes(RESPONSE_BODY));
  Assert.equal(writeOffset, request.length);

  const headers = await proxy.execute("global.rawConnectHeaders");
  Assert.equal(await proxy.execute("global.rawConnectMethod"), "CONNECT");
  Assert.equal(
    await proxy.execute("global.rawConnectTarget"),
    `localhost:${target.port()}`
  );
  Assert.ok(!("alpn" in headers), "CONNECT has no synthetic ALPN");
  Assert.ok(!("upgrade" in headers), "CONNECT has no Upgrade header");
  Assert.notEqual(headers.connection.toLowerCase(), "upgrade");

  tunnelInput.close();
  tunnelOutput.close();
  tunnelTransport.close(Cr.NS_BINDING_ABORTED);
  await proxy.stop();
  await target.stop();
});
