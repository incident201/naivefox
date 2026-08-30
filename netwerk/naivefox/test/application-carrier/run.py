#!/usr/bin/env python3
"""Experimental full-browser carrier admission; never changes product defaults."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
from types import SimpleNamespace

INTEGRATION = Path(__file__).resolve().parents[1] / "integration"
sys.path.insert(0, str(INTEGRATION))
from camouflage_browser_controller import firefox_preferences
from camouflage_capture_health import validate_dumpcap_log
import camouflage_features as features
import camouflage_superblocks as superblocks

OLD = Path("/home/zubastik/naivefox-refresh-20260830.fJHfmY")
FIREFOX = OLD / "reference/firefox/firefox"
PACKAGE = OLD / "full-linux/package/allocator-fixed"
TRANSPORT = Path("/home/zubastik/naivefox-transport")
PROFILES = {
    "v1": (16, 131072), "duplex-v1": (16, 131072),
    "compact": (16, 65536), "compact-sync": (16, 65536),
    "compact-sync20": (20, 65536), "compact-fast20": (20, 65536),
    "staged": (18, 65536), "staged-fast": (18, 65536),
    "staged-fast20": (20, 65536),
    "staged-stream20": (20, 65536),
}


def profile_budget(name):
    rounds, media = PROFILES[name]
    down = 4 * 24576 + (rounds - 4) * media
    if name.startswith("staged"):
        down = 770048 + (rounds - 18) * 65536
    duplex = name.startswith("duplex") or name.startswith("compact-sync") or name == "compact-fast20"
    return rounds, down, rounds * 4096, 7 + rounds * (1 if duplex else 2)


def validate_http_graph(stats, name, mode):
    if mode == "default":
        return
    rounds, down, up, requests = profile_budget(name)
    if stats["connect"] or stats["rejected"] or sum(stats["requests"].values()) != requests:
        raise RuntimeError("HTTP graph admission")
    if mode == "append":
        if stats["download_bytes"] < down or stats["upload_bytes"] < up or stats["download_filler"] != down - rounds * 16 or stats["upload_filler"] != up - rounds * 16:
            raise RuntimeError("append fixed filler admission")
    elif stats["download_bytes"] != down or stats["upload_bytes"] != up:
        raise RuntimeError("exact HTTP capacity admission")
    if mode == "reference" and (stats["opens"] or stats["download_useful"] or stats["upload_useful"]):
        raise RuntimeError("empty visitor admission")


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise RuntimeError("readiness timeout")


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def process_memory(pid):
    pending=[int(pid)];seen=set();pss=0
    while pending:
        current=pending.pop()
        if current in seen:continue
        seen.add(current)
        try:
            children=Path(f"/proc/{current}/task/{current}/children").read_text()
            pending.extend(int(value) for value in children.split())
            for line in Path(f"/proc/{current}/smaps_rollup").read_text().splitlines():
                if line.startswith("Pss:"):pss+=int(line.split()[1])
        except (FileNotFoundError,ProcessLookupError):pass
    return {"processes":len(seen),"pss_kib":pss}


def profile(path, source, protocol, port, local_kind=None, local_port=0, target_port=0):
    shutil.copytree(source, path)
    prefs = firefox_preferences(protocol, port, local_port if local_kind == "socks" else 0,
                                local_port if local_kind == "http" else 0, target_port)
    prefs.update({"browser.safebrowsing.realTime.enabled": False,
                  "browser.safebrowsing.globalCache.enabled": False,
                  "browser.safebrowsing.provider.google5.enabled": False})
    (path / "user.js").write_text("".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k,v in prefs.items()))
    return path


def browser(path, log):
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    options = Options()
    options.binary_location = str(FIREFOX)
    options.profile = str(path)
    options.accept_insecure_certs = False
    options.add_argument("-headless")
    options.add_argument("--width=1280")
    options.add_argument("--height=720")
    options.page_load_strategy = "none"
    driver = webdriver.Firefox(options=options, service=Service(executable_path="/root/.cache/selenium/geckodriver/linux64/0.37.1/geckodriver", log_output=str(log)))
    driver.set_page_load_timeout(30)
    driver.get("about:blank")
    return driver


class Campaign:
    def __init__(self, root, protocol):
        self.root = root
        self.protocol = protocol
        self.env = dict(os.environ, NAIVEFOX_OBJDIR=str(root / "fixture"))
        self.fixture = None
        self.base_config = None
        self.outer_rate_mbit = 0

    def start(self):
        binaries = {}
        for name in ("caddy", "bridge"):
            with (self.root.parent / "bin" / name).open("rb") as source:
                binaries[name] = hashlib.file_digest(source, "sha256").hexdigest()
        write_json(self.root / "provenance.json", {
            "binary_sha256": binaries,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "server_revision": subprocess.check_output(["git", "-C", str(TRANSPORT), "rev-parse", "HEAD"], text=True).strip(),
            "server_worktree_dirty": bool(subprocess.check_output(["git", "-C", str(TRANSPORT), "status", "--porcelain"], text=True)),
            "firefox_base": "0b76543aaeeeb2a5748ce2675ee36e7c94cb1125",
            "firefox_ci_task": "L5Q0X7WRRqCc5qenw0iRZQ",
        })
        state = self.root / "fixture/naivefox-fixture"
        state.mkdir(parents=True, exist_ok=True)
        if not (state / "tools").exists():
            (state / "tools").symlink_to(OLD / "full-linux/naivefox-fixture/tools", target_is_directory=True)
        args = [str(INTEGRATION / "start.sh"), "--mode", self.protocol, "--inner-h2"]
        if self.protocol == "h2":
            args += ["--outer-h2-only"]
        with (self.root / "fixture-start.log").open("w") as log:
            subprocess.run(args, env=self.env, stdout=log, stderr=subprocess.STDOUT, check=True)
        self.fixture = Path((state / "active-run").read_text().strip())
        self.values = {}
        for line in (self.fixture / "fixture.env").read_text().splitlines():
            name, value = line.split("=", 1)
            self.values[name] = shlex.split(value)[0] if value else ""
        self.port = int(self.values["NAIVEFOX_FIXTURE_PROXY_PORT"])
        self.target_port = int(self.values["NAIVEFOX_FIXTURE_INNER_H2_PORT"])
        pid = int((self.fixture / "caddy.pid").read_text())
        if Path(f"/proc/{pid}/exe").resolve() != (state / "tools/bin/caddy").resolve():
            raise RuntimeError("fixture process identity")
        os.kill(pid, signal.SIGTERM)
        wait_for(lambda: not Path(f"/proc/{pid}").exists() or Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z")
        (self.fixture / "caddy.pid").unlink()
        self.base_config = json.loads((self.fixture / "adapted.json").read_text())

    def close(self):
        with (self.root / "fixture-stop.log").open("w") as log:
            subprocess.run([str(INTEGRATION / "stop.sh"), "--quiet"], env=self.env, stdout=log, stderr=subprocess.STDOUT)

    def shape_outer(self, rate):
        if not os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED"):
            raise RuntimeError("refusing shaping outside isolated namespace")
        commands = [
            ["tc", "qdisc", "add", "dev", "lo", "root", "handle", "1:", "prio", "bands", "3", "priomap", *(["0"] * 16)],
            ["tc", "qdisc", "add", "dev", "lo", "parent", "1:3", "handle", "30:", "netem", "rate", f"{rate}mbit", "limit", "10000"],
        ]
        for priority, direction in enumerate(("sport", "dport"), 10):
            commands.append(["tc", "filter", "add", "dev", "lo", "protocol", "ip", "parent", "1:", "prio", str(priority), "u32", "match", "ip", "protocol", "6" if self.protocol == "h2" else "17", "0xff", "match", "ip", direction, str(self.port), "0xffff", "flowid", "1:3"])
        for command in commands:
            subprocess.run(command, check=True, capture_output=True)
        self.outer_rate_mbit = rate
        write_json(self.root / "outer-shaping.json", {"rate_mbit": rate, "scope": "outer-port-only-both-directions", "qdisc": subprocess.check_output(["tc", "qdisc", "show", "dev", "lo"], text=True)})

    def sample(self, name, kind="socks", mode="replace", rounds=0, capture=False, probe=False, app_profile="v1"):
        rounds = rounds or PROFILES[app_profile][0]
        directory = self.root / name
        directory.mkdir(mode=0o700)
        key, token = secrets.token_hex(32), secrets.token_hex(32)
        config = json.loads(json.dumps(self.base_config))
        handler = {"handler":"naivefox_transport", "key":key,
                   "profile":app_profile,
                   "append_mode":mode=="append",
                   "allowed_targets":[f"localhost:{self.target_port}"],
                   "stats_path":str(directory / "server-stats.json")}
        servers = config["apps"]["http"]["servers"]
        for server in servers.values():
            if f"127.0.0.1:{self.port}" in server["listen"]:
                server["routes"].insert(0, {"handle":[handler]})
        write_json(directory / "caddy.json", config)
        caddy = bridge = naive = cap = monitor = None
        worker = inner = None
        probes = []
        files = []
        stage = tempfile.TemporaryDirectory(prefix="naivefox-carrier-capture-") if capture else None
        def launch(args, logname, env=None):
            log = (directory / logname).open("w"); files.append(log)
            return subprocess.Popen([str(v) for v in args], stdout=log, stderr=subprocess.STDOUT, env=env)
        result = {"sample":name,"protocol":self.protocol,"kind":kind,"mode":mode,"rounds":rounds,"app_profile":app_profile,"outer_rate_mbit":self.outer_rate_mbit}
        try:
            caddy = launch([self.root.parent / "bin/caddy","run","--config",directory / "caddy.json"], "caddy.log",
                           dict(os.environ, XDG_DATA_HOME=str(directory / "xdg-data"), XDG_CONFIG_HOME=str(directory / "xdg-config")))
            wait_for(lambda: caddy.poll() is not None or "server running" in (directory / "caddy.log").read_text())
            if caddy.poll() is not None: raise RuntimeError("caddy startup")
            reference = mode == "reference"
            control = mode == "default"
            if not reference:
                if control:
                    with socket.socket() as listener:
                        listener.bind(("127.0.0.1",0));local_port=listener.getsockname()[1]
                    native_profile = profile(directory / "native-profile", self.fixture / "profiles/trusted", "h2",self.port)
                    if self.protocol=="h3":
                        with (native_profile / "user.js").open("a") as prefs:
                            prefs.write('user_pref("network.http.http3.enable", true);\nuser_pref("network.http.http3.disable_when_third_party_roots_found", false);\n')
                    proxy = ("quic" if self.protocol=="h3" else "https") + "://" + self.values["NAIVEFOX_FIXTURE_USER"] + ":" + self.values["NAIVEFOX_FIXTURE_PASS"] + f"@localhost:{self.port}"
                    write_json(directory / "naive.json",{"listen":f"{kind}://127.0.0.1:{local_port}","proxy":proxy,"host-resolver-rules":"MAP localhost 127.0.0.1","log":""})
                    naive = launch([PACKAGE / "naivefox",directory / "naive.json"],"naive.log",dict(os.environ,NAIVEFOX_PROFILE=str(native_profile)))
                    wait_for(lambda: naive.poll() is not None or subprocess.check_output(["ss","-H","-ltn",f"sport = :{local_port}"],text=True).strip())
                    if naive.poll() is not None: raise RuntimeError("naive startup")
                else:
                    write_json(directory / "bridge.json",{"key":key,"token":token,"origin":f"https://localhost:{self.port}",
                               "certificate":str(self.fixture / "pki/target.crt"),"private_key":str(self.fixture / "pki/target.key"),
                               "ready":str(directory / "bridge-ready.json"),"stats":str(directory / "bridge-stats.json"),"append":mode=="append"})
                    bridge = launch([self.root.parent / "bin/bridge","--config",directory / "bridge.json"],"bridge.log")
                    wait_for(lambda:(directory / "bridge-ready.json").exists() or bridge.poll() is not None)
                    if bridge.poll() is not None: raise RuntimeError("bridge startup")
                    ready=json.loads((directory / "bridge-ready.json").read_text())
                    local_port=int(ready[kind].split(":")[1])
                if not probe:
                    inner=browser(profile(directory / "inner-profile",self.fixture / "profiles/trusted","h2",self.port,kind,local_port,self.target_port),directory / "inner-webdriver.log")
            if not control:
                worker=browser(profile(directory / "worker-profile",self.fixture / "profiles/trusted",self.protocol,self.port),directory / "worker-webdriver.log")
                if self.protocol=="h3":
                    warm=secrets.token_hex(16)
                    worker.get(f"https://127.0.0.1:{self.values['NAIVEFOX_FIXTURE_HTTPS_PORT']}/camouflage/index.html?scenario=initial&completion={warm}")
                    wait_for(lambda:(self.fixture / "completions" / warm).exists())
            completion=secrets.token_hex(16)
            workload=f"https://localhost:{self.target_port}/camouflage/index.html?scenario=browser_page&asset_base=262144&completion={completion}"
            fragment={"rounds":str(rounds)}
            if not reference and not control:
                fragment["bridge"]=f"wss://127.0.0.1:{ready['websocket'].split(':')[1]}/bridge?token={token}"
            outer=f"https://localhost:{self.port}/#"+urllib.parse.urlencode(fragment)
            if probe:
                if reference or control or capture:raise RuntimeError("probe is separate functional admission")
                expected=subprocess.check_output(["curl","--silent","--show-error","--fail","--noproxy","*",f"http://127.0.0.1:{self.values['NAIVEFOX_FIXTURE_HTTP_PORT']}/camouflage/resource?size=98304"])
                expected_digest=hashlib.sha256(expected).hexdigest()
            if capture:
                monitor=launch([sys.executable,INTEGRATION / "monitor-network-mutations.py","--ready",directory / "network-ready","--events",directory / "network-events","--done",directory / "network-done"],"network.log")
                wait_for(lambda:(directory / "network-ready").exists() or monitor.poll() is not None)
                if monitor.poll() is not None:raise RuntimeError("network monitor startup")
                cap=launch(["dumpcap","-q","-i","any","-f",f"port {self.port}","-a","duration:20","-a","filesize:131072","-w",Path(stage.name) / "outer.pcapng"],"dumpcap.log")
                wait_for(lambda:"File:" in (directory / "dumpcap.log").read_text() or cap.poll() is not None)
                if cap.poll() is not None:raise RuntimeError("capture startup")
            start=time.monotonic()
            if probe:
                for index in range(4):
                    listener_kind="socks" if index%2==0 else "http"
                    scheme="socks5h" if listener_kind=="socks" else "http"
                    probes.append(launch(["curl","--silent","--show-error","--fail","--max-time","15","--noproxy","","--proxy",scheme+"://"+ready[listener_kind],
                        "--http2","--cacert",self.fixture / "pki/root.crt","--output",directory / f"probe-{index}.bin",f"https://localhost:{self.target_port}/camouflage/resource?size=98304"],f"probe-{index}.log"))
            if inner:inner.get(workload)
            if worker:worker.get(outer)
            app_done=control
            target_done=reference
            while time.monotonic()-start<(2 if capture else 15):
                if worker:
                    state=worker.execute_script("return {done:!!window.__NFC_DONE__,error:window.__NFC_ERROR__||null,round:window.__NFC_ROUND__||0,early:window.__NFC_EARLY_CELLS__||0,early_filler:window.__NFC_EARLY_FILLER__||0}")
                    if not isinstance(state,dict):time.sleep(.01);continue
                    if state["error"]:raise RuntimeError("SPA admission: "+state["error"])
                    app_done=state["done"]
                    result["completed_rounds"]=state["round"]
                    result["early_prefix_cells"]=state["early"]
                    result["filler_pending_at_delivery"]=state["early_filler"]
                target_done=all(p.poll()==0 for p in probes) if probe else reference or (self.fixture / "completions" / completion).exists()
                if not reference and target_done and "target_done_ms" not in result:result["target_done_ms"]=round((time.monotonic()-start)*1000,3)
                if app_done and (not control or target_done):
                    result["app_done_ms"]=round((time.monotonic()-start)*1000,3)
                    break
                time.sleep(.01)
            result["app_done"]=app_done;result["target_done"]=target_done
            if probe and target_done:
                if any(hashlib.sha256((directory / f"probe-{index}.bin").read_bytes()).hexdigest()!=expected_digest for index in range(4)):
                    raise RuntimeError("byte-exact proxy probe digest")
                result["byte_exact_concurrent_probes"]=4
                result["probe_body_bytes_each"]=len(expected)
            journal=self.fixture / "inner-h2-access.jsonl"
            if journal.exists():
                requests=[json.loads(line) for line in journal.read_text().splitlines() if line]
                result["inner_http_statuses"]=[r.get("status") for r in requests if "camouflage" in r.get("request",{}).get("uri","")]
            if inner and not target_done:
                uri=inner.execute_script("return document.documentURI")
                result["inner_state"]=inner.execute_script("return document.readyState")
                result["inner_error_code"]=urllib.parse.parse_qs(urllib.parse.urlsplit(uri).query).get("e",[""])[0]
            if capture:
                remaining=2-(time.monotonic()-start)
                if remaining>0:time.sleep(remaining)
                if cap.poll() is None:cap.send_signal(signal.SIGINT);cap.wait(timeout=10)
                validate_dumpcap_log((directory / "dumpcap.log").read_text())
                result["capture_window_seconds"]=2
                stop(monitor)
                if monitor.returncode!=0 or not (directory / "network-done").exists() or (directory / "network-events").stat().st_size:
                    raise RuntimeError("capture network mutation")
                result["network_mutation_check"]="passed"
            if worker:result["worker_memory"]=process_memory(worker.capabilities["moz:processID"])
            if bridge:result["bridge_memory"]=process_memory(bridge.pid)
            if naive:result["native_memory"]=process_memory(naive.pid)
            if not app_done or not target_done:raise RuntimeError("workload outside application capacity phase")
            result["admitted"]=True
        except Exception as error:
            with (directory / "private-error.log").open("w") as failure_log:traceback.print_exc(file=failure_log)
            result["admitted"]=False
            result["failure"]=str(error) if isinstance(error,RuntimeError) else type(error).__name__
        finally:
            if cap is not None and cap.poll() is None:cap.send_signal(signal.SIGINT);cap.wait(timeout=10)
            if stage:
                staged=Path(stage.name) / "outer.pcapng"
                if staged.exists():shutil.move(staged,directory / "outer.pcapng")
                stage.cleanup()
            for driver in (worker,inner):
                if driver:
                    try:driver.quit()
                    except Exception:pass
            for process in (*probes,monitor,bridge,naive,caddy):stop(process)
            for file in files:file.close()
            for source in ("server-stats","bridge-stats"):
                path=directory / (source+".json")
                if path.exists():result[source]=json.loads(path.read_text())
            write_json(directory / "result.json",result)
        return result

    def screen(self, count, seed, app_profile="v1", lean=False):
        arms=("application-default-socks","application-replace-socks") if lean else ("application-default-socks","application-default-http","application-replace-socks","application-replace-http","application-append-socks")
        schedule=superblocks.schedule_rows(seed,self.protocol,count,["browser_page"],arms)
        write_json(self.root / "schedule.json",schedule)
        feature_dir=self.root / "features";feature_dir.mkdir()
        for index,row in enumerate(schedule):
            arm=row["naivefox_arm"]
            mode="reference" if arm=="reference" else arm.split("-")[1]
            kind="http" if arm.endswith("-http") else "socks"
            name=f"sample-{index:03d}"
            result=self.sample(name,kind,mode,0,True,app_profile=app_profile)
            summary={k:v for k,v in result.items() if k not in ("server-stats","bridge-stats","inner_http_statuses")}
            print(json.dumps(summary,sort_keys=True),flush=True)
            if not result["admitted"]:raise RuntimeError("screen stopped at failed admission")
            stats=result["server-stats"]
            if mode=="default" and self.protocol=="h3":
                required=["GET /","GET /assets/site.css","GET /assets/app.js",*[f"GET /assets/image-{i}.svg" for i in range(1,5)]]
                if any(stats["requests"].get(path)!=1 for path in required):raise RuntimeError("default H3 six-resource admission")
            validate_http_graph(stats,app_profile,mode)
            destination=feature_dir / (name+".json")
            features.extract(SimpleNamespace(pcap=str(self.root / name / "outer.pcapng"),protocol=self.protocol,server_port=self.port,
                scenario="browser_page",label=row["label"],session_id=name,naivefox_arm=arm,experiment_block=row["experiment_block"],output=str(destination)))
            numeric=json.loads(destination.read_text())["features"]
            if numeric["tls_client_hello_count"]!=1:raise RuntimeError("outer ClientHello count")
            if self.protocol=="h2":
                events,_=features.packet_events_h2(str(self.root / name / "outer.pcapng"),self.port)
                if len({e["flow"] for e in events})!=1 or sum(e["syn"] and not e["ack"] for e in events)!=1:
                    raise RuntimeError("outer TCP identity")
            else:
                events,_=features.packet_events_h3(str(self.root / name / "outer.pcapng"),self.port)
                if any(e["wire_size"]>1500 for e in events):raise RuntimeError("QUIC superframe")
                identities={value for e in events for value in features.split_values(e["flow"])}
                if len(identities)!=1 or numeric["quic_tcp_probe_packet_count"]!=0:raise RuntimeError("outer QUIC identity or TCP probe")
        features.merge(SimpleNamespace(input_dir=str(feature_dir),output=str(self.root / "features.csv"),expected_superblocks=count,expected_superblock_arms=",".join(arms)))
        subprocess.run([sys.executable,str(INTEGRATION / "analyze-camouflage-arms.py"),"--features",str(self.root / "features.csv"),
            "--output-json",str(self.root / "analysis.json"),"--output-summary",str(self.root / "analysis.md"),"--mode","gate","--seed",str(seed),
            "--bootstrap","1000","--permutations","999","--views","initial_packets_16,packets_17_32,initial_packets_32,initial_time_250ms,whole"],check=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--protocol",choices=["h2","h3"],default="h2")
    parser.add_argument("--mode",choices=["reference","replace","append","default"],default="replace")
    parser.add_argument("--kind",choices=["socks","http"],default="socks")
    parser.add_argument("--rounds",type=int,default=0)
    parser.add_argument("--app-profile",choices=PROFILES,default="v1")
    parser.add_argument("--screen-lean",action="store_true")
    parser.add_argument("--sweep",action="store_true")
    parser.add_argument("--timing-pair",type=int,default=0)
    parser.add_argument("--outer-rate-mbit",type=int,default=0)
    parser.add_argument("--capture",action="store_true")
    parser.add_argument("--probe",action="store_true")
    parser.add_argument("--screen",type=int,default=0)
    parser.add_argument("--seed",type=int,default=202608301)
    args=parser.parse_args()
    if args.outer_rate_mbit < 0 or args.outer_rate_mbit > 1000 or args.timing_pair < 0:
        parser.error("invalid timing/rate bounds")
    if args.outer_rate_mbit and (args.capture or args.screen):
        parser.error("shaped links are functional timing only; no transmit-copy residual scoring")
    if os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK")!="1" or not os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED"):
        raise SystemExit("isolated fixture namespace required")
    args.root.mkdir(mode=0o700,parents=True,exist_ok=True)
    runtime=args.root / "runtime"
    runtime.mkdir(mode=0o700,exist_ok=True)
    os.environ["XDG_RUNTIME_DIR"]=str(runtime)
    os.environ["MOZ_HEADLESS"]="1"
    os.environ["LD_LIBRARY_PATH"]=str(FIREFOX.parent)
    for variable in ("SSLKEYLOGFILE","DISPLAY","WAYLAND_DISPLAY"):
        os.environ.pop(variable,None)
    campaign=Campaign(args.root,args.protocol)
    try:
        campaign.start()
        if args.outer_rate_mbit:
            campaign.shape_outer(args.outer_rate_mbit)
        if args.timing_pair:
            rng=random.Random(args.seed)
            schedule=[]
            for block in range(args.timing_pair):
                profiles=["staged-fast20","staged-stream20"]
                rng.shuffle(profiles)
                schedule.extend({"block":block,"profile":value} for value in profiles)
            write_json(args.root / "timing-schedule.json",schedule)
            failed=False
            for index,row in enumerate(schedule):
                result=campaign.sample(f"timing-{index:03d}",app_profile=row["profile"])
                print(json.dumps(result,sort_keys=True),flush=True)
                failed |= not result["admitted"]
            write_json(args.root / "outer-shaping-final.json",{"qdisc":subprocess.check_output(["tc","-s","qdisc","show","dev","lo"],text=True)})
            return 1 if failed else 0
        if args.sweep:
            for variant in PROFILES:
                result=campaign.sample("functional-"+variant,app_profile=variant)
                print(json.dumps(result,sort_keys=True),flush=True)
            return 0
        if args.screen:
            campaign.screen(args.screen,args.seed,args.app_profile,args.screen_lean)
            return 0
        result=campaign.sample("admission-"+secrets.token_hex(4),args.kind,args.mode,args.rounds,args.capture,args.probe,args.app_profile)
        print(json.dumps(result,sort_keys=True),flush=True)
        return 0 if result["admitted"] else 1
    finally:campaign.close()


if __name__=="__main__":sys.exit(main())
