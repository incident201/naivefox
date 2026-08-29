#!/usr/bin/env python3

import argparse
import base64
import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse

DEAD_LOCAL_PROXY_PORT = 9


def proxy_pac_script(
    socks_port,
    target_port,
    dead_proxy_port=DEAD_LOCAL_PROXY_PORT,
):
    for name, port in (
        ("SOCKS", socks_port),
        ("target", target_port),
        ("dead proxy", dead_proxy_port),
    ):
        if not 1 <= port <= 65535:
            raise ValueError(f"{name} port is outside 1..65535")
    return f"""function FindProxyForURL(url, host) {{
  host = host.toLowerCase();
  var authority = url.toLowerCase().split("/")[2];
  if (host === "localhost" || host === "127.0.0.1" ||
      host === "::1" || host === "[::1]") {{
    if (authority === "localhost:{target_port}" ||
        authority === "127.0.0.1:{target_port}" ||
        authority === "[::1]:{target_port}") {{
      return "SOCKS5 127.0.0.1:{socks_port}";
    }}
    return "DIRECT";
  }}
  return "PROXY 127.0.0.1:{dead_proxy_port}";
}}
"""


def proxy_pac_url(
    socks_port,
    target_port,
    dead_proxy_port=DEAD_LOCAL_PROXY_PORT,
):
    encoded = base64.b64encode(
        proxy_pac_script(socks_port, target_port, dead_proxy_port).encode("utf-8")
    ).decode("ascii")
    return f"data:application/x-ns-proxy-autoconfig;base64,{encoded}"


def http_proxy_pac_script(
    http_proxy_port,
    target_port,
    dead_proxy_port=DEAD_LOCAL_PROXY_PORT,
):
    for name, port in (
        ("HTTP proxy", http_proxy_port),
        ("target", target_port),
        ("dead proxy", dead_proxy_port),
    ):
        if not 1 <= port <= 65535:
            raise ValueError(f"{name} port is outside 1..65535")
    return f"""function FindProxyForURL(url, host) {{
  host = host.toLowerCase();
  var authority = url.toLowerCase().split("/")[2];
  if (host === "localhost" || host === "127.0.0.1" ||
      host === "::1" || host === "[::1]") {{
    if (authority === "localhost:{target_port}" ||
        authority === "127.0.0.1:{target_port}" ||
        authority === "[::1]:{target_port}") {{
      return "PROXY 127.0.0.1:{http_proxy_port}";
    }}
    return "DIRECT";
  }}
  return "PROXY 127.0.0.1:{dead_proxy_port}";
}}
"""


def http_proxy_pac_url(
    http_proxy_port,
    target_port,
    dead_proxy_port=DEAD_LOCAL_PROXY_PORT,
):
    encoded = base64.b64encode(
        http_proxy_pac_script(
            http_proxy_port,
            target_port,
            dead_proxy_port,
        ).encode("utf-8")
    ).decode("ascii")
    return f"data:application/x-ns-proxy-autoconfig;base64,{encoded}"


def proxy_preferences(socks_port, target_port):
    return {
        "network.proxy.type": 2,
        "network.proxy.autoconfig_url": proxy_pac_url(socks_port, target_port),
        "network.proxy.autoconfig_url.include_path": False,
        "network.proxy.no_proxies_on": "",
        "network.proxy.allow_hijacking_localhost": True,
        "network.proxy.failover_direct": False,
    }


def http_proxy_preferences(http_proxy_port, target_port):
    return {
        "network.proxy.type": 2,
        "network.proxy.autoconfig_url": http_proxy_pac_url(
            http_proxy_port,
            target_port,
        ),
        "network.proxy.autoconfig_url.include_path": False,
        "network.proxy.no_proxies_on": "",
        "network.proxy.allow_hijacking_localhost": True,
        "network.proxy.failover_direct": False,
    }


def proxy_user_js(socks_port, target_port):
    return "".join(
        f"user_pref({json.dumps(name)}, {json.dumps(value)});\n"
        for name, value in proxy_preferences(socks_port, target_port).items()
    )


def http_proxy_user_js(http_proxy_port, target_port):
    return "".join(
        f"user_pref({json.dumps(name)}, {json.dumps(value)});\n"
        for name, value in http_proxy_preferences(
            http_proxy_port,
            target_port,
        ).items()
    )


def firefox_preferences(
    protocol,
    proxy_port,
    socks_port,
    http_proxy_port=0,
    target_port=0,
):
    local_proxy_ports = [socks_port, http_proxy_port]
    if sum(bool(port) for port in local_proxy_ports) > 1:
        raise ValueError("local proxy ports are mutually exclusive")
    if any(local_proxy_ports) and not 1 <= target_port <= 65535:
        raise ValueError("target port is outside 1..65535")
    direct_h3 = protocol == "h3" and not any(local_proxy_ports)
    preferences = {
        "app.update.enabled": False,
        "browser.shell.checkDefaultBrowser": False,
        "network.captive-portal-service.enabled": False,
        "network.connectivity-service.enabled": False,
        "network.dns.disableIPv6": True,
        "network.prefetch-next": False,
        "network.http.speculative-parallel-limit": 0,
        "network.http.http3.enable": direct_h3,
    }
    if direct_h3:
        preferences.update({
            "network.http.http3.disable_when_third_party_roots_found": False,
            "network.http.http3.alt-svc-mapping-for-testing": (
                f"localhost;h3=:{proxy_port}"
            ),
            "network.http.http3.force-use-alt-svc-mapping-for-testing": True,
        })
    if socks_port:
        preferences.update(proxy_preferences(socks_port, target_port))
    if http_proxy_port:
        preferences.update(http_proxy_preferences(http_proxy_port, target_port))
    return preferences


class Controller:
    def __init__(self, args):
        self.args = args
        self.driver = None
        self.process = None
        self.process_log = None
        self.stopping = False
        self.forced_kill = False
        self.shutdown_method = "not_started"
        self.shutdown_failed = False

    def stop(self, *_args):
        self.stopping = True

    def start_selenium(self):
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service

        options = Options()
        options.binary_location = self.args.binary
        options.profile = self.args.profile
        options.accept_insecure_certs = False
        options.add_argument("-headless")
        options.add_argument("--width=1280")
        options.add_argument("--height=720")
        for name, value in firefox_preferences(
            self.args.protocol,
            self.args.proxy_port,
            self.args.socks_port,
            self.args.http_proxy_port,
            urllib.parse.urlsplit(self.args.url).port or 0,
        ).items():
            options.set_preference(name, value)
        service_args = (
            ["--allow-system-access"] if self.args.navigation_evidence_file else None
        )
        service = Service(log_output=self.args.webdriver_log, service_args=service_args)
        self.driver = webdriver.Firefox(options=options, service=service)
        self.driver.set_page_load_timeout(self.args.timeout)
        return "selenium"

    def start(self):
        if self.args.backend == "commandline":
            return "commandline"
        try:
            return self.start_selenium()
        except ImportError:
            if self.args.backend == "selenium":
                raise
            return "commandline"

    def navigate(self, backend):
        if backend == "selenium":
            self.driver.get(self.args.url)
            return
        self.process_log = open(self.args.browser_log, "ab", buffering=0)
        self.process = subprocess.Popen(
            [
                self.args.binary,
                "--headless",
                "--new-instance",
                "--no-remote",
                "--profile",
                self.args.profile,
                "--window-size",
                "1280,720",
                self.args.url,
            ],
            stdout=self.process_log,
            stderr=subprocess.STDOUT,
        )

    def selenium_identity(self):
        self.driver.set_context("chrome")
        try:
            identity = self.driver.execute_script(
                """return {
                  browser_pid: Services.appinfo.processID,
                  browsing_context_id:
                    gBrowser.selectedBrowser.browsingContext.id,
                  content_pid:
                    gBrowser.selectedBrowser.browsingContext.currentWindowGlobal.osPid
                };"""
            )
        finally:
            self.driver.set_context("content")
        identity["webdriver_session_id"] = self.driver.session_id
        identity["current_window_handle"] = self.driver.current_window_handle
        identity["window_handles"] = self.driver.window_handles
        return identity

    def write_navigation_evidence(self, identities):
        temporary = self.args.navigation_evidence_file + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    f"navigation_{index}": identity
                    for index, identity in enumerate(identities, start=1)
                },
                stream,
            )
            stream.write("\n")
        os.replace(temporary, self.args.navigation_evidence_file)

    def browser_alive(self):
        if self.driver is not None:
            try:
                _ = self.driver.current_url
                return True
            except Exception:
                return False
        return self.process is not None and self.process.poll() is None

    def wait_for_completion(self, completion_file=None):
        completion_file = completion_file or self.args.completion_file
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline and not self.stopping:
            if os.path.isfile(completion_file):
                return
            if not self.browser_alive():
                raise RuntimeError("Firefox exited before workload completion")
            time.sleep(0.05)
        raise RuntimeError("timed out waiting for browser workload completion")

    def wait_for_file(self, path):
        while not os.path.exists(path) and not self.stopping:
            time.sleep(0.05)

    def close(self):
        if self.driver is not None:
            self.shutdown_method = "webdriver_quit"
            try:
                self.driver.quit()
            except Exception:
                self.shutdown_failed = True
        if self.process is not None:
            self.shutdown_method = "controlled_sigterm"
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.forced_kill = True
                    self.process.kill()
                    self.process.wait()
        if self.process_log is not None:
            self.process_log.close()

    def run(self):
        backend = self.start()
        if self.args.warmup_url:
            if backend != "selenium":
                raise RuntimeError("browser warmup requires Selenium")
            self.driver.get(self.args.warmup_url)
            self.wait_for_completion(self.args.warmup_completion_file)
            self.driver.get("about:blank")
            if self.driver.current_url != "about:blank":
                raise RuntimeError("browser warmup did not drain to about:blank")
        temporary = self.args.ready_file + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump({"backend": backend}, stream, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, self.args.ready_file)
        self.wait_for_file(self.args.navigate_file)
        if self.stopping:
            return
        self.navigate(backend)
        self.wait_for_completion()
        repeat_navigations = []
        if self.args.second_url:
            repeat_navigations.append(
                (self.args.second_url, self.args.second_completion_file)
            )
        repeat_navigations.extend(self.args.additional_navigation)
        if repeat_navigations:
            if backend != "selenium":
                raise RuntimeError("repeat navigation requires Selenium")
            identities = [self.selenium_identity()]
            for url, completion_file in repeat_navigations:
                self.driver.get(url)
                self.wait_for_completion(completion_file)
                identities.append(self.selenium_identity())
            self.write_navigation_evidence(identities)
        with open(self.args.done_file, "w", encoding="utf-8") as stream:
            stream.write("complete\n")
        self.wait_for_file(self.args.stop_file)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--generate-pac-user-js":
        try:
            print(proxy_user_js(int(sys.argv[2]), int(sys.argv[3])), end="")
        except (ValueError, TypeError) as error:
            raise SystemExit(f"invalid PAC port: {error}") from error
        return
    if len(sys.argv) == 4 and sys.argv[1] == "--generate-http-pac-user-js":
        try:
            print(http_proxy_user_js(int(sys.argv[2]), int(sys.argv[3])), end="")
        except (ValueError, TypeError) as error:
            raise SystemExit(f"invalid PAC port: {error}") from error
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--backend", choices=("auto", "selenium", "commandline"), default="auto"
    )
    parser.add_argument("--protocol", choices=("h2", "h3"), required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument("--socks-port", type=int, default=0)
    parser.add_argument("--http-proxy-port", type=int, default=0)
    parser.add_argument("--url", required=True)
    parser.add_argument("--completion-file", required=True)
    parser.add_argument("--second-url")
    parser.add_argument("--second-completion-file")
    parser.add_argument(
        "--additional-navigation",
        nargs=2,
        action="append",
        metavar=("URL", "COMPLETION_FILE"),
        default=[],
    )
    parser.add_argument("--navigation-evidence-file")
    parser.add_argument("--warmup-url")
    parser.add_argument("--warmup-completion-file")
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--navigate-file", required=True)
    parser.add_argument("--done-file", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--browser-log", required=True)
    parser.add_argument("--webdriver-log", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--shutdown-file")
    args = parser.parse_args()
    local_proxy_ports = (
        args.socks_port,
        args.http_proxy_port,
    )
    if sum(bool(port) for port in local_proxy_ports) > 1:
        parser.error("local proxy port options are mutually exclusive")
    if bool(args.warmup_url) != bool(args.warmup_completion_file):
        parser.error("--warmup-url and --warmup-completion-file must be used together")
    if bool(args.second_url) != bool(args.second_completion_file):
        parser.error(
            "--second-url and --second-completion-file must be used together"
        )
    has_repeat_navigation = bool(args.second_url or args.additional_navigation)
    if has_repeat_navigation != bool(args.navigation_evidence_file):
        parser.error(
            "repeat navigation and --navigation-evidence-file must be used together"
        )
    controller = Controller(args)
    signal.signal(signal.SIGTERM, controller.stop)
    signal.signal(signal.SIGINT, controller.stop)
    error = None
    try:
        controller.run()
    except Exception as caught:
        error = caught
    finally:
        controller.close()
        if args.shutdown_file:
            temporary = args.shutdown_file + ".tmp"
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "browser_process_exited": (
                            controller.process is None
                            or controller.process.poll() is not None
                        ),
                        "forced_kill": controller.forced_kill,
                        "shutdown_failed": controller.shutdown_failed,
                        "process_returncode": (
                            None
                            if controller.process is None
                            else controller.process.returncode
                        ),
                        "shutdown_method": controller.shutdown_method,
                    },
                    stream,
                    sort_keys=True,
                )
                stream.write("\n")
            os.replace(temporary, args.shutdown_file)
    if error is not None:
        raise error


if __name__ == "__main__":
    main()
