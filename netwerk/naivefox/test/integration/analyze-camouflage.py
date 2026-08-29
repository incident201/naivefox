#!/usr/bin/env python3

import argparse
import csv
import hashlib
import json
import math
import random
import statistics

SCHEMA_VERSION = 1
MIN_RESEARCH_SAMPLES_PER_COHORT = 240
COHORT_LABELS = ("firefox_a", "firefox_b", "naivefox")
REQUIRED_METADATA_FIELDS = {
    "schema_version",
    "protocol",
    "scenario",
    "label",
    "session_id",
}
OPTIONAL_METADATA_FIELDS = {"experiment_block", "naivefox_arm"}
METADATA_FIELDS = REQUIRED_METADATA_FIELDS | OPTIONAL_METADATA_FIELDS
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
FORBIDDEN_FEATURE_TERMS = (
    "absolute_timestamp",
    "authority",
    "canary",
    "cohort",
    "credential",
    "destination_port",
    "decrypted",
    "experiment_block",
    "filename",
    "header",
    "label",
    "method",
    "naivefox_arm",
    "password",
    "path",
    "plaintext",
    "process_duration",
    "profile",
    "query",
    "session_id",
    "source_port",
    "status",
    "stream_id",
)
VIEWS = (
    "whole",
    "initial_packets_16",
    "packets_17_32",
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
PRIMARY_VIEWS = ("initial_packets_32", "steady_after_32", "lifecycle")
PHASE_COUNT_FEATURES = (
    "initial_16_packet_count",
    "initial_32_packet_count",
    "initial_64_packet_count",
    "initial_128_packet_count",
    "initial_50ms_packet_count",
    "initial_100ms_packet_count",
    "initial_250ms_packet_count",
    "initial_500ms_packet_count",
    "initial_1000ms_packet_count",
    "initial_2000ms_packet_count",
    "steady_after_32_packet_count",
    "steady_after_2000ms_packet_count",
    "lifecycle_tail_16_packet_count",
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
        ordered[lower] * (upper - position) + ordered[upper] * (position - lower)
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
        missing = REQUIRED_METADATA_FIELDS - set(reader.fieldnames)
        if missing:
            raise SystemExit(
                f"feature dataset lacks metadata fields: {sorted(missing)}"
            )
        feature_names = [
            name for name in reader.fieldnames if name not in METADATA_FIELDS
        ]
        invalid = [
            name
            for name in feature_names
            if not name.startswith(FEATURE_PREFIXES)
            or any(term in name for term in FORBIDDEN_FEATURE_TERMS)
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
            arm = source.get("naivefox_arm") or None
            naivefox_arms = {
                "firefox-proxied",
                "off",
                "gate",
                "root",
                "root-pmtud-control",
                "document-complete",
                "document-carrier-dispatch",
                "document-cold-winner-handoff",
                "document-native-cache-open",
                "document-handshake-confirmed",
                "document-first-buffer-overlap",
                "document-first-buffer-task-overlap",
                "document-first-buffer-task-http-connect",
                "document-first-buffer-http-connect",
                "document-overlap",
                "document-headers-task-overlap",
                "document-headers-task-http-connect",
                "document-overlap-http-connect",
                "document-start-http-connect",
                "document-start-overlap",
                "document-start-task-overlap",
                "document-start-task-http-connect",
                "tree-complete",
                "tree-complete-css",
                "tree-complete-resource-tree",
                "tree-early-overlap",
                "tree-early-overlap-resource-tree",
                "tree-root-overlap",
                "tree-root-overlap-css",
                "tree-resource-committed-overlap-css",
                "tree-resource-committed-overlap-tree",
                "tree-resource-committed-overlap-page",
                "tree-resource-native-cache-committed-overlap",
                "tree-native-parser-preload-overlap-css",
                "tree-native-parser-document-start-overlap-css",
                "tree-native-parser-document-start-resource-tree",
                "tree-native-parser-resource-committed-tree",
                "tree-native-parser-resource-committed-page",
                "tree-native-parser-resource-committed-page-http-connect",
                "tree-native-parser-document-start-navigation-stop-css",
                "tree-native-parser-document-start-response-stop-css",
                "tree-native-parser-document-handoff-overlap-css",
                "tree-native-parser-retarget-overlap-css",
                "tree-native-parser-ipc-rendezvous-overlap-css",
                "tree-native-parser-root-rendezvous-overlap-css",
                "tree-native-parser-process-overlap-css",
                "tree-native-parser-full-process-overlap-css",
                "tree-warm-css-304",
                "tree-overlap",
            }
            if arm not in {None, "reference", *naivefox_arms}:
                raise SystemExit("invalid NaiveFox arm metadata")
            if arm == "firefox-proxied" and source["protocol"] != "h2":
                raise SystemExit("firefox-proxied analysis requires h2")
            if arm == "root-pmtud-control" and source["protocol"] != "h3":
                raise SystemExit("root-pmtud-control requires h3")
            if arm == "document-handshake-confirmed" and source["protocol"] != "h3":
                raise SystemExit("document-handshake-confirmed requires h3")
            if arm == "document-carrier-dispatch" and source["protocol"] != "h3":
                raise SystemExit("document-carrier-dispatch requires h3")
            if arm == "document-cold-winner-handoff" and source["protocol"] != "h3":
                raise SystemExit("document-cold-winner-handoff requires h3")
            if arm == "document-native-cache-open" and source["protocol"] != "h3":
                raise SystemExit("document-native-cache-open requires h3")
            if (
                arm
                in (
                    "tree-resource-committed-overlap-css",
                    "tree-resource-committed-overlap-tree",
                    "tree-resource-committed-overlap-page",
                    "tree-native-parser-resource-committed-tree",
                    "tree-native-parser-resource-committed-page",
                    "tree-native-parser-resource-committed-page-http-connect",
                    "tree-complete-resource-tree",
                    "tree-early-overlap-resource-tree",
                )
                and source["protocol"] != "h3"
            ):
                raise SystemExit(f"{arm} requires h3")
            if (
                arm == "tree-resource-native-cache-committed-overlap"
                and source["protocol"] != "h3"
            ):
                raise SystemExit(
                    "tree-resource-native-cache-committed-overlap requires h3"
                )
            if (
                arm
                in (
                    "tree-native-parser-preload-overlap-css",
                    "tree-native-parser-document-start-response-stop-css",
                    "tree-native-parser-document-handoff-overlap-css",
                    "tree-native-parser-retarget-overlap-css",
                    "tree-native-parser-ipc-rendezvous-overlap-css",
                    "tree-native-parser-root-rendezvous-overlap-css",
                    "tree-native-parser-process-overlap-css",
                    "tree-native-parser-full-process-overlap-css",
                )
                and source["protocol"] != "h3"
            ):
                raise SystemExit(f"{arm} requires h3")
            if arm == "reference" and source["label"] == "naivefox":
                raise SystemExit("NaiveFox row cannot use reference arm metadata")
            if arm in naivefox_arms and source["label"] != "naivefox":
                raise SystemExit("Firefox row cannot use NaiveFox arm metadata")
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
                "session_id": source["session_id"],
                "experiment_block": source.get("experiment_block") or None,
                "naivefox_arm": arm,
                "features": values,
            })
    group_labels = {}
    for row in rows:
        existing = group_labels.setdefault(row["session_id"], row["label"])
        if existing != row["label"]:
            raise SystemExit(f"mixed labels in session {row['session_id']}")
    block_members = {}
    for row in rows:
        block = row["experiment_block"]
        if not block:
            continue
        key = (row["protocol"], block)
        block_members.setdefault(key, []).append(row)
    if block_members and any(not row["experiment_block"] for row in rows):
        raise SystemExit("experiment block metadata is missing from some rows")
    expected_labels = ["firefox_a", "firefox_b", "naivefox"]
    for (protocol, block), members in block_members.items():
        scenarios = {row["scenario"] for row in members}
        labels = sorted(row["label"] for row in members)
        if len(scenarios) != 1:
            raise SystemExit(
                f"experiment block {block} spans multiple scenarios for {protocol}"
            )
        if labels != expected_labels:
            raise SystemExit(
                f"experiment block {block} has incomplete cohorts for {protocol}: "
                f"{labels}"
            )
    for count_name in PHASE_COUNT_FEATURES:
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


def analysis_group(row):
    if row.get("_analysis_group"):
        return row["_analysis_group"]
    block = row.get("experiment_block")
    return f"block:{block}" if block else f"session:{row['session_id']}"


def sample_weight_group(row):
    return row.get("_weight_group") or analysis_group(row)


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
    if view == "packets_17_32":
        selected = []
        for name in all_names:
            if name.startswith("packet_"):
                try:
                    index = int(name.split("_", 2)[1])
                except (IndexError, ValueError):
                    continue
                if 17 <= index <= 32:
                    selected.append(name)
            elif name.startswith("tls_record_"):
                parts = name.split("_")
                if len(parts) > 2 and parts[2].isdigit():
                    if 17 <= int(parts[2]) <= 32:
                        selected.append(name)
        return selected
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
        return [name for name in all_names if name.startswith("steady_after_2000ms_")]
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
            analysis_group(row),
            {"indices": [], "labels": set(), "scenario": row["scenario"]},
        )
        if group["scenario"] != row["scenario"]:
            raise ValueError("one analysis group spans multiple scenarios")
        group["indices"].append(index)
        group["labels"].add(label)
    class_groups = {
        label: [name for name, group in groups.items() if label in group["labels"]]
        for label in (0, 1)
    }
    folds = min(requested, len(class_groups[0]), len(class_groups[1]))
    if folds < 2:
        raise ValueError("at least two independent sessions per class are required")
    assigned = [set() for _ in range(folds)]
    scenarios = sorted({group["scenario"] for group in groups.values()})
    single_label_groups = all(len(group["labels"]) == 1 for group in groups.values())
    for scenario_index, scenario in enumerate(scenarios):
        if single_label_groups:
            partitions = [
                sorted(
                    name
                    for name, group in groups.items()
                    if group["labels"] == {label} and group["scenario"] == scenario
                )
                for label in (0, 1)
            ]
        else:
            partitions = [
                sorted(
                    name
                    for name, group in groups.items()
                    if group["scenario"] == scenario
                )
            ]
        for partition_index, names in enumerate(partitions):
            rng = random.Random(
                seed
                + stable_offset(
                    f"{scenario}:{partition_index}:{len(partitions)}", 1_000_003
                )
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
        if (
            len({labels[index] for index in train}) < 2
            or len({labels[index] for index in test}) < 2
        ):
            raise ValueError("grouped fold lacks one classifier class")
        result.append((train, test))
    if len(result) < 2:
        raise ValueError("grouped split produced fewer than two usable folds")
    return result


def sample_weights(rows, labels, indices):
    per_group = {}
    for index in indices:
        group = sample_weight_group(rows[index])
        per_group[group] = per_group.get(group, 0) + 1
    base = {
        index: 1.0 / per_group[sample_weight_group(rows[index])] for index in indices
    }
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
    mean = (
        sum(
            weights[index] * rows[index]["features"].get(name, 0.0) for index in indices
        )
        / total
    )
    variance = (
        sum(
            weights[index] * (rows[index]["features"].get(name, 0.0) - mean) ** 2
            for index in indices
        )
        / total
    )
    return mean, math.sqrt(max(variance, 0.0))


def fit_model(rows, labels, indices, feature_names, max_features, l2, iterations):
    weights = sample_weights(rows, labels, indices)
    candidates = []
    stats = {}
    class_indices = {
        label: [index for index in indices if labels[index] == label]
        for label in (0, 1)
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
    standardized = {
        index: [
            (rows[index]["features"].get(name, 0.0) - stats[name][0]) / stats[name][1]
            for name in selected
        ]
        for index in indices
    }
    for iteration in range(iterations):
        gradient = [0.0] * len(selected)
        bias_gradient = 0.0
        for index in indices:
            values = standardized[index]
            prediction = sigmoid(
                bias
                + sum(weight * value for weight, value in zip(model_weights, values))
            )
            error = (prediction - labels[index]) * weights[index]
            bias_gradient += error
            for position, value in enumerate(values):
                gradient[position] += error * value
        rate = 0.25 / math.sqrt(1.0 + iteration / 20.0)
        bias -= rate * bias_gradient / normalization
        for position in range(len(model_weights)):
            regularized = (
                gradient[position] / normalization + l2 * model_weights[position]
            )
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
        tn = sum(
            not label and score < threshold for label, score in zip(labels, scores)
        )
        fp = sum(
            not label and score >= threshold for label, score in zip(labels, scores)
        )
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
    tn = sum(
        not label and not prediction for label, prediction in zip(labels, predictions)
    )
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


def fold_auc_metrics(labels, scores, fold_ids):
    fold_results = []
    for fold_id in sorted(set(fold_ids)):
        indices = [index for index, value in enumerate(fold_ids) if value == fold_id]
        fold_labels = [labels[index] for index in indices]
        fold_scores = [scores[index] for index in indices]
        positives = sum(fold_labels)
        negatives = len(fold_labels) - positives
        if not positives or not negatives:
            raise ValueError("outer fold AUC requires both classes")
        fold_results.append({
            "fold": fold_id,
            "auc": auc(fold_labels, fold_scores),
            "positive": positives,
            "negative": negatives,
            "pairs": positives * negatives,
        })
    pair_count = sum(item["pairs"] for item in fold_results)
    value = sum(item["auc"] * item["pairs"] for item in fold_results) / pair_count
    return {
        "auc": value,
        "macro_auc": statistics.fmean(item["auc"] for item in fold_results),
        "fold_auc": fold_results,
        "comparable_pairs": pair_count,
    }


def fit_cross_validated(
    rows,
    labels,
    feature_names,
    folds,
    seed,
    max_features,
    l2,
    iterations,
    diagnostics,
):
    split = grouped_folds(rows, labels, folds, seed)
    scores = [0.0] * len(rows)
    hard_predictions = [False] * len(rows)
    fold_ids = [-1] * len(rows)
    coefficients = {}
    thresholds = []
    for fold_id, (train, test) in enumerate(split):
        model = fit_model(
            rows, labels, train, feature_names, max_features, l2, iterations
        )
        if diagnostics:
            train_scores = [predict(model, rows[index]) for index in train]
            threshold = best_threshold([labels[index] for index in train], train_scores)
            thresholds.append(threshold)
        for index in test:
            scores[index] = predict(model, rows[index])
            fold_ids[index] = fold_id
            if diagnostics:
                hard_predictions[index] = scores[index] >= threshold
        if diagnostics:
            for name, weight in zip(model["features"], model["weights"]):
                coefficients.setdefault(name, []).append(weight)
    if any(fold_id < 0 for fold_id in fold_ids):
        raise ValueError("grouped split did not produce one prediction per row")
    return {
        "split": split,
        "scores": scores,
        "hard_predictions": hard_predictions,
        "fold_ids": fold_ids,
        "coefficients": coefficients,
        "thresholds": thresholds,
    }


def clustered_bootstrap(rows, labels, scores, fold_ids, iterations, seed):
    fold_groups = {}
    for index, (row, fold_id) in enumerate(zip(rows, fold_ids)):
        fold_groups.setdefault(fold_id, {}).setdefault(analysis_group(row), []).append(
            index
        )
    rng = random.Random(seed)
    auc_values = []
    d_values = []
    for _ in range(iterations):
        fold_values = []
        fold_pairs = []
        for groups in fold_groups.values():
            strata = {}
            for group, indices in groups.items():
                signature = tuple(sorted({labels[index] for index in indices}))
                strata.setdefault(signature, []).append(group)
            sampled_indices = []
            for group_names in strata.values():
                for _ in group_names:
                    sampled_indices.extend(groups[rng.choice(group_names)])
            sampled_labels = [labels[index] for index in sampled_indices]
            sampled_scores = [scores[index] for index in sampled_indices]
            positives = sum(sampled_labels)
            negatives = len(sampled_labels) - positives
            fold_values.append(auc(sampled_labels, sampled_scores))
            fold_pairs.append(positives * negatives)
        value = sum(
            fold_value * pair_count
            for fold_value, pair_count in zip(fold_values, fold_pairs)
        ) / sum(fold_pairs)
        auc_values.append(value)
        d_values.append(max(value, 1 - value))
    return {
        "iterations": iterations,
        "auc_ci95": [percentile(auc_values, 0.025), percentile(auc_values, 0.975)],
        "distinguishability_ci95": [
            percentile(d_values, 0.025),
            percentile(d_values, 0.975),
        ],
        "method": (
            "conditional clustered bootstrap of held-out groups within outer "
            "folds; outer models remain fixed"
        ),
    }


def refit_clustered_bootstrap(
    rows,
    labels,
    feature_names,
    folds,
    seed,
    max_features,
    l2,
    fit_iterations,
    bootstrap_iterations,
):
    groups = {}
    for index, row in enumerate(rows):
        group = groups.setdefault(
            analysis_group(row),
            {"indices": [], "scenario": row["scenario"]},
        )
        if group["scenario"] != row["scenario"]:
            raise ValueError("one bootstrap group spans multiple scenarios")
        group["indices"].append(index)
    strata = {}
    for name, group in groups.items():
        signature = tuple(sorted({labels[index] for index in group["indices"]}))
        strata.setdefault((group["scenario"], signature), []).append(name)
    rng = random.Random(seed)
    auc_values = []
    attempts = 0
    while len(auc_values) < bootstrap_iterations and attempts < max(
        bootstrap_iterations * 5, 20
    ):
        attempts += 1
        sampled_rows = []
        sampled_labels = []
        occurrence = 0
        for group_names in strata.values():
            for _ in group_names:
                selected_name = rng.choice(group_names)
                synthetic_group = f"refit-bootstrap-source:{selected_name}"
                weight_group = f"refit-bootstrap-occurrence:{occurrence}"
                occurrence += 1
                for index in groups[selected_name]["indices"]:
                    row = dict(rows[index])
                    row["_analysis_group"] = synthetic_group
                    row["_weight_group"] = weight_group
                    sampled_rows.append(row)
                    sampled_labels.append(labels[index])
        try:
            fitted = fit_cross_validated(
                sampled_rows,
                sampled_labels,
                feature_names,
                folds,
                rng.randrange(2**31),
                max_features,
                l2,
                fit_iterations,
                False,
            )
            value = fold_auc_metrics(
                sampled_labels, fitted["scores"], fitted["fold_ids"]
            )["auc"]
        except ValueError:
            continue
        auc_values.append(value)
    if len(auc_values) < bootstrap_iterations:
        raise ValueError("refit bootstrap produced too few valid grouped splits")
    d_values = [max(value, 1 - value) for value in auc_values]
    return {
        "iterations": len(auc_values),
        "auc_ci95": [percentile(auc_values, 0.025), percentile(auc_values, 0.975)],
        "distinguishability_ci95": [
            percentile(d_values, 0.025),
            percentile(d_values, 0.975),
        ],
        "method": (
            "workload-stratified cluster bootstrap with full grouped-CV pipeline "
            "refit, including feature screening and standardization"
        ),
    }


def permuted_session_labels(rows, labels, rng):
    sessions = {}
    for index, row in enumerate(rows):
        session = sessions.setdefault(
            row["session_id"],
            {
                "indices": [],
                "label": labels[index],
                "scenario": row["scenario"],
                "block": row.get("experiment_block"),
            },
        )
        if session["label"] != labels[index]:
            raise ValueError("one session has both classifier labels")
        if session["scenario"] != row["scenario"]:
            raise ValueError("one session spans multiple scenarios")
        session["indices"].append(index)
    use_blocks = any(session["block"] for session in sessions.values())
    strata = {}
    for name, session in sessions.items():
        if use_blocks and session["block"]:
            key = (session["scenario"], f"block:{session['block']}")
        else:
            key = (session["scenario"], "unblocked")
        strata.setdefault(key, []).append(name)
    permuted = list(labels)
    for session_names in strata.values():
        shuffled = [sessions[name]["label"] for name in session_names]
        rng.shuffle(shuffled)
        for name, label in zip(session_names, shuffled):
            for index in sessions[name]["indices"]:
                permuted[index] = label
    return permuted


def permutation_test(
    rows,
    labels,
    observed,
    feature_names,
    folds,
    max_features,
    l2,
    fit_iterations,
    iterations,
    seed,
):
    if not iterations:
        return None
    rng = random.Random(seed)
    extreme = 0
    completed = 0
    attempts = 0
    while completed < iterations and attempts < max(iterations * 5, 20):
        attempts += 1
        permuted = permuted_session_labels(rows, labels, rng)
        try:
            fitted = fit_cross_validated(
                rows,
                permuted,
                feature_names,
                folds,
                seed,
                max_features,
                l2,
                fit_iterations,
                False,
            )
            value = fold_auc_metrics(permuted, fitted["scores"], fitted["fold_ids"])[
                "auc"
            ]
        except ValueError:
            continue
        completed += 1
        if value >= observed:
            extreme += 1
    if completed < iterations:
        raise ValueError("permutation refits produced too few valid grouped splits")
    return {
        "iterations": completed,
        "p_value": (extreme + 1) / (completed + 1),
        "alternative": "orientation-fixed AUC greater than the null",
        "method": (
            "session-label permutation within experiment block and workload "
            "with full grouped-CV pipeline refit"
        ),
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
    refit_bootstrap_iterations=0,
):
    try:
        fitted = fit_cross_validated(
            rows,
            labels,
            feature_names,
            folds,
            seed,
            max_features,
            l2,
            iterations,
            True,
        )
    except ValueError as error:
        return {"available": False, "reason": str(error)}
    split = fitted["split"]
    scores = fitted["scores"]
    hard_predictions = fitted["hard_predictions"]
    coefficients = fitted["coefficients"]
    thresholds = fitted["thresholds"]
    auc_metrics = fold_auc_metrics(labels, scores, fitted["fold_ids"])
    auc_value = auc_metrics["auc"]
    distinguishability = max(auc_value, 1 - auc_value)
    metrics = confusion_metrics(labels, hard_predictions)
    importance = []
    for name, values in coefficients.items():
        padded = values + [0.0] * (len(split) - len(values))
        importance.append({
            "feature": name,
            "mean_abs_standardized_coefficient": statistics.fmean(
                abs(value) for value in padded
            ),
            "mean_signed_standardized_coefficient": statistics.fmean(padded),
            "selection_count": len(values),
        })
    importance.sort(
        key=lambda item: (-item["mean_abs_standardized_coefficient"], item["feature"])
    )
    result = {
        "available": True,
        "samples": {"negative": labels.count(0), "positive": labels.count(1)},
        "groups": len({analysis_group(row) for row in rows}),
        "sessions": len({row["session_id"] for row in rows}),
        "grouping": (
            "experiment_block"
            if any(row.get("experiment_block") for row in rows)
            else "session_id"
        ),
        "folds": len(split),
        "features_considered": len(feature_names),
        "max_features_per_fold": max_features,
        "auc": auc_value,
        "macro_fold_auc": auc_metrics["macro_auc"],
        "fold_auc": auc_metrics["fold_auc"],
        "comparable_pairs": auc_metrics["comparable_pairs"],
        "distinguishability": distinguishability,
        "train_selected_threshold_median": statistics.median(thresholds),
        **metrics,
        **clustered_bootstrap(
            rows,
            labels,
            scores,
            fitted["fold_ids"],
            bootstrap_iterations,
            seed + 104729,
        ),
        "top_features": importance[:12],
    }
    try:
        result["permutation_test"] = permutation_test(
            rows,
            labels,
            auc_value,
            feature_names,
            folds,
            max_features,
            l2,
            iterations,
            permutation_iterations,
            seed + 130363,
        )
    except ValueError as error:
        result["permutation_test"] = {
            "available": False,
            "reason": str(error),
        }
    if refit_bootstrap_iterations:
        try:
            result["refit_bootstrap"] = refit_clustered_bootstrap(
                rows,
                labels,
                feature_names,
                folds,
                seed + 155921,
                max_features,
                l2,
                iterations,
                refit_bootstrap_iterations,
            )
        except ValueError as error:
            result["refit_bootstrap"] = {
                "available": False,
                "reason": str(error),
            }
    return result


def classify(target, baseline, mode):
    if not target.get("available") or not baseline.get("available"):
        return "inconclusive"
    if mode != "research":
        return "inconclusive"
    target_uncertainty = target.get("refit_bootstrap")
    baseline_uncertainty = baseline.get("refit_bootstrap")
    if not isinstance(target_uncertainty, dict) or not isinstance(
        baseline_uncertainty, dict
    ):
        return "inconclusive"
    if (
        target_uncertainty.get("available") is False
        or baseline_uncertainty.get("available") is False
    ):
        return "inconclusive"
    value = target["auc"]
    lower, upper = target_uncertainty["auc_ci95"]
    baseline_value = baseline["auc"]
    baseline_lower, baseline_upper = baseline_uncertainty["auc_ci95"]
    baseline_healthy = (
        0.40 <= baseline_value <= 0.60
        and 0.40 <= baseline_lower <= 0.50 <= baseline_upper <= 0.60
        and baseline_upper - baseline_lower <= 0.12
    )
    if not baseline_healthy:
        return "yellow"
    permutation = target.get("permutation_test")
    significant = bool(
        permutation
        and permutation.get("p_value") is not None
        and permutation["p_value"] <= 0.05
    )
    if value >= 0.70 and lower >= 0.60 and significant:
        return "red"
    target_advantage = abs(value - 0.50)
    baseline_advantage = abs(baseline_value - 0.50)
    if (
        0.40 <= lower
        and upper <= 0.60
        and upper - lower <= 0.12
        and target_advantage - baseline_advantage <= 0.05
    ):
        return "green"
    return "yellow"


def permutation_plan(view, comparison, requested):
    if comparison != "firefox_vs_naivefox":
        return 0, "the Firefox-A-versus-Firefox-B baseline is not a verdict test"
    if view not in PRIMARY_VIEWS:
        return 0, "permutation refits are reserved for the predeclared primary views"
    if not requested:
        return 0, "disabled by --permutations=0"
    return requested, None


def cross_workload(rows, feature_names, seed, max_features, l2, iterations):
    selected, labels = comparison_rows(rows, "firefox_vs_naivefox")
    scenarios = sorted({row["scenario"] for row in selected})
    results = {}
    for scenario in scenarios:
        train = [
            index for index, row in enumerate(selected) if row["scenario"] != scenario
        ]
        test = [
            index for index, row in enumerate(selected) if row["scenario"] == scenario
        ]
        if (
            len({labels[index] for index in train}) < 2
            or len({labels[index] for index in test}) < 2
        ):
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
        "macro_auc": (
            statistics.fmean(item["auc"] for item in results.values())
            if results
            else None
        ),
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
        if name.startswith((
            "tls_",
            "tcp_syn_",
            "quic_initial_",
            "quic_tp_",
            "quic_version_",
        ))
    ]
    firefox = [row for row in rows if row["label"].startswith("firefox_")]
    naivefox = [row for row in rows if row["label"] == "naivefox"]
    differences = []
    for name in names:
        left = statistics.fmean(row["features"].get(name, 0.0) for row in firefox)
        right = statistics.fmean(row["features"].get(name, 0.0) for row in naivefox)
        if abs(left - right) > 1e-12:
            differences.append({
                "feature": name,
                "firefox_mean": left,
                "naivefox_mean": right,
            })
    differences.sort(
        key=lambda item: (
            -abs(item["firefox_mean"] - item["naivefox_mean"]),
            item["feature"],
        )
    )
    return {
        "scope": "passively visible handshake fields only; not decrypted H2/H3 parity",
        "different_feature_count": len(differences),
        "top_differences": differences[:20],
    }


def absolute_inference_status(rows, mode, screening_only=False):
    protocol_samples = {}
    for protocol in sorted({row["protocol"] for row in rows}):
        protocol_rows = [row for row in rows if row["protocol"] == protocol]
        protocol_samples[protocol] = {
            label: len({
                analysis_group(row) for row in protocol_rows if row["label"] == label
            })
            for label in COHORT_LABELS
        }
    research_shortfalls = []
    for protocol, samples in protocol_samples.items():
        for label, count in samples.items():
            if count < MIN_RESEARCH_SAMPLES_PER_COHORT:
                research_shortfalls.append({
                    "protocol": protocol,
                    "cohort": label,
                    "samples": count,
                    "minimum": MIN_RESEARCH_SAMPLES_PER_COHORT,
                })
    research_samples_sufficient = bool(protocol_samples) and not research_shortfalls
    reasons = []
    if screening_only:
        reasons.append("multi-arm screening analysis cannot emit an absolute verdict")
    if mode != "research":
        reasons.append(f"{mode} mode is non-inferential")
    elif not research_samples_sufficient:
        reasons.append(
            "research mode requires at least "
            f"{MIN_RESEARCH_SAMPLES_PER_COHORT} samples in every cohort and protocol"
        )
    supports_absolute_verdict = (
        mode == "research" and not screening_only and research_samples_sufficient
    )
    return {
        "status": (
            "RESEARCH_VERDICT_ENABLED" if supports_absolute_verdict else "INCONCLUSIVE"
        ),
        "supports_absolute_verdict": supports_absolute_verdict,
        "screening_only": bool(screening_only),
        "minimum_research_samples_per_cohort": MIN_RESEARCH_SAMPLES_PER_COHORT,
        "protocol_samples": protocol_samples,
        "research_samples_sufficient": research_samples_sufficient,
        "research_sample_shortfalls": research_shortfalls,
        "reasons": reasons,
    }


def build_report(args, rows, all_feature_names):
    inference = absolute_inference_status(
        rows, args.mode, getattr(args, "screening_only", False)
    )
    verdict_mode = "research" if inference["supports_absolute_verdict"] else "standard"
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": args.mode,
        "seed": args.seed,
        "inference": inference,
        "methodology": {
            "classifier": "dependency-free L2 logistic regression",
            "split": (
                "workload-stratified grouped cross-validation by experiment_block "
                "when present, otherwise session_id"
            ),
            "preprocessing": "train-only standardization and feature screening",
            "primary_metric": (
                "orientation-fixed ROC AUC, pair-weighted across outer folds; "
                "NaiveFox or Firefox-B is always the positive class"
            ),
            "diagnostic_metric": "D=max(ROC_AUC,1-ROC_AUC), never used for verdicts",
            "missing_phases": (
                "structural zeros plus explicit present indicators derived from "
                "observable phase packet counts"
            ),
            "bootstrap": (
                "conditional clustered resampling within outer folds; no comparisons "
                "between uncalibrated fold score scales"
            ),
            "verdict_uncertainty": (
                "workload-stratified cluster bootstrap with full grouped-CV "
                "pipeline refit for both comparisons in primary views"
            ),
            "permutation": "full grouped-CV pipeline refit",
            "permutation_scope": (
                "Firefox-vs-NaiveFox in the three predeclared primary views only"
            ),
            "bootstrap_iterations": args.bootstrap,
            "refit_bootstrap_iterations": args.refit_bootstrap,
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
                permutation_iterations, permutation_reason = permutation_plan(
                    view, comparison, args.permutations
                )
                refit_iterations = args.refit_bootstrap if view in PRIMARY_VIEWS else 0
                comparison_report = analyze_comparison(
                    selected,
                    labels,
                    names,
                    args.folds,
                    args.seed
                    + stable_offset(f"{protocol}:{view}:{comparison}", 100000),
                    args.max_features,
                    args.l2,
                    args.iterations,
                    args.bootstrap,
                    permutation_iterations,
                    refit_iterations,
                )
                if not permutation_iterations:
                    comparison_report["permutation_test"] = None
                    comparison_report["permutation_test_reason"] = permutation_reason
                view_report[comparison] = comparison_report
            view_report["classification"] = classify(
                view_report["firefox_vs_naivefox"],
                view_report["firefox_baseline"],
                verdict_mode,
            )
            if view_report["firefox_baseline"].get("available") and view_report[
                "firefox_vs_naivefox"
            ].get("available"):
                view_report["baseline_delta"] = (
                    view_report["firefox_vs_naivefox"]["auc"]
                    - view_report["firefox_baseline"]["auc"]
                )
            protocol_report["views"][view] = view_report
        for scenario in sorted({row["scenario"] for row in protocol_rows}):
            workload_rows = [
                row for row in protocol_rows if row["scenario"] == scenario
            ]
            workload_report = {}
            for comparison in ("firefox_baseline", "firefox_vs_naivefox"):
                selected, labels = comparison_rows(workload_rows, comparison)
                comparison_report = analyze_comparison(
                    selected,
                    labels,
                    view_feature_names(all_feature_names, "whole"),
                    args.folds,
                    args.seed
                    + stable_offset(f"{protocol}:{scenario}:{comparison}", 100000),
                    args.max_features,
                    args.l2,
                    args.iterations,
                    args.bootstrap,
                    0,
                    0,
                )
                comparison_report["permutation_test"] = None
                comparison_report["permutation_test_reason"] = (
                    "per-workload results are exploratory, not primary verdict tests"
                )
                workload_report[comparison] = comparison_report
            workload_report["classification"] = classify(
                workload_report["firefox_vs_naivefox"],
                workload_report["firefox_baseline"],
                verdict_mode,
            )
            protocol_report["workloads"][scenario] = workload_report
        report["protocols"][protocol] = protocol_report
    red = []
    green = []
    for protocol, protocol_report in report["protocols"].items():
        for view in PRIMARY_VIEWS:
            classification = protocol_report["views"][view]["classification"]
            red.append(classification == "red")
            green.append(classification == "green")
    if inference["supports_absolute_verdict"] and any(red):
        conclusion = "YES"
    elif inference["supports_absolute_verdict"] and green and all(green):
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
    lower, upper = result["auc_ci95"]
    text = (
        f"AUC={result['auc']:.3f} CI95=[{lower:.3f},{upper:.3f}] "
        f"diagnostic_D={result['distinguishability']:.3f}"
    )
    refit = result.get("refit_bootstrap")
    if isinstance(refit, dict) and "auc_ci95" in refit:
        refit_lower, refit_upper = refit["auc_ci95"]
        text += f" refit_CI95=[{refit_lower:.3f},{refit_upper:.3f}]"
    return text


def write_summary(path, report):
    inference = report["inference"]
    lines = [
        "NaiveFox passive camouflage experiment",
        f"mode={report['mode']} seed={report['seed']}",
        f"inference_status={inference['status']}",
        "absolute_verdict_enabled="
        + str(inference["supports_absolute_verdict"]).lower(),
        (
            "Interpretation: selected classifiers attempt to distinguish cohorts using "
            "externally observable wire features."
        ),
    ]
    for reason in inference["reasons"]:
        lines.append(f"inconclusive: {reason}")
    lines.append("")
    for protocol, data in report["protocols"].items():
        lines.append(protocol.upper())
        lines.append(
            "samples="
            + ", ".join(f"{name}:{count}" for name, count in data["samples"].items())
        )
        for view in (
            "whole",
            "initial_packets_16",
            "packets_17_32",
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
            lines.append(
                f"{view} Firefox-vs-Firefox: {format_metric(item['firefox_baseline'])}"
            )
            lines.append(
                f"{view} Firefox-vs-NaiveFox: "
                f"{format_metric(item['firefox_vs_naivefox'])} {item['classification'].upper()}"
            )
            top = item["firefox_vs_naivefox"].get("top_features", [])[:3]
            if top:
                lines.append(
                    "  top=" + ", ".join(feature["feature"] for feature in top)
                )
        cross = data["cross_workload"]["macro_auc"]
        holdouts = data["cross_workload"]["holdouts"]
        lines.append(
            "cross_workload_holdouts="
            + str(len(holdouts))
            + " samples="
            + str(sum(item["samples"] for item in holdouts.values()))
        )
        lines.append(
            "cross_workload_macro_AUC="
            + ("unavailable" if cross is None else f"{cross:.3f}")
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
    parser.add_argument(
        "--mode", choices=("gate", "smoke", "standard", "research"), required=True
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--refit-bootstrap", type=int, default=0)
    parser.add_argument("--permutations", type=int, default=0)
    parser.add_argument("--max-features", type=int, default=64)
    parser.add_argument("--l2", type=float, default=0.05)
    parser.add_argument("--iterations", type=int, default=180)
    parser.add_argument(
        "--screening-only",
        action="store_true",
        help="disable absolute verdicts for multi-arm candidate screening",
    )
    args = parser.parse_args()
    if args.bootstrap < 100:
        raise SystemExit("bootstrap iterations must be at least 100")
    if args.refit_bootstrap not in (0,) and args.refit_bootstrap < 20:
        raise SystemExit("refit bootstrap iterations must be zero or at least 20")
    rows, feature_names = load_dataset(args.features)
    inference = absolute_inference_status(rows, args.mode, args.screening_only)
    if (
        args.mode == "research"
        and not args.screening_only
        and not inference["research_samples_sufficient"]
    ):
        raise SystemExit("; ".join(inference["reasons"]))
    report = build_report(args, rows, feature_names)
    with open(args.output_json, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    write_summary(args.output_summary, report)


if __name__ == "__main__":
    main()
