#!/usr/bin/env python3
"""Audit saved H3 connection fields without rewriting primary run evidence."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import camouflage_features as features


def audit(root):
    rows = []
    for result_file in sorted(root.glob("*/result.json")):
        result = json.loads(result_file.read_text())
        if result["protocol"] != "h3":
            continue
        directory = result_file.parent
        port = result.get("outer_port")
        if port is None:
            config = json.loads((directory / "caddy.json").read_text())
            ports = {int(address.rsplit(":", 1)[1])
                     for server in config["apps"]["http"]["servers"].values()
                     if any(handler.get("handler") == "naivefox_transport" for route in server["routes"] for handler in route.get("handle", []))
                     for address in server["listen"]}
            if len(ports) != 1:
                raise ValueError("ambiguous outer server")
            port = ports.pop()
        for name in ("outer.pcapng", "idle.pcapng"):
            pcap = directory / name
            if not pcap.exists():
                continue
            events, _ = features.packet_events_h3(str(pcap), port)
            identities = {value for event in events for value in features.split_values(event["flow"])}
            tcp, _ = features.packet_events_h2(str(pcap), port)
            row = {"sample": directory.name, "capture": name, "unique_quic_ids": len(identities),
                   "raw_quic_field_forms": sorted({event["flow"] for event in events}),
                   "initial_packets": sum("0" in event["packet_types"] for event in events),
                   "tcp_packets": len(tcp), "udp_wire_bytes": sum(event["wire_size"] for event in events)}
            if tcp:
                raise ValueError("TCP in strict H3 capture")
            rows.append(row)
    report = {"campaign": root.name, "note": "empty QUIC fields excluded and coalesced fields split; primary result files remain unchanged", "captures": rows}
    (root / "connection-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    for value in sys.argv[1:]:
        print(json.dumps(audit(Path(value)), indent=2))
