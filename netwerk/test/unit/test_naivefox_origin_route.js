/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const { NodeHTTP2ProxyServer } = ChromeUtils.importESModule(
  "resource://testing-common/NodeServer.sys.mjs"
);

add_setup(async function () {
  do_get_profile();
  Services.prefs.setBoolPref("network.proxy.allow_hijacking_localhost", true);
  registerCleanupFunction(() => {
    Services.prefs.clearUserPref("network.proxy.allow_hijacking_localhost");
  });
});

add_task(async function test_ordinary_origin_requests_on_explicit_h2_route() {
  const proxy = new NodeHTTP2ProxyServer();
  await proxy.start();
  registerCleanupFunction(async () => proxy.stop());
  await proxy.execute([
    "global.originRequests = [];",
    "global.proxySessionCount = 0;",
    'global.proxy.on("session", () => global.proxySessionCount++);',
    'global.proxy.removeAllListeners("stream");',
    'global.proxy.on("stream", (stream, headers) => {',
    '  let body = "";',
    '  stream.on("data", chunk => { body += chunk.toString(); });',
    '  stream.on("end", () => {',
    "    global.originRequests.push({ headers: { ...headers }, body });",
    '    stream.respond({ ":status": 200, "content-type": "text/plain" });',
    '    stream.end("origin-ok");',
    "  });",
    "});",
  ].join("\n"));

  for (const method of ["GET", "POST"]) {
    const channel = NetUtil.newChannel({
      uri: proxy.origin() + "/",
      loadUsingSystemPrincipal: true,
      securityFlags:
        Ci.nsILoadInfo.SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL |
        Ci.nsILoadInfo.SEC_COOKIES_OMIT,
    }).QueryInterface(Ci.nsIHttpChannel);
    const internal = channel.QueryInterface(Ci.nsIHttpChannelInternal);
    internal.setNaiveFoxOriginRoute();
    if (method === "POST") {
      const input = Cc["@mozilla.org/io/string-input-stream;1"].createInstance(
        Ci.nsIStringInputStream
      );
      input.setByteStringData("origin-upload");
      channel.QueryInterface(Ci.nsIUploadChannel2).explicitSetUploadStream(
        input, "text/plain", 13, "POST", false
      );
    }
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
        onStopRequest(_request, status) {
          if (Components.isSuccessCode(status)) {
            resolve();
          } else {
            reject(new Error("origin request failed: " + status));
          }
        },
        QueryInterface: ChromeUtils.generateQI(["nsIStreamListener"]),
      });
    });
    Assert.equal(body, "origin-ok");
    Assert.throws(
      () => internal.setNaiveFoxOriginRoute(),
      /NS_ERROR_IN_PROGRESS/,
      "origin routing is pre-open only"
    );
  }
  Assert.equal(await proxy.execute("global.proxySessionCount"), 1);
  const requests = await proxy.execute("global.originRequests");
  Assert.equal(requests.length, 2);
  for (const [index, request] of requests.entries()) {
    Assert.equal(request.headers[":method"], index ? "POST" : "GET");
    Assert.equal(request.headers[":scheme"], "https");
    Assert.equal(request.headers[":authority"], "localhost:" + proxy.port());
    Assert.equal(request.headers[":path"], "/");
    Assert.equal(request.body, index ? "origin-upload" : "");
    Assert.ok(!request.headers["proxy-authorization"]);
  }
});
