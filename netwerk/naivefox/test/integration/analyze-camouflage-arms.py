#!/usr/bin/env python3

import argparse
import csv
import importlib.util
import itertools
import json
import math
import os
import random
import re
import statistics

HERE = os.path.dirname(__file__)


def load_local_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYSIS = load_local_module("camouflage_analysis_core", "analyze-camouflage.py")
SUPERBLOCKS = load_local_module(
    "camouflage_superblocks_core", "camouflage_superblocks.py"
)

SCHEMA_VERSION = 1
ARMS = SUPERBLOCKS.ARMS
MIN_SCENARIO_REPLICATES = 2
MIN_PAIRED_BLOCKS = 30
EXACT_PERMUTATION_BLOCK_LIMIT = 16
NORMALIZED_EXCESS_CAP = 4.0
MECHANISM_TOP_FEATURES = 12
SIGNED_PACKET_FEATURE = re.compile(
    r"^packet_(?P<index>[0-9]{3})_"
    r"(?P<metric>transport_size_signed|wire_size_signed)$"
)


def load_dataset(path):
    with open(path, newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise SystemExit("feature dataset has no header")
        required = SUPERBLOCKS.METADATA_FIELDS
        missing = required - set(reader.fieldnames)
        if missing:
            raise SystemExit(
                f"superblock dataset lacks metadata fields: {sorted(missing)}"
            )
        feature_names = [
            name for name in reader.fieldnames if name not in ANALYSIS.METADATA_FIELDS
        ]
        invalid = [
            name
            for name in feature_names
            if not name.startswith(ANALYSIS.FEATURE_PREFIXES)
            or any(term in name for term in ANALYSIS.FORBIDDEN_FEATURE_TERMS)
        ]
        if invalid:
            raise SystemExit(f"unknown or unsafe feature columns: {invalid[:8]}")
        source_rows = list(reader)

    try:
        SUPERBLOCKS.validate_superblocks(source_rows, require_dataset=True)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    rows = []
    for source in source_rows:
        if source["schema_version"] != str(SCHEMA_VERSION):
            raise SystemExit("unsupported camouflage feature schema")
        values = {}
        for name in feature_names:
            try:
                value = float(source[name] or 0.0)
            except ValueError as error:
                raise SystemExit(f"non-numeric feature {name}") from error
            if not math.isfinite(value):
                raise SystemExit(f"non-finite feature {name}")
            values[name] = value
        rows.append({
            "protocol": source["protocol"],
            "scenario": source["scenario"],
            "label": source["label"],
            "naivefox_arm": source["naivefox_arm"],
            "session_id": source["session_id"],
            "experiment_block": source["experiment_block"],
            "features": values,
        })

    for count_name in ANALYSIS.PHASE_COUNT_FEATURES:
        if count_name not in feature_names:
            continue
        indicator_name = count_name.removesuffix("_packet_count") + "_present"
        if indicator_name in feature_names:
            continue
        feature_names.append(indicator_name)
        for row in rows:
            row["features"][indicator_name] = float(
                row["features"].get(count_name, 0.0) > 0.0
            )
    return rows, feature_names


def group_superblocks(rows, arms=None):
    selected_arms = SUPERBLOCKS.infer_arms(rows) if arms is None else tuple(arms)
    grouped = {}
    for row in rows:
        key = (row["protocol"], row["experiment_block"])
        block = grouped.setdefault(
            key,
            {
                "protocol": row["protocol"],
                "experiment_block": row["experiment_block"],
                "scenario": row["scenario"],
                "references": {},
                "arms": {},
            },
        )
        if block["scenario"] != row["scenario"]:
            raise ValueError("one superblock spans multiple workloads")
        if row["naivefox_arm"] == SUPERBLOCKS.REFERENCE_ARM:
            block["references"][row["label"]] = row["features"]
        else:
            block["arms"][row["naivefox_arm"]] = row["features"]
    for block in grouped.values():
        if set(block["references"]) != {"firefox_a", "firefox_b"}:
            raise ValueError("superblock lacks the common Firefox A/B controls")
        if set(block["arms"]) != set(selected_arms):
            raise ValueError("superblock lacks one or more NaiveFox arms")
    return list(grouped.values())


def feature_tolerance(*values):
    return 1e-9 * (1.0 + max(abs(value) for value in values))


def control_scales(blocks, feature_names):
    scales = {}
    invariant = 0
    for name in feature_names:
        radii = []
        magnitudes = []
        for block in blocks:
            left = block["references"]["firefox_a"].get(name, 0.0)
            right = block["references"]["firefox_b"].get(name, 0.0)
            radii.append(abs(left - right) / 2.0)
            magnitudes.extend((left, right))
        scale = ANALYSIS.percentile(radii, 0.75)
        tolerance = feature_tolerance(*magnitudes)
        if scale <= tolerance:
            scale = 0.0
            invariant += 1
        scales[name] = (scale, tolerance)
    return scales, invariant


def block_distances(blocks, feature_names, arms):
    scales, invariant = control_scales(blocks, feature_names)
    scores = {}
    outside = {}
    for block in blocks:
        block_id = block["experiment_block"]
        scores[block_id] = {}
        outside[block_id] = {}
        for arm in arms:
            bounded_excess = 0.0
            outside_count = 0
            for name in feature_names:
                left = block["references"]["firefox_a"].get(name, 0.0)
                right = block["references"]["firefox_b"].get(name, 0.0)
                value = block["arms"][arm].get(name, 0.0)
                midpoint = (left + right) / 2.0
                radius = abs(left - right) / 2.0
                excess = max(0.0, abs(value - midpoint) - radius)
                scale, tolerance = scales[name]
                if excess <= tolerance:
                    normalized = 0.0
                elif scale == 0.0:
                    normalized = 1.0
                else:
                    normalized = (
                        min(excess / scale, NORMALIZED_EXCESS_CAP)
                        / NORMALIZED_EXCESS_CAP
                    )
                bounded_excess += normalized
                outside_count += normalized > 0.0
            denominator = len(feature_names)
            scores[block_id][arm] = bounded_excess / denominator
            outside[block_id][arm] = outside_count / denominator
    return {
        "scores": scores,
        "outside": outside,
        "control_calibration": {
            "features": len(feature_names),
            "control_invariant_features": invariant,
            "control_variable_features": len(feature_names) - invariant,
            "scale_quantile": 0.75,
        },
    }


def stratified_bootstrap_indices(blocks, iterations, seed):
    strata = {}
    for index, block in enumerate(blocks):
        strata.setdefault(block["scenario"], []).append(index)
    rng = random.Random(seed)
    for _ in range(iterations):
        yield [
            rng.choice(indices)
            for scenario in sorted(strata)
            for indices in [strata[scenario]]
            for _ in indices
        ]


def paired_sign_flip(differences, iterations, seed):
    if not differences:
        return {"available": False, "reason": "no paired blocks"}
    observed = abs(statistics.fmean(differences))
    tolerance = 1e-15
    count = len(differences)
    if count <= EXACT_PERMUTATION_BLOCK_LIMIT:
        extreme = 0
        total = 1 << count
        for assignment in range(total):
            value = statistics.fmean(
                difference if assignment & (1 << index) else -difference
                for index, difference in enumerate(differences)
            )
            extreme += abs(value) >= observed - tolerance
        p_value = extreme / total
        method = "exact paired sign-flip randomization"
    else:
        rng = random.Random(seed)
        extreme = 0
        for _ in range(iterations):
            value = statistics.fmean(
                difference if rng.randrange(2) else -difference
                for difference in differences
            )
            extreme += abs(value) >= observed - tolerance
        p_value = (extreme + 1) / (iterations + 1)
        total = iterations
        method = "Monte Carlo paired sign-flip randomization"
    return {
        "available": True,
        "p_value": p_value,
        "iterations": total,
        "alternative": "two-sided difference in mean paired distance",
        "method": method,
    }


def scenario_counts(blocks):
    counts = {}
    for block in blocks:
        scenario = block["scenario"]
        counts[scenario] = counts.get(scenario, 0) + 1
    return counts


def inference_status(blocks, mode, min_blocks):
    min_blocks = max(min_blocks, MIN_PAIRED_BLOCKS)
    counts = scenario_counts(blocks)
    reasons = []
    if mode not in {"standard", "research"}:
        reasons.append(f"{mode} mode is diagnostic only")
    if len(blocks) < min_blocks:
        reasons.append(f"{len(blocks)} paired blocks is below minimum {min_blocks}")
    sparse = sorted(
        scenario
        for scenario, count in counts.items()
        if count < MIN_SCENARIO_REPLICATES
    )
    if sparse:
        reasons.append(
            "workloads with fewer than two paired blocks: " + ", ".join(sparse)
        )
    return {
        "supports_paired_inference": not reasons,
        "status": "eligible" if not reasons else "insufficient",
        "reasons": reasons,
        "blocks": len(blocks),
        "scenario_counts": counts,
        "minimum_blocks": min_blocks,
        "minimum_scenario_replicates": MIN_SCENARIO_REPLICATES,
    }


def ranked_arms(means, arms):
    ordered = sorted(arms, key=lambda arm: (means[arm], arm))
    ranks = []
    previous = None
    rank = 0
    for position, arm in enumerate(ordered, 1):
        if previous is None or not math.isclose(
            means[arm], previous, rel_tol=1e-12, abs_tol=1e-12
        ):
            rank = position
        ranks.append({"rank": rank, "arm": arm, "mean_distance": means[arm]})
        previous = means[arm]
    return ranks


def mechanism_diagnostics(blocks, feature_names, arms):
    """Describe passive causes without feeding diagnostics back into inference."""
    scales, _ = control_scales(blocks, feature_names)
    top_features = {}
    for arm in arms:
        summaries = []
        for name in feature_names:
            signed_deltas = []
            absolute_deltas = []
            control_differences = []
            normalized_excesses = []
            outside = 0
            scale, tolerance = scales[name]
            for block in blocks:
                left = block["references"]["firefox_a"].get(name, 0.0)
                right = block["references"]["firefox_b"].get(name, 0.0)
                value = block["arms"][arm].get(name, 0.0)
                midpoint = (left + right) / 2.0
                radius = abs(left - right) / 2.0
                delta = value - midpoint
                excess = max(0.0, abs(delta) - radius)
                if excess <= tolerance:
                    normalized = 0.0
                elif scale == 0.0:
                    normalized = 1.0
                else:
                    normalized = (
                        min(excess / scale, NORMALIZED_EXCESS_CAP)
                        / NORMALIZED_EXCESS_CAP
                    )
                signed_deltas.append(delta)
                absolute_deltas.append(abs(delta))
                control_differences.append(abs(left - right))
                normalized_excesses.append(normalized)
                outside += normalized > 0.0
            summaries.append({
                "feature": name,
                "mean_signed_delta_from_firefox_midpoint": statistics.fmean(
                    signed_deltas
                ),
                "mean_abs_delta_from_firefox_midpoint": statistics.fmean(
                    absolute_deltas
                ),
                "firefox_control_mean_abs_pair_difference": statistics.fmean(
                    control_differences
                ),
                "mean_normalized_excess": statistics.fmean(normalized_excesses),
                "outside_control_envelope_fraction": outside / len(blocks),
            })
        summaries.sort(key=lambda item: (
            -item["mean_normalized_excess"],
            -item["outside_control_envelope_fraction"],
            -item["mean_abs_delta_from_firefox_midpoint"],
            item["feature"],
        ))
        top_features[arm] = summaries[:MECHANISM_TOP_FEATURES]

    sequence = []
    coordinates = []
    for name in feature_names:
        match = SIGNED_PACKET_FEATURE.fullmatch(name)
        if match and int(match.group("index")) <= 32:
            coordinates.append((int(match.group("index")), match.group("metric"), name))
    for index, metric, name in sorted(coordinates):
        firefox_midpoints = []
        firefox_pair_deltas = []
        arm_values = {arm: [] for arm in arms}
        arm_deltas = {arm: [] for arm in arms}
        for block in blocks:
            left = block["references"]["firefox_a"].get(name, 0.0)
            right = block["references"]["firefox_b"].get(name, 0.0)
            midpoint = (left + right) / 2.0
            firefox_midpoints.append(midpoint)
            firefox_pair_deltas.append(left - right)
            for arm in arms:
                value = block["arms"][arm].get(name, 0.0)
                arm_values[arm].append(value)
                arm_deltas[arm].append(value - midpoint)
        sequence.append({
            "packet_index": index,
            "metric": metric,
            "firefox_midpoint_mean": statistics.fmean(firefox_midpoints),
            "firefox_a_minus_b_mean": statistics.fmean(firefox_pair_deltas),
            "firefox_control_mean_abs_pair_difference": statistics.fmean(
                abs(value) for value in firefox_pair_deltas
            ),
            "arms": {
                arm: {
                    "mean_signed_value": statistics.fmean(arm_values[arm]),
                    "mean_signed_delta_from_firefox_midpoint": statistics.fmean(
                        arm_deltas[arm]
                    ),
                    "mean_abs_delta_from_firefox_midpoint": statistics.fmean(
                        abs(value) for value in arm_deltas[arm]
                    ),
                }
                for arm in arms
            },
        })
    return {
        "diagnostic_only": True,
        "used_for_arm_ranking_or_inference": False,
        "scope": (
            "aggregated passively visible feature values only; no decrypted, "
            "stream, header, endpoint, or timestamp data"
        ),
        "top_features_by_arm": top_features,
        "signed_packet_sequence": sequence,
    }


def summarize_view(
    blocks,
    feature_names,
    bootstrap_iterations,
    permutations,
    seed,
    arms=None,
):
    if not feature_names:
        return {"available": False, "reason": "feature view has no columns"}
    selected_arms = SUPERBLOCKS.validate_arm_sequence(
        arms
        if arms is not None
        else (
            arm
            for arm in SUPERBLOCKS.SUPPORTED_ARMS
            if arm in blocks[0]["arms"]
        )
    )
    arm_pairs = tuple(itertools.combinations(selected_arms, 2))
    distances = block_distances(blocks, feature_names, selected_arms)
    scores = distances["scores"]
    outside = distances["outside"]
    values = {
        arm: [scores[block["experiment_block"]][arm] for block in blocks]
        for arm in selected_arms
    }
    outside_values = {
        arm: [outside[block["experiment_block"]][arm] for block in blocks]
        for arm in selected_arms
    }
    means = {arm: statistics.fmean(values[arm]) for arm in selected_arms}
    bootstrap_arm = {arm: [] for arm in selected_arms}
    bootstrap_pair = {pair: [] for pair in arm_pairs}
    best_probability = {arm: 0.0 for arm in selected_arms}
    for indices in stratified_bootstrap_indices(blocks, bootstrap_iterations, seed):
        replicate = {
            arm: statistics.fmean(values[arm][index] for index in indices)
            for arm in selected_arms
        }
        for arm in selected_arms:
            bootstrap_arm[arm].append(replicate[arm])
        for pair in arm_pairs:
            bootstrap_pair[pair].append(replicate[pair[0]] - replicate[pair[1]])
        minimum = min(replicate.values())
        winners = [
            arm
            for arm in selected_arms
            if math.isclose(replicate[arm], minimum, rel_tol=1e-12, abs_tol=1e-12)
        ]
        for arm in winners:
            best_probability[arm] += 1.0 / len(winners)

    arms = {}
    for arm in selected_arms:
        arms[arm] = {
            "mean_distance": means[arm],
            "median_block_distance": statistics.median(values[arm]),
            "mean_outside_control_envelope_fraction": statistics.fmean(
                outside_values[arm]
            ),
            "bootstrap_ci95": [
                ANALYSIS.percentile(bootstrap_arm[arm], 0.025),
                ANALYSIS.percentile(bootstrap_arm[arm], 0.975),
            ],
            "bootstrap_probability_best": (
                best_probability[arm] / bootstrap_iterations
            ),
        }

    comparisons = []
    for first, second in arm_pairs:
        differences = [
            left - right for left, right in zip(values[first], values[second])
        ]
        comparison = {
            "first": first,
            "second": second,
            "mean_distance_difference": statistics.fmean(differences),
            "interpretation": "negative means first is closer to Firefox controls",
            "bootstrap_ci95": [
                ANALYSIS.percentile(bootstrap_pair[(first, second)], 0.025),
                ANALYSIS.percentile(bootstrap_pair[(first, second)], 0.975),
            ],
            "permutation_test": paired_sign_flip(
                differences,
                permutations,
                seed + ANALYSIS.stable_offset(f"{first}:{second}", 100_003),
            ),
            "holm_adjusted_p_value": None,
        }
        comparisons.append(comparison)
    return {
        "available": True,
        "features": len(feature_names),
        "distance": (
            "mean featurewise excess outside the matched Firefox A/B envelope, "
            "normalized by the Firefox-only 75th-percentile paired radius and "
            "bounded to [0,1]"
        ),
        "ranking": ranked_arms(means, selected_arms),
        "arms": arms,
        "paired_comparisons": comparisons,
        "control_calibration": distances["control_calibration"],
        "mechanism_diagnostics": mechanism_diagnostics(
            blocks, feature_names, selected_arms
        ),
    }


def apply_holm_correction(report):
    tests = []
    for protocol in report["protocols"].values():
        if not protocol["inference"]["supports_paired_inference"]:
            continue
        for view in protocol["views"].values():
            if not view.get("available"):
                continue
            for comparison in view["paired_comparisons"]:
                test = comparison["permutation_test"]
                if test.get("available"):
                    tests.append((test["p_value"], comparison))
    ordered = sorted(tests, key=lambda item: item[0])
    running = 0.0
    count = len(ordered)
    for index, (p_value, comparison) in enumerate(ordered):
        adjusted = min(1.0, p_value * (count - index))
        running = max(running, adjusted)
        comparison["holm_adjusted_p_value"] = running
    return count


def parse_views(value):
    views = tuple(item.strip() for item in value.split(",") if item.strip())
    if not views:
        raise ValueError("at least one feature view is required")
    if len(set(views)) != len(views):
        raise ValueError("feature view list contains duplicates")
    invalid = sorted(set(views) - set(ANALYSIS.VIEWS))
    if invalid:
        raise ValueError(f"unknown feature views: {invalid}")
    return views


def build_report(args, rows, all_feature_names):
    arms = SUPERBLOCKS.infer_arms(rows)
    blocks = group_superblocks(rows, arms)
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": args.mode,
        "seed": args.seed,
        "screening_only": True,
        "arms": list(arms),
        "views_selected": list(args.views),
        "methodology": {
            "design": (
                "within-experiment_block paired comparison against the same "
                "contemporaneous Firefox A/B controls"
            ),
            "model_training": False,
            "feature_selection": False,
            "arm_labels_used_as_features": False,
            "control_calibration": (
                "Firefox controls only; NaiveFox arms never set feature scales"
            ),
            "bootstrap": (
                "workload-stratified paired block bootstrap conditional on the "
                "observed Firefox-only feature scales"
            ),
            "permutation": (
                "two-sided paired arm-label sign flip within experiment blocks"
            ),
            "multiplicity": (
                "Holm family-wise correction across every eligible protocol, "
                "feature view, and arm pair in this report"
            ),
            "bootstrap_iterations": args.bootstrap,
            "permutation_iterations": args.permutations,
            "normalized_excess_cap": NORMALIZED_EXCESS_CAP,
            "mechanism_diagnostics": (
                "post-hoc passive feature summaries are never used to rank arms, "
                "fit a classifier, or compute inferential tests"
            ),
        },
        "limitations": [
            (
                "This is a relative arm ranking, not an indistinguishability test: "
                "the two Firefox rows define the control envelope, so an independent "
                "third Firefox observation is unavailable as an absolute null."
            ),
            (
                "Engineered feature columns, views, and protocol results are highly "
                "correlated; equal feature weighting is a declared diagnostic choice."
            ),
            (
                "Sign-flip inference assumes randomized arm order removes carryover "
                "and that paired blocks are independent experimental units."
            ),
            (
                "Bootstrap intervals are conditional on observed Firefox-only scales; "
                "small samples do not estimate scale uncertainty reliably."
            ),
            (
                "Top-feature and signed packet-sequence summaries are mechanism "
                "diagnostics, not confirmatory feature selection."
            ),
        ],
        "protocols": {},
    }
    for protocol_name in sorted({block["protocol"] for block in blocks}):
        protocol_blocks = [
            block for block in blocks if block["protocol"] == protocol_name
        ]
        protocol = {
            "inference": inference_status(protocol_blocks, args.mode, args.min_blocks),
            "views": {},
        }
        for view in args.views:
            names = ANALYSIS.view_feature_names(all_feature_names, view)
            protocol["views"][view] = summarize_view(
                protocol_blocks,
                names,
                args.bootstrap,
                args.permutations,
                args.seed
                + ANALYSIS.stable_offset(f"{protocol_name}:{view}", 1_000_003),
                arms=arms,
            )
        report["protocols"][protocol_name] = protocol
    test_count = apply_holm_correction(report)
    report["multiple_testing"] = {
        "family": "all inferentially eligible protocol/view/arm-pair tests",
        "tests": test_count,
        "method": "Holm step-down family-wise error control",
    }
    eligible = [
        data["inference"]["supports_paired_inference"]
        for data in report["protocols"].values()
    ]
    report["conclusion"] = {
        "screening_only": True,
        "supports_relative_arm_inference": bool(eligible) and all(eligible),
        "supports_absolute_camouflage_verdict": False,
        "status": (
            "RELATIVE_RANKING_ONLY"
            if eligible and all(eligible)
            else "INSUFFICIENT_FOR_INFERENCE"
        ),
    }
    return report


def write_summary(path, report):
    lines = [
        "NaiveFox paired multi-arm camouflage analysis",
        f"mode={report['mode']} seed={report['seed']}",
        f"arms={','.join(report['arms'])}",
        f"views={','.join(report['views_selected'])}",
        "screening_only=true",
        f"status={report['conclusion']['status']}",
        "absolute_camouflage_verdict=NOT_SUPPORTED",
        (
            "Lower distance is closer to the same Firefox A/B controls in the "
            "same experiment block."
        ),
        "",
    ]
    for protocol_name, protocol in report["protocols"].items():
        inference = protocol["inference"]
        lines.append(
            f"{protocol_name.upper()} blocks={inference['blocks']} "
            f"paired_inference={str(inference['supports_paired_inference']).lower()}"
        )
        for reason in inference["reasons"]:
            lines.append(f"  insufficient: {reason}")
        for view_name, view in protocol["views"].items():
            if not view.get("available"):
                lines.append(f"{view_name}: unavailable ({view['reason']})")
                continue
            ranking = " < ".join(item["arm"] for item in view["ranking"])
            values = []
            for arm in report["arms"]:
                lower, upper = view["arms"][arm]["bootstrap_ci95"]
                values.append(
                    f"{arm}={view['arms'][arm]['mean_distance']:.5f}"
                    f"[{lower:.5f},{upper:.5f}]"
                )
            lines.append(f"{view_name}: {ranking}; {', '.join(values)}")
            mechanism = view["mechanism_diagnostics"]
            for arm in report["arms"]:
                top = mechanism["top_features_by_arm"][arm][:3]
                if top:
                    lines.append(
                        f"  diagnostic_top[{arm}]="
                        + ", ".join(
                            f"{item['feature']}:"
                            f"{item['mean_signed_delta_from_firefox_midpoint']:+.3f}"
                            for item in top
                        )
                    )
            if view_name in {
                "initial_packets_16",
                "packets_17_32",
                "initial_packets_32",
            }:
                wire = [
                    item
                    for item in mechanism["signed_packet_sequence"]
                    if item["metric"] == "wire_size_signed"
                ]
                if wire:
                    lines.append(
                        "  diagnostic_firefox_ab_mean_abs_wire_delta="
                        + ",".join(
                            f"p{item['packet_index']:03d}:"
                            f"{item['firefox_control_mean_abs_pair_difference']:.3f}"
                            for item in wire
                        )
                    )
                    for arm in report["arms"]:
                        lines.append(
                            f"  diagnostic_{arm}_minus_firefox_wire="
                            + ",".join(
                                f"p{item['packet_index']:03d}:"
                                f"{item['arms'][arm]['mean_signed_delta_from_firefox_midpoint']:+.3f}"
                                for item in wire
                            )
                        )
            if inference["supports_paired_inference"]:
                for comparison in view["paired_comparisons"]:
                    lower, upper = comparison["bootstrap_ci95"]
                    lines.append(
                        "  "
                        f"{comparison['first']}-{comparison['second']}="
                        f"{comparison['mean_distance_difference']:.5f} "
                        f"CI95=[{lower:.5f},{upper:.5f}] "
                        f"Holm-p={comparison['holm_adjusted_p_value']:.4g}"
                    )
        lines.append("")
    lines.extend([
        (
            "Caveat: the report ranks arms but cannot establish absolute "
            "indistinguishability without an independent Firefox null observation."
        ),
        (
            "Caveat: feature views overlap; Holm correction covers reported pairwise "
            "tests, but rankings remain diagnostic and depend on equal feature weighting."
        ),
    ])
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-summary", required=True)
    parser.add_argument(
        "--mode", choices=("gate", "smoke", "standard", "research"), required=True
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--permutations", type=int, default=10000)
    parser.add_argument("--min-blocks", type=int, default=30)
    parser.add_argument(
        "--views",
        default=",".join(ANALYSIS.VIEWS),
        help="comma-separated feature views to include in this screening report",
    )
    args = parser.parse_args()
    if args.bootstrap < 100:
        raise SystemExit("bootstrap iterations must be at least 100")
    if args.permutations < 99:
        raise SystemExit("permutation iterations must be at least 99")
    if args.min_blocks < MIN_PAIRED_BLOCKS:
        raise SystemExit(
            f"minimum blocks must be at least {MIN_PAIRED_BLOCKS}"
        )
    try:
        args.views = parse_views(args.views)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    rows, feature_names = load_dataset(args.features)
    report = build_report(args, rows, feature_names)
    with open(args.output_json, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    write_summary(args.output_summary, report)


if __name__ == "__main__":
    main()
