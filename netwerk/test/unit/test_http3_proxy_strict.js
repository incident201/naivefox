/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

/* import-globals-from http3_proxy_common.js */

add_setup(async function () {
  Services.prefs.setBoolPref("network.http.happy-eyeballs.enabled", true);
  await setup_http3_proxy();
});

add_task(async function test_strict_h3_proxy_does_not_fallback_to_h2() {
  pps.unregisterFilter(proxyFilter);

  let failedH3Port = Number(Services.env.get("MOZHTTP3_PORT_FAILED"));
  Assert.greater(failedH3Port, 0, "the deterministic failing H3 port exists");
  let h2Proxy = new NodeHTTP2ProxyServer();
  await h2Proxy.startWithoutProxyFilter(failedH3Port);
  let target = new NodeHTTPServer();
  await target.start();
  await target.registerPathHandler("/strict", (req, resp) => {
    resp.writeHead(200);
    resp.end("unexpected h2 fallback");
  });

  let strictFilter = new Http3ProxyFilter(
    proxyHost,
    h2Proxy.port(),
    Ci.nsIProxyInfo.DISABLE_HTTP3_PROXY_FALLBACK,
    "/.well-known/masque/udp/{target_host}/{target_port}/",
    proxyAuth
  );
  pps.registerFilter(strictFilter, 10);

  registerCleanupFunction(async () => {
    pps.unregisterFilter(strictFilter);
    await h2Proxy.stop();
    await target.stop();
  });

  let chan = makeChan(
    `${target.protocol()}://alt1.example.com:${target.port()}/strict`
  );
  let [req, body] = await channelOpenPromise(chan, CL_EXPECT_FAILURE);
  Assert.notEqual(req.status, Cr.NS_OK, "the unavailable H3 proxy must fail");
  Assert.notEqual(body, "unexpected h2 fallback", "H2 was not used");
});
