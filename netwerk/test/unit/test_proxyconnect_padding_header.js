/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const { NodeHTTPServer, NodeHTTPSProxyServer, NodeHTTP2ProxyServer } =
  ChromeUtils.importESModule("resource://testing-common/NodeServer.sys.mjs");
const { TestUtils } = ChromeUtils.importESModule(
  "resource://testing-common/TestUtils.sys.mjs"
);

const REQUEST_PADDING = "0123456789abcdef";
const RESPONSE_PADDING = "fedcba9876543210";

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

function startConnect(channel, upgradeListener) {
  const internal = channel.QueryInterface(Ci.nsIHttpChannelInternal);
  internal.setConnectOnly(false);
  internal.setProxyConnectHeader("padding", REQUEST_PADDING);
  internal.HTTPUpgrade("", upgradeListener);
  channel.asyncOpen({
    onStartRequest() {},
    onDataAvailable() {},
    onStopRequest() {},
    QueryInterface: ChromeUtils.generateQI(["nsIStreamListener"]),
  });
}

function openConnect(channel) {
  const { promise, resolve, reject } = Promise.withResolvers();
  startConnect(channel, {
    onTransportAvailable(transport, input, output) {
      resolve({ transport, input, output });
    },
    onUpgradeFailed(errorCode) {
      reject(new Error(`proxy CONNECT failed: ${errorCode}`));
    },
    QueryInterface: ChromeUtils.generateQI(["nsIHttpUpgradeListener"]),
  });
  return promise;
}

add_setup(function () {
  do_get_profile();
  Services.prefs.setBoolPref("network.proxy.allow_hijacking_localhost", true);
  registerCleanupFunction(() => {
    Services.prefs.clearUserPref("network.proxy.allow_hijacking_localhost");
  });
});

add_task(function test_proxy_connect_header_validation() {
  const internal = makeChannel(80).QueryInterface(Ci.nsIHttpChannelInternal);

  for (const [name, value] of [
    ["padding\r\ninjected", "value"],
    ["padding", "value\r\ninjected"],
    ["Host", "example.com"],
    ["Connection", "close"],
    ["TE", "trailers"],
    ["Trailer", "checksum"],
    ["Proxy-Authorization", "Basic secret"],
    ["Proxy-Authenticate", "Basic"],
    ["ALPN", "custom"],
  ]) {
    Assert.throws(
      () => internal.setProxyConnectHeader(name, value),
      /NS_ERROR_ILLEGAL_VALUE/,
      `${name} is not accepted as a proxy CONNECT extra header`
    );
  }
});

add_task(async function test_padding_header_on_h1_connect() {
  const target = new NodeHTTPServer();
  await target.start();
  const proxy = new NodeHTTPSProxyServer();
  await proxy.start();
  await proxy.registerConnectHandler(function (request, clientSocket, head) {
    global.paddingConnectHeaders = { ...request.headers };
    const { port } = new URL(`https://${request.url}`);
    const net = require("net");
    const serverSocket = net.connect(port, "127.0.0.1", () => {
      clientSocket.write(
        "HTTP/1.1 200 Connection Established\r\n" +
          "padding: fedcba9876543210\r\n\r\n"
      );
      serverSocket.write(head);
      serverSocket.pipe(clientSocket);
      clientSocket.pipe(serverSocket);
    });
  });

  const channel = makeChannel(target.port());
  const tunnel = await openConnect(channel);
  const headers = await proxy.execute("global.paddingConnectHeaders");
  Assert.equal(headers.padding, REQUEST_PADDING);
  Assert.equal(
    channel
      .QueryInterface(Ci.nsIProxiedChannel)
      .getHttpProxyResponseHeader("padding"),
    RESPONSE_PADDING
  );

  tunnel.input.close();
  tunnel.output.close();
  tunnel.transport.close(Cr.NS_BINDING_ABORTED);
  await proxy.stop();
  await target.stop();
});

add_task(async function test_padding_header_on_h2_connect() {
  const target = new NodeHTTPServer();
  await target.start();
  const proxy = new NodeHTTP2ProxyServer();
  await proxy.start();
  await proxy.execute(`
    global.paddingConnectHeaders = null;
    global.paddingConnectAlpn = null;
    global.proxy.prependListener("stream", (stream, headers) => {
      if (headers[":method"] !== "CONNECT") {
        return;
      }
      global.paddingConnectHeaders = { ...headers };
      global.paddingConnectAlpn = stream.session.socket.alpnProtocol;
      const respond = stream.respond;
      stream.respond = function(responseHeaders, options) {
        if (responseHeaders[":status"] === 200) {
          responseHeaders = {
            ...responseHeaders,
            padding: "${RESPONSE_PADDING}",
          };
        }
        return respond.call(this, responseHeaders, options);
      };
    });
  `);

  const channel = makeChannel(target.port());
  startConnect(channel, {
    onTransportAvailable() {},
    onUpgradeFailed() {},
    QueryInterface: ChromeUtils.generateQI(["nsIHttpUpgradeListener"]),
  });
  await TestUtils.waitForCondition(async () =>
    proxy.execute("global.paddingConnectHeaders !== null")
  );
  const headers = await proxy.execute("global.paddingConnectHeaders");
  Assert.equal(await proxy.execute("global.paddingConnectAlpn"), "h2");
  Assert.equal(headers[":method"], "CONNECT");
  Assert.equal(headers.padding, REQUEST_PADDING);
  await TestUtils.waitForCondition(() => {
    try {
      return (
        channel
          .QueryInterface(Ci.nsIProxiedChannel)
          .getHttpProxyResponseHeader("padding") === RESPONSE_PADDING
      );
    } catch (error) {
      return false;
    }
  });
  Assert.equal(
    channel
      .QueryInterface(Ci.nsIProxiedChannel)
      .getHttpProxyResponseHeader("padding"),
    RESPONSE_PADDING
  );

  channel.cancel(Cr.NS_BINDING_ABORTED);
  await proxy.stop();
  await target.stop();
});
