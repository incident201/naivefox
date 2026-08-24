#!/usr/bin/env python3

import argparse
import base64
import json
import os
import signal
import time


class Controller:
    def __init__(self, args):
        self.args = args
        self.driver = None
        self.filter_registered = False
        self.stopping = False

    def stop(self, *_args):
        self.stopping = True

    def start(self):
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service

        user = os.environ.get("NAIVEFOX_FIXTURE_USER", "")
        password = os.environ.get("NAIVEFOX_FIXTURE_PASS", "")
        if not user or not password:
            raise RuntimeError("private proxy credentials are unavailable")
        authorization = "Basic " + base64.b64encode(
            f"{user}:{password}".encode()
        ).decode("ascii")

        options = Options()
        options.binary_location = self.args.binary
        options.profile = self.args.profile
        options.accept_insecure_certs = False
        options.add_argument("-headless")
        options.add_argument("--width=1280")
        options.add_argument("--height=720")
        options.set_preference("network.http.http3.enable", False)
        options.set_preference("network.proxy.allow_hijacking_localhost", True)
        service = Service(
            log_output=self.args.webdriver_log,
            service_args=["--allow-system-access"],
        )
        self.driver = webdriver.Firefox(options=options, service=service)
        self.driver.set_page_load_timeout(self.args.timeout)
        self.driver.set_context("chrome")
        registered = self.driver.execute_script(
            """
const proxyHost = arguments[0];
const proxyPort = arguments[1];
const targetHost = arguments[2];
const targetPort = arguments[3];
const authorization = arguments[4];
const pps = Cc["@mozilla.org/network/protocol-proxy-service;1"]
  .getService(Ci.nsIProtocolProxyService);
const filter = {
  QueryInterface: ChromeUtils.generateQI(["nsIProtocolProxyChannelFilter"]),
  applyFilter(channel, defaultProxyInfo, callback) {
    const uri = channel.URI;
    if (uri.scheme === "https" && uri.asciiHost === targetHost &&
        uri.port === targetPort) {
      const flags = Ci.nsIProxyInfo.TRANSPARENT_PROXY_RESOLVES_HOST |
        Ci.nsIProxyInfo.ALWAYS_TUNNEL_VIA_PROXY;
      callback.onProxyFilterResult(pps.newProxyInfo(
        "https", proxyHost, proxyPort, authorization,
        "naivefox-raw-tunnel", flags, 0xffffffff, null));
      return;
    }
    callback.onProxyFilterResult(defaultProxyInfo);
  },
};
pps.registerChannelFilter(filter, 0);
window.__naivefoxH2DiagnosticProxy = {pps, filter};
return true;
""",
            self.args.proxy_host,
            self.args.proxy_port,
            self.args.target_host,
            self.args.target_port,
            authorization,
        )
        if registered is not True:
            raise RuntimeError("privileged proxy filter registration failed")
        self.filter_registered = True
        self.driver.set_context("content")

    def wait_for_file(self, path):
        while not self.stopping:
            if os.path.isfile(path):
                return
            time.sleep(0.05)
        raise RuntimeError("controller stopped before marker")

    def wait_for_completion(self):
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline and not self.stopping:
            if os.path.isfile(self.args.completion_file):
                return
            time.sleep(0.05)
        raise RuntimeError("timed out waiting for proxied Firefox workload")

    def close(self):
        if self.driver is None:
            return
        if self.filter_registered:
            try:
                self.driver.set_context("chrome")
                self.driver.execute_script(
                    """
const state = window.__naivefoxH2DiagnosticProxy;
if (state) {
  state.pps.unregisterChannelFilter(state.filter);
  delete window.__naivefoxH2DiagnosticProxy;
}
"""
                )
            except Exception:
                pass
        try:
            self.driver.quit()
        except Exception:
            pass
        self.driver = None

    def run(self):
        self.start()
        temporary = self.args.ready_file + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(
                {"backend": "selenium", "proxy_filter": "registered"},
                stream,
                sort_keys=True,
            )
            stream.write("\n")
        os.replace(temporary, self.args.ready_file)
        self.wait_for_file(self.args.navigate_file)
        self.driver.get(self.args.url)
        self.wait_for_completion()
        with open(self.args.done_file, "w", encoding="utf-8") as stream:
            stream.write("complete\n")
        self.wait_for_file(self.args.stop_file)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--proxy-host", required=True)
    parser.add_argument("--proxy-port", required=True, type=int)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", required=True, type=int)
    parser.add_argument("--url", required=True)
    parser.add_argument("--completion-file", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--navigate-file", required=True)
    parser.add_argument("--done-file", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--webdriver-log", required=True)
    parser.add_argument("--timeout", required=True, type=int)
    args = parser.parse_args()
    for name, port in (("proxy", args.proxy_port), ("target", args.target_port)):
        if not 1 <= port <= 65535:
            parser.error(f"{name} port is outside 1..65535")
    controller = Controller(args)
    signal.signal(signal.SIGTERM, controller.stop)
    signal.signal(signal.SIGINT, controller.stop)
    try:
        controller.run()
    finally:
        controller.close()


if __name__ == "__main__":
    main()
