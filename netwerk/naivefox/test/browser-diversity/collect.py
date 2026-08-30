#!/usr/bin/env python3
"""Collect a separate page-family benchmark; never score or tune the transport."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
from types import SimpleNamespace

from origin import Origin
from workload import BrowserWorkload

CARRIER = Path(__file__).resolve().parents[1] / "application-carrier"
sys.path.insert(0,str(CARRIER))
spec=importlib.util.spec_from_file_location("carrier_runner",CARRIER/"run.py")
carrier=importlib.util.module_from_spec(spec);spec.loader.exec_module(carrier)
ROLES=("firefox_a","firefox_b","classic","no_connect")


def schedule(manifest,seed,pilot=False,pilot_pages=None):
    pages=manifest["pages"]
    if pilot:
        wanted=set(pilot_pages or ("article-0","documentation-1","gallery-3","settings-1"))
        pages=[page for page in pages if page["id"] in wanted]
        if len(pages)!=len(wanted):raise ValueError("unknown pilot page")
    rng=random.Random(seed);pages=list(pages);rng.shuffle(pages)
    cases=[]
    for page in pages:
        roles=list(ROLES);rng.shuffle(roles)
        cases.extend({"page":page["id"],"family":page["family"],"variant":page["variant"],"partition":page["partition"],"role":role} for role in roles)
    if not pilot:
        extra=[{"page":manifest["pages"][0]["id"],"family":"fronting-control","variant":i%2,"partition":i//2,"role":"fronting-browser"} for i in range(8)]
        for value in extra:cases.insert(rng.randrange(len(cases)+1),value)
    return cases


def validate_flow(pcap,protocol,port,numeric):
    events,_=(carrier.features.packet_events_h2 if protocol=="h2" else carrier.features.packet_events_h3)(str(pcap),port)
    if not events or carrier.outer_flow_count(events)!=1 or numeric["tls_client_hello_count"]!=1:
        raise RuntimeError("benchmark requires one captured origin flow and ClientHello")
    if any(event["wire_size"]>1500 for event in events):raise RuntimeError("oversized capture frame")
    if protocol=="h2" and sum(event["syn"] and not event["ack"] for event in events)!=1:raise RuntimeError("H2 opening not captured")
    if protocol=="h3" and numeric["quic_tcp_probe_packet_count"]!=0:raise RuntimeError("strict H3 TCP probe")


def collect(args):
    if os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK")!="1" or not os.environ.get("NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED"):
        raise ValueError("isolated WSL namespace required")
    if args.root.exists() and any(args.root.iterdir()):raise ValueError("new benchmark directory required")
    os.umask(0o077);args.root.mkdir(parents=True,mode=0o700,exist_ok=True)
    runtime=args.root/"runtime";runtime.mkdir()
    os.environ.update(XDG_RUNTIME_DIR=str(runtime),MOZ_HEADLESS="1",LD_LIBRARY_PATH=str(carrier.FIREFOX.parent))
    for name in ("SSLKEYLOGFILE","DISPLAY","WAYLAND_DISPLAY"):os.environ.pop(name,None)
    origin=Origin(args.corpus).start()
    plan=schedule(origin.manifest,args.seed,args.pilot,args.pilot_page)
    carrier.write_json(args.root/"schedule.json",plan)
    carrier.write_json(args.root/"benchmark.json",{"schema_version":1,"purpose":"browser-diversity","pilot":args.pilot,"protocol":args.protocol,"listener":args.listener,"seed":args.seed,
        "expected_samples":len(plan),"families":origin.manifest["family_count"],"corpus_sha256":hashlib.sha256((args.corpus/"manifest.json").read_bytes()).hexdigest(),
        "source_sha256":{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ("collect.py","corpus.py","origin.py","workload.py")},
        "native_package_manifest_sha256":hashlib.sha256((carrier.PACKAGE/"runtime-manifest.json").read_bytes()).hexdigest(),
        "native_full_source_ref":subprocess.check_output(["git","-C",str(CARRIER),"rev-parse","naivefox-full-source"],text=True).strip(),
        "experimental_profile":carrier.DEFAULT_PROFILE,"capture_seconds":5,"status":"collecting"})
    features=args.root/"features";features.mkdir()
    records=args.root/"records";records.mkdir()
    campaign=carrier.Campaign(args.root,args.protocol)
    completed=0
    try:
        campaign.start()
        for index,case in enumerate(plan):
            origin.reset()
            name=f"private-{index:04d}"
            page=origin.pages[case["page"]]
            workload=BrowserWorkload(origin,page,case["role"]=="fronting-browser")
            mode="default" if case["role"]=="classic" else "replace" if case["role"]=="no_connect" else "reference"
            result=campaign.sample(name,kind=args.listener,mode=mode,capture=True,browser_workload=workload)
            directory=args.root/name
            if not result["admitted"]:
                carrier.write_json(records/(name+".json"),{**case,"admitted":False,"failure":result.get("failure","unknown")})
                raise RuntimeError("sample admission failed; private diagnostics retained")
            label=case["role"] if case["role"] in ("firefox_a","firefox_b") else "firefox_a" if case["role"]=="fronting-browser" else "naivefox"
            destination=features/(name+".json")
            carrier.features.extract(SimpleNamespace(pcap=str(directory/"outer.pcapng"),protocol=args.protocol,server_port=campaign.port,
                scenario="browser_diversity",label=label,session_id=name,naivefox_arm=case["role"],experiment_block=case["family"],output=str(destination)))
            numeric=json.loads(destination.read_text())["features"]
            validate_flow(directory/"outer.pcapng",args.protocol,campaign.port,numeric)
            safe={**case,"admitted":True,"protocol":args.protocol,"listener":args.listener,
                  "capture_seconds":result["capture_window_seconds"],"network_stable":result["network_mutation_check"]=="passed",
                  "page_done_ms":result.get("target_done_ms") if mode!="reference" else result["app_done_ms"],
                  "workload":result.get("browser_workload"),"wire_bytes":numeric["whole_client_wire_bytes"]+numeric["whole_server_wire_bytes"]}
            carrier.write_json(records/(name+".json"),safe)
            if directory.parent.resolve()!=args.root.resolve() or not directory.name.startswith("private-"):raise ValueError("cleanup scope")
            shutil.rmtree(directory)
            completed+=1
            print(json.dumps({"protocol":args.protocol,"completed":completed,"total":len(plan),"role":case["role"],"admitted":True}),flush=True)
    except (Exception,SystemExit) as error:
        carrier.write_json(args.root/"failure.json",{"error_type":type(error).__name__,"completed_samples":completed})
        raise
    finally:
        campaign.close();origin.close()
        metadata=json.loads((args.root/"benchmark.json").read_text())
        metadata.update(completed_samples=completed,status="complete" if completed==len(plan) else "incomplete")
        carrier.write_json(args.root/"benchmark.json",metadata)
        for name in ("fixture","runtime"):
            path=args.root/name
            if path.exists() and path.parent.resolve()==args.root.resolve():shutil.rmtree(path)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--corpus",type=Path,required=True)
    parser.add_argument("--protocol",choices=("h2","h3"),required=True)
    parser.add_argument("--listener",choices=("socks","http"),default="socks")
    parser.add_argument("--seed",type=int,default=202608361)
    parser.add_argument("--pilot",action="store_true")
    parser.add_argument("--pilot-page",action="append")
    args=parser.parse_args()
    if args.pilot_page and not args.pilot:parser.error("page selection is pilot-only")
    collect(args)
