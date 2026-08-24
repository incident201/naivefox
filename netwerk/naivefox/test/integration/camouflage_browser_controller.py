#!/usr/bin/env python3

import argparse
import json
import os
import signal
import subprocess
import time


def firefox_preferences(protocol, proxy_port, socks_port):
    direct_h3 = protocol == "h3" and not socks_port
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
        preferences.update(
            {
                "network.http.http3.disable_when_third_party_roots_found": False,
                "network.http.http3.alt-svc-mapping-for-testing": (
                    f"localhost;h3=:{proxy_port}"
                ),
                "network.http.http3.force-use-alt-svc-mapping-for-testing": True,
            }
        )
    if socks_port:
        preferences.update(
            {
                "network.proxy.type": 1,
                "network.proxy.socks": "127.0.0.1",
                "network.proxy.socks_port": socks_port,
                "network.proxy.socks_version": 5,
                "network.proxy.socks_remote_dns": True,
                "network.proxy.no_proxies_on": "",
                "network.proxy.allow_hijacking_localhost": True,
                "network.proxy.failover_direct": False,
            }
        )
    return preferences


class Controller:
    def __init__(self, args):
        self.args = args
        self.driver = None
        self.process = None
        self.process_log = None
        self.stopping = False

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
            self.args.protocol, self.args.proxy_port, self.args.socks_port
        ).items():
            options.set_preference(name, value)
        service = Service(log_output=self.args.webdriver_log)
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

    def browser_alive(self):
        if self.driver is not None:
            try:
                _ = self.driver.current_url
                return True
            except Exception:
                return False
        return self.process is not None and self.process.poll() is None

    def wait_for_completion(self):
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline and not self.stopping:
            if os.path.isfile(self.args.completion_file):
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
            try:
                self.driver.quit()
            except Exception:
                pass
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process_log is not None:
            self.process_log.close()

    def run(self):
        backend = self.start()
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
        with open(self.args.done_file, "w", encoding="utf-8") as stream:
            stream.write("complete\n")
        self.wait_for_file(self.args.stop_file)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--backend", choices=("auto", "selenium", "commandline"), default="auto"
    )
    parser.add_argument("--protocol", choices=("h2", "h3"), required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
    parser.add_argument("--socks-port", type=int, default=0)
    parser.add_argument("--url", required=True)
    parser.add_argument("--completion-file", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--navigate-file", required=True)
    parser.add_argument("--done-file", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--browser-log", required=True)
    parser.add_argument("--webdriver-log", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    args = parser.parse_args()
    controller = Controller(args)
    signal.signal(signal.SIGTERM, controller.stop)
    signal.signal(signal.SIGINT, controller.stop)
    try:
        controller.run()
    finally:
        controller.close()


if __name__ == "__main__":
    main()
