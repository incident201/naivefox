#!/usr/bin/env python3
"""Corpus-specific, family-disjoint classification; no absolute camouflage verdict."""
import argparse
import collections
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import statistics
import sys

INTEGRATION=Path(__file__).resolve().parents[1]/"integration"
sys.path.insert(0,str(INTEGRATION))
import camouflage_features as passive
spec=importlib.util.spec_from_file_location("existing_classifier",INTEGRATION/"analyze-camouflage.py")
learner=importlib.util.module_from_spec(spec);spec.loader.exec_module(learner)
VIEWS=("initial_packets_16","packets_17_32","initial_packets_32","initial_time_250ms","whole")
MAX_FEATURES=64
L2=0.1
ITERATIONS=200


def load(root):
    meta=json.loads((root/"benchmark.json").read_text())
    if meta.get("pilot") or meta.get("status")!="complete" or meta.get("completed_samples")!=392:
        raise ValueError("only a complete, non-pilot 96-page benchmark can be analyzed")
    rows=[];counts=collections.Counter();partitions={};variants=collections.defaultdict(set)
    for path in sorted((root/"records").glob("*.json")):
        row=json.loads(path.read_text())
        if not row.get("admitted") or not row.get("network_stable") or row.get("capture_seconds")!=5:
            raise ValueError("unadmitted or incompatible observation")
        feature=json.loads((root/"features"/path.name).read_text())
        if feature["scenario"]!="browser_diversity" or feature["protocol"]!=meta["protocol"] or feature["session_id"]!=path.stem:
            raise ValueError("feature/observation identity mismatch")
        passive.validate_features(feature["features"])
        row.update(features=feature["features"],session_id=path.stem,_weight_group=row["family"])
        if row["role"]!="fronting-browser":
            key=(row["family"],row["variant"],row["role"]);counts[key]+=1
            variants[row["family"]].add(row["variant"])
            if row["family"] in partitions and partitions[row["family"]]!=row["partition"]:raise ValueError("family crosses partitions")
            partitions[row["family"]]=row["partition"]
        rows.append(row)
    roles=("firefox_a","firefox_b","classic","no_connect")
    expected={(family,variant,role) for family in partitions for variant in range(4) for role in roles}
    if len(rows)!=392 or len(partitions)!=24 or set(counts)!=expected or any(value!=1 for value in counts.values()):raise ValueError("incomplete family/role matrix")
    if any(value!={0,1,2,3} for value in variants.values()) or collections.Counter(partitions.values())!={0:6,1:6,2:6,3:6}:raise ValueError("invalid family partitions")
    front=[row for row in rows if row["role"]=="fronting-browser"]
    if collections.Counter(row["partition"] for row in front)!={0:2,1:2,2:2,3:2}:raise ValueError("fronting controls incomplete")
    return meta,rows


def split(rows,test_partition):
    calibration=(test_partition+1)%4
    test=[i for i,row in enumerate(rows) if row["partition"]==test_partition]
    calibrate=[i for i,row in enumerate(rows) if row["partition"]==calibration]
    train=[i for i,row in enumerate(rows) if row["partition"] not in (test_partition,calibration)]
    groups=[{rows[i]["family"] for i in values} for values in (train,calibrate,test)]
    if not all(groups) or groups[0]&groups[1] or groups[0]&groups[2] or groups[1]&groups[2]:raise ValueError("family leakage")
    return train,calibrate,test


def fpr_threshold(scores,target):
    if not scores:raise ValueError("no calibration Firefox observations")
    ordered=sorted(scores,reverse=True)
    allowed=math.floor(target*len(ordered))
    return math.nextafter(ordered[min(allowed,len(ordered)-1)],math.inf)


def rates(items,key):
    zero=[item for item in items if item["label"]==0];one=[item for item in items if item["label"]==1]
    fp=sum(item[key] for item in zero);tp=sum(item[key] for item in one)
    return {"fpr":fp/len(zero) if zero else None,"tpr":tp/len(one) if one else None,
            "false_positives":fp,"browser_samples":len(zero),"true_positives":tp,"proxy_samples":len(one)}


def fold_auc(items):
    values=[]
    for fold in range(4):
        part=[item for item in items if item["fold"]==fold]
        values.append(learner.auc([item["label"] for item in part],[item["score"] for item in part]))
    return statistics.fmean(values),values


def uncertainty(items,seed,iterations=500):
    rng=random.Random(seed);by_fold={}
    for fold in range(4):
        families=collections.defaultdict(list)
        for item in items:
            if item["fold"]==fold:families[item["family"]].append(item)
        by_fold[fold]=families
    samples=[]
    for _ in range(iterations):
        selected=[]
        for families in by_fold.values():
            names=sorted(families)
            for name in rng.choices(names,k=len(names)):selected.extend(families[name])
        samples.append(fold_auc(selected)[0])
    return [learner.percentile(samples,.025),learner.percentile(samples,.975)]


def comparison(all_rows,kind,view,seed):
    null=kind=="firefox_null"
    allowed=("firefox_a","firefox_b") if null else ("firefox_a","firefox_b",kind)
    rows=[row for row in all_rows if row["role"] in allowed]
    labels=[int(row["role"]==("firefox_b" if null else kind)) for row in rows]
    predictions=[];models=[];front_scores=[]
    for fold in range(4):
        train,calibrate,test=split(rows,fold)
        names=learner.view_feature_names(sorted({name for i in train for name in rows[i]["features"]}),view)
        model=learner.fit_model(rows,labels,train,names,MAX_FEATURES,L2,ITERATIONS)
        cal_scores=[learner.predict(model,rows[i]) for i in calibrate]
        cal_labels=[labels[i] for i in calibrate]
        browser_scores=[score for label,score in zip(cal_labels,cal_scores) if label==0]
        thresholds={"flag5":fpr_threshold(browser_scores,.05),"flag10":fpr_threshold(browser_scores,.10),
                    "balanced":learner.best_threshold(cal_labels,cal_scores)}
        for i in test:
            score=learner.predict(model,rows[i])
            predictions.append({"session_id":rows[i]["session_id"],"family":rows[i]["family"],"fold":fold,"label":labels[i],"score":score,
                                **{name:int(score>=threshold) for name,threshold in thresholds.items()}})
        if not null:
            for row in all_rows:
                if row["role"]=="fronting-browser" and row["partition"]==fold:
                    score=learner.predict(model,row)
                    front_scores.append({"fold":fold,"score":score,**{name:int(score>=threshold) for name,threshold in thresholds.items()}})
        models.append({"fold":fold,"training_families":sorted({rows[i]["family"] for i in train}),
                       "calibration_families":sorted({rows[i]["family"] for i in calibrate}),"test_families":sorted({rows[i]["family"] for i in test}),
                       "thresholds":thresholds,"calibration_browser_samples":len(browser_scores),
                       "calibration_false_positives":{key:sum(score>=threshold for score in browser_scores) for key,threshold in thresholds.items()},
                       "top_features":sorted(zip(model["features"],model["weights"]),key=lambda value:-abs(value[1]))[:10]})
    point,folds=fold_auc(predictions)
    balanced=rates(predictions,"balanced")
    result={"auc":point,"fold_auc":folds,"conditional_family_bootstrap_ci95":uncertainty(predictions,seed),
            "calibrated_5pct":rates(predictions,"flag5"),"calibrated_10pct":rates(predictions,"flag10"),
            "balanced_accuracy":(balanced["tpr"]+1-balanced["fpr"])/2,
            "folds":models,"predictions":predictions}
    if front_scores:
        result["genuine_fronting_browser"]={"samples":len(front_scores),"false_positives_at_5pct_threshold":sum(row["flag5"] for row in front_scores),
                                             "false_positives_at_10pct_threshold":sum(row["flag10"] for row in front_scores),"scores":front_scores}
    return result


def costs(rows):
    groups=collections.defaultdict(dict)
    for row in rows:
        if row["role"] in ("classic","no_connect"):groups[(row["family"],row["variant"])][row["role"]]=row
    equal=[];unequal=0
    for pair in groups.values():
        old,new=pair["classic"],pair["no_connect"]
        a,b=old["workload"]["origin"],new["workload"]["origin"]
        if (a.get("bytes",0),a.get("upload_bytes",0))!=(b.get("bytes",0),b.get("upload_bytes",0)):
            unequal+=1;continue
        equal.append((old,new))
    if not equal:return {"equal_work_pairs":0,"unequal_work_pairs":unequal}
    old_bytes=statistics.fmean(a["wire_bytes"] for a,b in equal);new_bytes=statistics.fmean(b["wire_bytes"] for a,b in equal)
    old_time=statistics.fmean(a["page_done_ms"] for a,b in equal);new_time=statistics.fmean(b["page_done_ms"] for a,b in equal)
    return {"equal_work_pairs":len(equal),"unequal_work_pairs":unequal,"classic_mean_wire_bytes":old_bytes,"no_connect_mean_wire_bytes":new_bytes,
            "extra_wire_percent":100*(new_bytes/old_bytes-1),"classic_mean_done_ms":old_time,"no_connect_mean_done_ms":new_time,
            "effective_rate_loss_percent":100*(1-old_time/new_time)}


def analyze(root):
    meta,rows=load(root)
    report={"status":"CORPUS_SPECIFIC_SCREENING","metadata":meta,"analysis_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "model":{"name":"existing L2 logistic learner","max_features":MAX_FEATURES,"l2":L2,"iterations":ITERATIONS},
            "notes":["Family-disjoint training/calibration/test; no matched-trace subtraction.",
                     "AUC is the mean within held-out folds, not pooled uncalibrated probabilities.",
                     "Confidence intervals condition on fitted models, not full-pipeline refit inference.",
                     "5%/10% are calibration targets; report achieved held-out FPR rather than promising the target.",
                     "A fronting-browser false positive can indicate surface recognition, not non-browser origin.",
                     "Synthetic cold single-origin corpus does not establish Internet-wide indistinguishability."],"views":{},"costs":costs(rows)}
    for index,view in enumerate(VIEWS):
        report["views"][view]={kind:comparison(rows,kind,view,meta["seed"]+index*10+k) for k,kind in enumerate(("classic","no_connect","firefox_null"))}
        print(json.dumps({"view":view,"auc":{kind:value["auc"] for kind,value in report["views"][view].items()}}),flush=True)
    (root/"classification.json").write_text(json.dumps(report,indent=2)+"\n")
    lines=["# Browser-diversity classification", "", "Corpus-specific screening, not a camouflage verdict.", "",
           "| View | Classic AUC | No Connect AUC | Firefox A/B AUC | Classic test TPR/FPR (5% calibration) | No Connect test TPR/FPR (5% calibration) |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for view,values in report["views"].items():
        old,new,null=values["classic"],values["no_connect"],values["firefox_null"]
        lines.append(f'| {view} | {old["auc"]:.4f} | {new["auc"]:.4f} | {null["auc"]:.4f} | {old["calibrated_5pct"]["tpr"]:.1%} / {old["calibrated_5pct"]["fpr"]:.1%} | {new["calibrated_5pct"]["tpr"]:.1%} / {new["calibrated_5pct"]["fpr"]:.1%} |')
    lines += ["", "All counts, fronting-browser false positives, conditional intervals, splits and predictions are in classification.json.", ""]
    (root/"classification.md").write_text("\n".join(lines))
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    args=parser.parse_args();analyze(args.root)
