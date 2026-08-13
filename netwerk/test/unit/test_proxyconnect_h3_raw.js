/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

/* import-globals-from http3_proxy_common.js */

const REQUEST_PADDING = "0123456789abcdef";
const RESPONSE_PADDING = "fedcba9876543210";
const RESPONSE_MARKER = "raw-h3-tunnel-ok";
const RESPONSE_SIZE = 512 * 1024;

add_setup(async function () {
  await setup_http3_proxy();
});

add_task(async function test_empty_protocol_raw_h3_connect() {
  let target = new NodeHTTPServer();
  await target.start();
  await target.registerPathHandler("/raw-h3", (_request, response) => {
    response.writeHead(200, {
      "Content-Type": "text/plain",
      "Content-Length": 512 * 1024,
    });
    const marker = "raw-h3-tunnel-ok";
    response.end(marker + "x".repeat(512 * 1024 - marker.length));
  });
  registerCleanupFunction(async () => target.stop());

  let channel = makeChan(`http://localhost:${target.port()}/`);
  let internal = channel.QueryInterface(Ci.nsIHttpChannelInternal);
  internal.setConnectOnly(false);
  internal.setProxyConnectHeader("padding", REQUEST_PADDING);

  const request =
    `GET /raw-h3 HTTP/1.1\r\nHost: localhost:${target.port()}\r\n` +
    "Connection: close\r\n\r\n";
  let response = "";
  let writeOffset = 0;
  let tunnelTransport;
  let tunnelInput;
  let tunnelOutput;
  const { promise, resolve, reject } = Promise.withResolvers();

  const slowInputCallback = {
    onInputStreamReady(input) {
      try {
        const available = input.available();
        if (available) {
          response += NetUtil.readInputStreamToString(
            input,
            Math.min(available, 4096)
          );
        }
        const headerEnd = response.indexOf("\r\n\r\n");
        if (
          headerEnd >= 0 &&
          response.length - headerEnd - 4 === RESPONSE_SIZE
        ) {
          resolve();
        } else {
          input.asyncWait(slowInputCallback, 0, 0, Services.tm.mainThread);
        }
      } catch (error) {
        reject(error);
      }
    },
    QueryInterface: ChromeUtils.generateQI(["nsIInputStreamCallback"]),
  };

  const outputCallback = {
    onOutputStreamReady(output) {
      info("raw H3 output stream ready");
      try {
        writeOffset += output.write(
          request.slice(writeOffset),
          request.length - writeOffset
        );
        if (writeOffset < request.length) {
          output.asyncWait(outputCallback, 0, 0, Services.tm.mainThread);
        } else {
          output.closeWithStatus(Cr.NS_OK);
          // Consume only one small chunk per callback. Neqo reaches flow
          // control and FIN while this listener drains the tunnel slowly.
          tunnelInput.asyncWait(
            slowInputCallback,
            0,
            0,
            Services.tm.mainThread
          );
        }
      } catch (error) {
        reject(error);
      }
    },
    QueryInterface: ChromeUtils.generateQI(["nsIOutputStreamCallback"]),
  };

  internal.HTTPUpgrade("", {
    onTransportAvailable(transport, input, output) {
      info("raw H3 transport available");
      tunnelTransport = transport;
      tunnelInput = input;
      tunnelOutput = output;
      output.asyncWait(outputCallback, 0, 0, Services.tm.mainThread);
    },
    onUpgradeFailed(errorCode) {
      info(`raw H3 upgrade failed: ${errorCode}`);
      reject(new Error(`raw H3 CONNECT upgrade failed: ${errorCode}`));
    },
    QueryInterface: ChromeUtils.generateQI(["nsIHttpUpgradeListener"]),
  });

  channel.asyncOpen({
    onStartRequest(requestChannel) {
      try {
        Assert.equal(requestChannel.protocolVersion, "h3");
        Assert.equal(requestChannel.responseStatus, 200);
        let proxied = requestChannel.QueryInterface(Ci.nsIProxiedChannel);
        Assert.equal(proxied.httpProxyConnectResponseCode, 200);
        Assert.equal(
          proxied.getHttpProxyResponseHeader("padding"),
          RESPONSE_PADDING
        );
      } catch (error) {
        reject(error);
      }
    },
    onDataAvailable() {},
    onStopRequest(_request, status) {
      if (status !== Cr.NS_OK && status !== Cr.NS_BINDING_ABORTED) {
        reject(new Error(`raw H3 channel failed: ${status}`));
      }
    },
    QueryInterface: ChromeUtils.generateQI(["nsIStreamListener"]),
  });

  await promise;
  const headerEnd = response.indexOf("\r\n\r\n");
  Assert.greaterOrEqual(headerEnd, 0);
  const body = response.slice(headerEnd + 4);
  Assert.equal(body.length, RESPONSE_SIZE);
  Assert.ok(body.startsWith(RESPONSE_MARKER));
  Assert.ok(/^x+$/.test(body.slice(RESPONSE_MARKER.length)));
  Assert.equal(writeOffset, request.length);

  tunnelInput.close();
  tunnelOutput.close();
  tunnelTransport.close(Cr.NS_BINDING_ABORTED);
});
