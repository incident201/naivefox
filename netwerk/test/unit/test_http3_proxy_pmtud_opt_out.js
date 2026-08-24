/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

/* import-globals-from http3_proxy_common.js */

add_setup(async function () {
  await setup_http3_proxy();
});

add_task(async function test_flagged_h3_proxy_route_remains_functional() {
  pps.unregisterFilter(proxyFilter);
  const flags =
    Ci.nsIProxyInfo.DISABLE_HTTP3_PROXY_FALLBACK |
    Ci.nsIProxyInfo.DO_NOT_FORCE_HTTP3_PROXY_PMTUD;
  const flaggedFilter = new Http3ProxyFilter(
    proxyHost,
    proxyPort,
    flags,
    "/.well-known/masque/udp/{target_host}/{target_port}/",
    proxyAuth
  );
  pps.registerFilter(flaggedFilter, 10);
  registerCleanupFunction(() => pps.unregisterFilter(flaggedFilter));

  const target = new NodeHTTPServer();
  await target.start();
  await target.registerPathHandler("/pmtud-opt-out", (_req, resp) => {
    resp.writeHead(200);
    resp.end("flagged-h3-route-ok");
  });
  registerCleanupFunction(async () => {
    await target.stop();
  });

  const channel = makeChan(
    `${target.protocol()}://alt1.example.com:${target.port()}/pmtud-opt-out`
  );
  const [request, body] = await channelOpenPromise(
    channel,
    CL_IGNORE_CL | CL_ALLOW_UNKNOWN_CL
  );
  Assert.equal(request.status, Cr.NS_OK, "the flagged H3 route succeeds");
  Assert.equal(body, "flagged-h3-route-ok", "the tunnel carries the response");
});
