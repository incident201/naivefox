#!/usr/bin/env python3
"""Recompute the closed campaign's cold matrix and distinct warm-session costs."""
import argparse
import json
from pathlib import Path

from costs import summarize
from session_costs import summarize as session_summary

VIEWS = ("initial_packets_16", "packets_17_32", "initial_packets_32", "initial_time_250ms", "whole")


def collect(root):
    output = {"profile": "continuous-bulk-pipeline", "status": "screening_only",
              "note": "Four cold blocks per protocol, two warm pairs; not statistical indistinguishability or an Internet speed prediction.",
              "cold": [], "warm": {}}
    for protocol in ("h2", "h3"):
        campaign=root/("final-pipeline-matrix-"+protocol)
        report=json.loads((campaign/"analysis.json").read_text())
        costs=summarize(campaign)
        inference=report["protocols"][protocol]["inference"]
        if inference["blocks"]!=4 or len(list(campaign.glob("sample-*/result.json")))!=24:
            raise ValueError("incomplete cold qualification")
        if any(not json.loads(path.read_text()).get("admitted") for path in campaign.glob("sample-*/result.json")):
            raise ValueError("unadmitted cold sample")
        for kind in ("socks","http"):
            old="application-default-"+kind;new="application-replace-"+kind
            row={"protocol":protocol,"listener":kind,"blocks":4,"residual":{},
                 "default":costs["arms"][old],"candidate":costs["arms"][new],"penalties":costs["comparisons"][kind]}
            for view in VIEWS:
                arms=report["protocols"][protocol]["views"][view]["arms"]
                baseline=arms[old]["mean_distance"];candidate=arms[new]["mean_distance"]
                row["residual"][view]={"default":baseline,"candidate":candidate,
                                       "candidate_ci95":arms[new]["bootstrap_ci95"],
                                       "reduction_percent":100*(1-candidate/baseline)}
            output["cold"].append(row)
        warm=root/("final-pipeline-native-h2" if protocol=="h2" else "final-pipeline-native-h3-retry1")
        values=session_summary(warm)
        output["warm"][protocol]={"wire":values["wire"],"stages":{}}
        for stage,values in values["curl_stages"].items():
            old,new=values["default"],values["replace"]
            output["warm"][protocol]["stages"][stage]={"default_ms":old,"candidate_ms":new,
                "time_increase_percent":100*(new/old-1),"effective_rate_decrease_percent":100*(1-old/new),
                "default_samples_ms":values["default_samples_ms"],"candidate_samples_ms":values["replace_samples_ms"]}
    return output


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    value=json.dumps(collect(args.root),indent=2)+"\n"
    if args.output:args.output.write_text(value)
    else:print(value,end="")
