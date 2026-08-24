#!/usr/bin/env python3

import argparse
import csv
import hashlib
import json
import math
import random
import statistics


SCHEMA_VERSION = 1
METADATA_FIELDS = {"schema_version", "protocol", "scenario", "label", "session_id"}
FEATURE_PREFIXES = (
    "initial_",
    "lifecycle_",
    "packet_",
    "quic_",
    "steady_",
    "tcp_syn_",
    "tls_",
    "whole_",
)
VIEWS = (
    "whole",
    "initial_packets_16",
    "initial_packets_32",
    "initial_packets_64",
    "initial_packets_128",
    "initial_time_250ms",
    "initial_time_500ms",
    "initial_time_1000ms",
    "initial_time_2000ms",
    "steady_after_32",
    "steady_after_2000ms",
    "lifecycle",
)


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


def auc(labels, scores):
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        raise ValueError("AUC requires both classes")
    ordered = sorted(zip(scores, labels), key=lambda item: item[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = ((index + 1) + end) / 2
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def sigmoid(value):
    if value >= 0:
        inverse = math.exp(-min(value, 40.0))
        return 1.0 / (1.0 + inverse)
    exponential = math.exp(max(value, -40.0))
    return exponential / (1.0 + exponential)


def load_dataset(path):
    with open(path, newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise SystemExit("feature dataset has no header")
        missing = METADATA_FIELDS - set(reader.fieldnames)
        if missing:
            raise SystemExit(f"feature dataset lacks metadata fields: {sorted(missing)}")
        feature_names = [name for name in reader.fieldnames if name not in METADATA_FIELDS]
        invalid = [
            name for name in feature_names if not name.startswith(FEATURE_PREFIXES)
        ]
        if invalid:
            raise SystemExit(f"unknown or unsafe feature columns: {invalid[:8]}")
        rows = []
        for source in reader:
            if source["schema_version"] != str(SCHEMA_VERSION):
                raise SystemExit("unsupported camouflage feature schema")
            if source["protocol"] not in {"h2", "h3"}:
                raise SystemExit("invalid protocol metadata")
            if source["label"] not in {"firefox_a", "firefox_b", "naivefox"}:
                raise SystemExit("invalid label metadata")
            values = {}
            for name in feature_names:
                try:
                    value = float(source[name] or 0.0)
                except ValueError as error:
                    raise SystemExit(f"non-numeric feature {name}") from error
                if not math.isfinite(value):
                    raise SystemExit(f"non-finite feature {name}")
                values[name] = value
            rows.append(
                {
                    "protocol": source["protocol"],
                    "scenario": source["scenario"],
                    "label": source["label"],
                    "session_id": source["session_id"],
                    "features": values,
                }
            )
    group_labels = {}
    for row in rows:
        existing = group_labels.setdefault(row["session_id"], row["label"])
        if existing != row["label"]:
            raise SystemExit(f"mixed labels in session {row['session_id']}")
    return rows, feature_names


def view_feature_names(all_names, view):
    handshake = (
        "tls_alpn_",
        "tls_cipher_",
        "tls_client_hello_",
        "tls_extension_",
        "tls_group_",
        "tls_key_share_",
        "tls_server_",
        "tls_signature_",
        "tls_sni_",
        "tls_supported_version_",
        "tcp_syn_",
        "quic_initial_",
        "quic_phase_",
        "quic_retry_",
        "quic_tcp_probe_",
        "quic_transport_",
        "quic_tp_",
        "quic_version_",
    )
    if view == "whole":
        return list(all_names)
    if view.startswith("initial_packets_"):
        count = int(view.rsplit("_", 1)[1])
        selected = []
        for name in all_names:
            if name.startswith(handshake) or name.startswith(f"initial_{count}_"):
                selected.append(name)
                continue
            if name.startswith("packet_"):
                try:
                    index = int(name.split("_", 2)[1])
                except (IndexError, ValueError):
                    continue
                if index <= count:
                    selected.append(name)
            if name.startswith("tls_record_"):
                parts = name.split("_")
                if len(parts) > 2 and parts[2].isdigit() and int(parts[2]) <= count:
                    selected.append(name)
        return selected
    if view.startswith("initial_time_"):
        window = view.removeprefix("initial_time_")
        return [
            name
            for name in all_names
            if name.startswith(handshake) or name.startswith(f"initial_{window}_")
        ]
    if view == "steady_after_32":
        return [name for name in all_names if name.startswith("steady_after_32_")]
    if view == "steady_after_2000ms":
        return [
            name for name in all_names if name.startswith("steady_after_2000ms_")
        ]
    if view == "lifecycle":
        return [name for name in all_names if name.startswith("lifecycle_")]
    raise ValueError(f"unknown feature view: {view}")


def comparison_rows(rows, comparison):
    if comparison == "firefox_baseline":
        selected = [row for row in rows if row["label"] in {"firefox_a", "firefox_b"}]
        labels = [int(row["label"] == "firefox_b") for row in selected]
    elif comparison == "firefox_vs_naivefox":
        selected = list(rows)
        labels = [int(row["label"] == "naivefox") for row in selected]
    else:
        raise ValueError(comparison)
    return selected, labels


def stable_offset(value, modulo):
    digest = hashlib.sha256(value.encode()).digest()
    return int.from_bytes(digest[:4], "big") % modulo


def grouped_folds(rows, labels, requested, seed):
    groups = {}
    for index, (row, label) in enumerate(zip(rows, labels)):
        group = groups.setdefault(
            row["session_id"],
            {"indices": [], "label": label, "scenario": row["scenario"]},
        )
        if group["label"] != label:
            raise ValueError("one session has both classifier labels")
        group["indices"].append(index)
    class_groups = {
        label: [name for name, group in groups.items() if group["label"] == label]
        for label in (0, 1)
    }
    folds = min(requested, len(class_groups[0]), len(class_groups[1]))
    if folds < 2:
        raise ValueError("at least two independent sessions per class are required")
    assigned = [set() for _ in range(folds)]
    scenarios = sorted({group["scenario"] for group in groups.values()})
    for scenario_index, scenario in enumerate(scenarios):
        for label in (0, 1):
            names = sorted(
                name
                for name, group in groups.items()
                if group["label"] == label and group["scenario"] == scenario
            )
            rng = random.Random(
                seed + stable_offset(f"{scenario}:{label}", 1_000_003)
            )
            rng.shuffle(names)
            for occurrence, name in enumerate(names):
                assigned[(scenario_index + occurrence) % folds].add(name)
    result = []
    all_groups = set(groups)
    for test_groups in assigned:
        if not test_groups:
            continue
        train_groups = all_groups - test_groups
        train = [index for name in train_groups for index in groups[name]["indices"]]
        test = [index for name in test_groups for index in groups[name]["indices"]]
        if len({labels[index] for index in train}) < 2 or len(
            {labels[index] for index in test}
        ) < 2:
            raise ValueError("grouped fold lacks one classifier class")
        result.append((train, test))
    if len(result) < 2:
        raise ValueError("grouped split produced fewer than two usable folds")
    return result


def sample_weights(rows, labels, indices):
    per_group = {}
    for index in indices:
        group = rows[index]["session_id"]
        per_group[group] = per_group.get(group, 0) + 1
    base = {index: 1.0 / per_group[rows[index]["session_id"]] for index in indices}
    totals = {
        label: sum(base[index] for index in indices if labels[index] == label)
        for label in (0, 1)
    }
    total = sum(totals.values())
    return {
        index: base[index] * total / (2 * totals[labels[index]]) for index in indices
    }


def weighted_stats(rows, indices, weights, name):
    total = sum(weights[index] for index in indices)
    mean = sum(
        weights[index] * rows[index]["features"].get(name, 0.0) for index in indices
    ) / total
    variance = sum(
        weights[index]
        * (rows[index]["features"].get(name, 0.0) - mean) ** 2
        for index in indices
    ) / total
    return mean, math.sqrt(max(variance, 0.0))


def fit_model(rows, labels, indices, feature_names, max_features, l2, iterations):
    weights = sample_weights(rows, labels, indices)
    candidates = []
    stats = {}
    class_indices = {
        label: [index for index in indices if labels[index] == label] for label in (0, 1)
    }
    for name in feature_names:
        mean, deviation = weighted_stats(rows, indices, weights, name)
        if deviation <= 1e-12:
            continue
        class_means = []
        for label in (0, 1):
            total = sum(weights[index] for index in class_indices[label])
            class_means.append(
                sum(
                    weights[index] * rows[index]["features"].get(name, 0.0)
                    for index in class_indices[label]
                )
                / total
            )
        effect = abs(class_means[1] - class_means[0]) / deviation
        candidates.append((effect, name))
        stats[name] = (mean, deviation)
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = [name for _, name in candidates[:max_features]]
    if not selected:
        return {"features": [], "means": {}, "scales": {}, "weights": [], "bias": 0.0}
    model_weights = [0.0] * len(selected)
    bias = 0.0
    normalization = sum(weights.values())
    for iteration in range(iterations):
        gradient = [0.0] * len(selected)
        bias_gradient = 0.0
        for index in indices:
            values = [
                (rows[index]["features"].get(name, 0.0) - stats[name][0])
                / stats[name][1]
                for name in selected
            ]
            prediction = sigmoid(
                bias + sum(weight * value for weight, value in zip(model_weights, values))
            )
            error = (prediction - labels[index]) * weights[index]
            bias_gradient += error
            for position, value in enumerate(values):
                gradient[position] += error * value
        rate = 0.25 / math.sqrt(1.0 + iteration / 20.0)
        bias -= rate * bias_gradient / normalization
        for position in range(len(model_weights)):
            regularized = gradient[position] / normalization + l2 * model_weights[position]
            model_weights[position] -= rate * regularized
    return {
        "features": selected,
        "means": {name: stats[name][0] for name in selected},
        "scales": {name: stats[name][1] for name in selected},
        "weights": model_weights,
        "bias": bias,
    }


def predict(model, row):
    score = model["bias"]
    for name, weight in zip(model["features"], model["weights"]):
        value = (row["features"].get(name, 0.0) - model["means"][name]) / model[
            "scales"
        ][name]
        score += weight * value
    return sigmoid(score)


def best_threshold(labels, scores):
    candidates = sorted(set(scores))
    if not candidates:
        return 0.5
    best = (-1.0, 0.5)
    for threshold in candidates:
        tp = sum(label and score >= threshold for label, score in zip(labels, scores))
        fn = sum(label and score < threshold for label, score in zip(labels, scores))
        tn = sum(not label and score < threshold for label, score in zip(labels, scores))
        fp = sum(not label and score >= threshold for label, score in zip(labels, scores))
        tpr = tp / (tp + fn) if tp + fn else 0.0
        tnr = tn / (tn + fp) if tn + fp else 0.0
        balanced = (tpr + tnr) / 2
        candidate = (balanced, -abs(threshold - 0.5))
        if candidate > (best[0], -abs(best[1] - 0.5)):
            best = (balanced, threshold)
    return best[1]


def confusion_metrics(labels, predictions):
    tp = sum(label and prediction for label, prediction in zip(labels, predictions))
    fn = sum(label and not prediction for label, prediction in zip(labels, predictions))
    tn = sum(not label and not prediction for label, prediction in zip(labels, predictions))
    fp = sum(not label and prediction for label, prediction in zip(labels, predictions))
    accuracy = (tp + tn) / len(labels)
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    return {
        "accuracy": accuracy,
        "balanced_accuracy": (recall + specificity) / 2,
        "precision": precision,
        "recall": recall,
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
    }


def clustered_bootstrap(rows, labels, scores, iterations, seed):
    group_rows = {}
    for index, row in enumerate(rows):
        group_rows.setdefault(row["session_id"], []).append(index)
    class_groups = {
        label: [
            group
            for group, indices in group_rows.items()
            if labels[indices[0]] == label
        ]
        for label in (0, 1)
    }
    rng = random.Random(seed)
    auc_values = []
    d_values = []
    for _ in range(iterations):
        sampled = []
        for label in (0, 1):
            sampled.extend(
                rng.choice(class_groups[label]) for _ in range(len(class_groups[label]))
            )
        boot_labels = []
        boot_scores = []
        for group in sampled:
            for index in group_rows[group]:
                boot_labels.append(labels[index])
                boot_scores.append(scores[index])
        value = auc(boot_labels, boot_scores)
        auc_values.append(value)
        d_values.append(max(value, 1 - value))
    return {
        "iterations": iterations,
        "auc_ci95": [percentile(auc_values, 0.025), percentile(auc_values, 0.975)],
        "distinguishability_ci95": [
            percentile(d_values, 0.025),
            percentile(d_values, 0.975),
        ],
        "method": "grouped bootstrap of pooled out-of-fold predictions",
    }


def permutation_test(rows, labels, scores, iterations, seed):
    if not iterations:
        return None
    groups = {}
    for index, row in enumerate(rows):
        groups.setdefault(row["session_id"], []).append(index)
    strata = {}
    for group, indices in groups.items():
        strata.setdefault(rows[indices[0]]["scenario"], []).append(group)
    observed_auc = auc(labels, scores)
    observed = max(observed_auc, 1 - observed_auc)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(iterations):
        permuted = list(labels)
        for group_names in strata.values():
            group_labels = [labels[groups[group][0]] for group in group_names]
            rng.shuffle(group_labels)
            for group, label in zip(group_names, group_labels):
                for index in groups[group]:
                    permuted[index] = label
        value = auc(permuted, scores)
        if max(value, 1 - value) >= observed:
            extreme += 1
    return {
        "iterations": iterations,
        "p_value": (extreme + 1) / (iterations + 1),
        "method": "group-label permutation within workload on fixed out-of-fold scores",
    }


def analyze_comparison(
    rows,
    labels,
    feature_names,
    folds,
    seed,
    max_features,
    l2,
    iterations,
    bootstrap_iterations,
    permutation_iterations,
):
    try:
        split = grouped_folds(rows, labels, folds, seed)
    except ValueError as error:
        return {"available": False, "reason": str(error)}
    scores = [0.0] * len(rows)
    hard_predictions = [False] * len(rows)
    coefficients = {}
    thresholds = []
    for train, test in split:
        model = fit_model(
            rows, labels, train, feature_names, max_features, l2, iterations
        )
        train_scores = [predict(model, rows[index]) for index in train]
        threshold = best_threshold([labels[index] for index in train], train_scores)
        thresholds.append(threshold)
        for index in test:
            scores[index] = predict(model, rows[index])
            hard_predictions[index] = scores[index] >= threshold
        for name, weight in zip(model["features"], model["weights"]):
            coefficients.setdefault(name, []).append(weight)
    auc_value = auc(labels, scores)
    distinguishability = max(auc_value, 1 - auc_value)
    metrics = confusion_metrics(labels, hard_predictions)
    importance = []
    for name, values in coefficients.items():
        padded = values + [0.0] * (len(split) - len(values))
        importance.append(
            {
                "feature": name,
                "mean_abs_standardized_coefficient": statistics.fmean(
                    abs(value) for value in padded
                ),
                "mean_signed_standardized_coefficient": statistics.fmean(padded),
                "selection_count": len(values),
            }
        )
    importance.sort(
        key=lambda item: (-item["mean_abs_standardized_coefficient"], item["feature"])
    )
    result = {
        "available": True,
        "samples": {"negative": labels.count(0), "positive": labels.count(1)},
        "groups": len({row["session_id"] for row in rows}),
        "folds": len(split),
        "features_considered": len(feature_names),
        "max_features_per_fold": max_features,
        "auc": auc_value,
        "distinguishability": distinguishability,
        "train_selected_threshold_median": statistics.median(thresholds),
        **metrics,
        **clustered_bootstrap(
            rows, labels, scores, bootstrap_iterations, seed + 104729
        ),
        "permutation_test": permutation_test(
            rows, labels, scores, permutation_iterations, seed + 130363
        ),
        "top_features": importance[:12],
    }
    return result


def classify(target, baseline, mode):
    if not target.get("available") or not baseline.get("available"):
        return "inconclusive"
    if mode != "research":
        return "inconclusive"
    value = target["distinguishability"]
    lower, upper = target["distinguishability_ci95"]
    delta = value - baseline["distinguishability"]
    if value >= 0.70 and lower >= 0.60:
        return "red"
    if value <= 0.60 and delta <= 0.05 and upper <= 0.65 and upper - lower <= 0.12:
        return "green"
    return "yellow"


def cross_workload(rows, feature_names, seed, max_features, l2, iterations):
    selected, labels = comparison_rows(rows, "firefox_vs_naivefox")
    scenarios = sorted({row["scenario"] for row in selected})
    results = {}
    for scenario in scenarios:
        train = [index for index, row in enumerate(selected) if row["scenario"] != scenario]
        test = [index for index, row in enumerate(selected) if row["scenario"] == scenario]
        if len({labels[index] for index in train}) < 2 or len(
            {labels[index] for index in test}
        ) < 2:
            continue
        if min(sum(labels[index] == label for index in test) for label in (0, 1)) < 2:
            continue
        model = fit_model(
            selected, labels, train, feature_names, max_features, l2, iterations
        )
        test_labels = [labels[index] for index in test]
        test_scores = [predict(model, selected[index]) for index in test]
        value = auc(test_labels, test_scores)
        results[scenario] = {
            "auc": value,
            "distinguishability": max(value, 1 - value),
            "samples": len(test),
        }
    return {
        "holdouts": results,
        "macro_distinguishability": (
            statistics.fmean(item["distinguishability"] for item in results.values())
            if results
            else None
        ),
    }


def passive_handshake_differences(rows, feature_names):
    names = [
        name
        for name in feature_names
        if name.startswith(("tls_", "tcp_syn_", "quic_initial_", "quic_tp_", "quic_version_"))
    ]
    firefox = [row for row in rows if row["label"].startswith("firefox_")]
    naivefox = [row for row in rows if row["label"] == "naivefox"]
    differences = []
    for name in names:
        left = statistics.fmean(row["features"].get(name, 0.0) for row in firefox)
        right = statistics.fmean(row["features"].get(name, 0.0) for row in naivefox)
        if abs(left - right) > 1e-12:
            differences.append({"feature": name, "firefox_mean": left, "naivefox_mean": right})
    differences.sort(key=lambda item: (-abs(item["firefox_mean"] - item["naivefox_mean"]), item["feature"]))
    return {
        "scope": "passively visible handshake fields only; not decrypted H2/H3 parity",
        "different_feature_count": len(differences),
        "top_differences": differences[:20],
    }


def build_report(args, rows, all_feature_names):
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": args.mode,
        "seed": args.seed,
        "methodology": {
            "classifier": "dependency-free L2 logistic regression",
            "split": "stratified grouped cross-validation by session_id",
            "preprocessing": "train-only standardization and feature screening",
            "metric": "D=max(ROC_AUC,1-ROC_AUC)",
            "bootstrap_iterations": args.bootstrap,
            "permutation_iterations": args.permutations,
            "labels_excluded_from_features": True,
            "decrypted_features_used": False,
        },
        "protocols": {},
    }
    for protocol in sorted({row["protocol"] for row in rows}):
        protocol_rows = [row for row in rows if row["protocol"] == protocol]
        protocol_report = {
            "samples": {
                label: sum(row["label"] == label for row in protocol_rows)
                for label in ("firefox_a", "firefox_b", "naivefox")
            },
            "views": {},
            "workloads": {},
            "cross_workload": cross_workload(
                protocol_rows,
                view_feature_names(all_feature_names, "whole"),
                args.seed + stable_offset(protocol, 10000),
                args.max_features,
                args.l2,
                args.iterations,
            ),
            "passive_handshake_differences": passive_handshake_differences(
                protocol_rows, all_feature_names
            ),
            "wire_parity": {
                "status": "not_measured_by_passive_classifier",
                "instruction": "use the same-base decrypted capture diagnostics",
            },
        }
        for view in VIEWS:
            names = view_feature_names(all_feature_names, view)
            view_report = {}
            for comparison in ("firefox_baseline", "firefox_vs_naivefox"):
                selected, labels = comparison_rows(protocol_rows, comparison)
                view_report[comparison] = analyze_comparison(
                    selected,
                    labels,
                    names,
                    args.folds,
                    args.seed + stable_offset(f"{protocol}:{view}:{comparison}", 100000),
                    args.max_features,
                    args.l2,
                    args.iterations,
                    args.bootstrap,
                    args.permutations,
                )
            view_report["classification"] = classify(
                view_report["firefox_vs_naivefox"],
                view_report["firefox_baseline"],
                args.mode,
            )
            if view_report["firefox_baseline"].get("available") and view_report[
                "firefox_vs_naivefox"
            ].get("available"):
                view_report["baseline_delta"] = (
                    view_report["firefox_vs_naivefox"]["distinguishability"]
                    - view_report["firefox_baseline"]["distinguishability"]
                )
            protocol_report["views"][view] = view_report
        for scenario in sorted({row["scenario"] for row in protocol_rows}):
            workload_rows = [row for row in protocol_rows if row["scenario"] == scenario]
            workload_report = {}
            for comparison in ("firefox_baseline", "firefox_vs_naivefox"):
                selected, labels = comparison_rows(workload_rows, comparison)
                workload_report[comparison] = analyze_comparison(
                    selected,
                    labels,
                    view_feature_names(all_feature_names, "whole"),
                    args.folds,
                    args.seed + stable_offset(f"{protocol}:{scenario}:{comparison}", 100000),
                    args.max_features,
                    args.l2,
                    args.iterations,
                    args.bootstrap,
                    0,
                )
            workload_report["classification"] = classify(
                workload_report["firefox_vs_naivefox"],
                workload_report["firefox_baseline"],
                args.mode,
            )
            protocol_report["workloads"][scenario] = workload_report
        report["protocols"][protocol] = protocol_report
    primary_views = ("initial_packets_32", "steady_after_32", "lifecycle")
    red = []
    green = []
    for protocol, protocol_report in report["protocols"].items():
        for view in primary_views:
            classification = protocol_report["views"][view]["classification"]
            red.append(classification == "red")
            green.append(classification == "green")
    if args.mode == "research" and any(red):
        conclusion = "YES"
    elif args.mode == "research" and green and all(green):
        conclusion = "NO_SIGNAL_FOUND"
    else:
        conclusion = "INCONCLUSIVE"
    report["conclusion"] = {
        "naivefox_distinguishable_by_selected_classifiers": conclusion,
        "qualification": (
            "The suite tests selected externally observable features; it does not prove "
            "mathematical indistinguishability."
        ),
    }
    return report


def format_metric(result):
    if not result.get("available"):
        return f"unavailable ({result.get('reason', 'insufficient data')})"
    lower, upper = result["distinguishability_ci95"]
    return (
        f"D={result['distinguishability']:.3f} "
        f"AUC={result['auc']:.3f} CI95=[{lower:.3f},{upper:.3f}]"
    )


def write_summary(path, report):
    lines = [
        "NaiveFox passive camouflage experiment",
        f"mode={report['mode']} seed={report['seed']}",
        (
            "Interpretation: selected classifiers attempt to distinguish cohorts using "
            "externally observable wire features."
        ),
        "",
    ]
    for protocol, data in report["protocols"].items():
        lines.append(protocol.upper())
        lines.append(
            "samples=" + ", ".join(f"{name}:{count}" for name, count in data["samples"].items())
        )
        for view in (
            "whole",
            "initial_packets_16",
            "initial_packets_32",
            "initial_packets_64",
            "initial_time_250ms",
            "initial_time_500ms",
            "initial_time_1000ms",
            "initial_time_2000ms",
            "steady_after_32",
            "steady_after_2000ms",
            "lifecycle",
        ):
            item = data["views"][view]
            lines.append(f"{view} Firefox-vs-Firefox: {format_metric(item['firefox_baseline'])}")
            lines.append(
                f"{view} Firefox-vs-NaiveFox: "
                f"{format_metric(item['firefox_vs_naivefox'])} {item['classification'].upper()}"
            )
            top = item["firefox_vs_naivefox"].get("top_features", [])[:3]
            if top:
                lines.append(
                    "  top=" + ", ".join(feature["feature"] for feature in top)
                )
        cross = data["cross_workload"]["macro_distinguishability"]
        holdouts = data["cross_workload"]["holdouts"]
        lines.append(
            "cross_workload_holdouts="
            + str(len(holdouts))
            + " samples="
            + str(sum(item["samples"] for item in holdouts.values()))
        )
        lines.append(
            "cross_workload_macro_D=" + ("unavailable" if cross is None else f"{cross:.3f}")
        )
        lines.append("same_base_decrypted_parity=not_run_by_passive_suite")
        lines.append("")
    lines.append(
        "conclusion="
        + report["conclusion"]["naivefox_distinguishable_by_selected_classifiers"]
    )
    lines.append(report["conclusion"]["qualification"])
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-summary", required=True)
    parser.add_argument("--mode", choices=("gate", "smoke", "standard", "research"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--permutations", type=int, default=0)
    parser.add_argument("--max-features", type=int, default=64)
    parser.add_argument("--l2", type=float, default=0.05)
    parser.add_argument("--iterations", type=int, default=180)
    args = parser.parse_args()
    if args.bootstrap < 100:
        raise SystemExit("bootstrap iterations must be at least 100")
    rows, feature_names = load_dataset(args.features)
    report = build_report(args, rows, feature_names)
    with open(args.output_json, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    write_summary(args.output_summary, report)


if __name__ == "__main__":
    main()
