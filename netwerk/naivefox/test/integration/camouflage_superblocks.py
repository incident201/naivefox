#!/usr/bin/env python3

import argparse
import csv
import os
import random

DEFAULT_ARMS = ("off", "gate", "root")
ARMS = DEFAULT_ARMS
SUPPORTED_ARMS = (
    "h2-finite-socks",
    "h2-finite-http-connect",
    "h2-finite-read-through-socks",
    "h2-finite-read-through-http-connect",
    "h2-finite-both-read-through-socks",
    "h2-finite-both-read-through-http-connect",
    "h2-finite-both-read-through-budgeted-socks",
    "h2-finite-both-read-through-budgeted-http-connect",
    "h2-finite-both-read-through-budgeted-data-window-socks",
    "h2-finite-both-read-through-budgeted-data-window-http-connect",
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
    "document-first-buffer-task-optimistic",
    "document-first-buffer-task-http-connect",
    "document-first-buffer-http-connect",
    "document-first-buffer-http-connect-optimistic",
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
)
REFERENCE_ARM = "reference"
METADATA_FIELDS = {
    "schema_version",
    "protocol",
    "scenario",
    "label",
    "naivefox_arm",
    "session_id",
    "experiment_block",
}


def validate_arm_sequence(arms):
    arms = tuple(arms)
    if len(arms) < 2:
        raise ValueError("multi-arm screening requires at least two arms")
    if len(set(arms)) != len(arms):
        raise ValueError("multi-arm list contains duplicate arms")
    invalid = sorted(set(arms) - set(SUPPORTED_ARMS))
    if invalid:
        raise ValueError(f"invalid multi-arm labels: {invalid}")
    if "root" in arms and "document-complete" in arms:
        raise ValueError("root and document-complete are aliases; select only one")
    if (
        "tree-native-parser-document-start-resource-tree" in arms
        and "document-start-overlap" not in arms
    ):
        raise ValueError(
            "tree-native-parser-document-start-resource-tree requires the "
            "document-start-overlap control"
        )
    if (
        "tree-resource-committed-overlap-tree" in arms
        and "document-start-overlap" not in arms
    ):
        raise ValueError(
            "tree-resource-committed-overlap-tree requires the "
            "document-start-overlap control"
        )
    if (
        "tree-resource-committed-overlap-page" in arms
        and "document-start-overlap" not in arms
    ):
        raise ValueError(
            "tree-resource-committed-overlap-page requires the "
            "document-start-overlap control"
        )
    if (
        "tree-native-parser-resource-committed-tree" in arms
        and "document-start-overlap" not in arms
    ):
        raise ValueError(
            "tree-native-parser-resource-committed-tree requires the "
            "document-start-overlap control"
        )
    if (
        "tree-native-parser-resource-committed-page" in arms
        and "document-start-overlap" not in arms
    ):
        raise ValueError(
            "tree-native-parser-resource-committed-page requires the "
            "document-start-overlap control"
        )
    if (
        "tree-native-parser-resource-committed-page-http-connect" in arms
        and "document-start-http-connect" not in arms
    ):
        raise ValueError(
            "tree-native-parser-resource-committed-page-http-connect requires "
            "the document-start-http-connect control"
        )
    for resource_tree_arm in (
        "tree-complete-resource-tree",
        "tree-early-overlap-resource-tree",
    ):
        if resource_tree_arm in arms and "document-start-overlap" not in arms:
            raise ValueError(
                f"{resource_tree_arm} requires the document-start-overlap control"
            )
    if (
        "tree-native-parser-document-start-navigation-stop-css" in arms
        and "tree-native-parser-document-start-overlap-css" not in arms
    ):
        raise ValueError(
            "tree-native-parser-document-start-navigation-stop-css requires "
            "the tree-native-parser-document-start-overlap-css control"
        )
    if (
        "tree-native-parser-document-start-response-stop-css" in arms
        and "tree-native-parser-document-start-navigation-stop-css" not in arms
    ):
        raise ValueError(
            "tree-native-parser-document-start-response-stop-css requires "
            "the tree-native-parser-document-start-navigation-stop-css control"
        )
    if (
        "tree-native-parser-process-overlap-css" in arms
        and "tree-native-parser-root-rendezvous-overlap-css" not in arms
    ):
        raise ValueError(
            "tree-native-parser-process-overlap-css requires the "
            "tree-native-parser-root-rendezvous-overlap-css control"
        )
    if (
        "tree-native-parser-full-process-overlap-css" in arms
        and "tree-native-parser-process-overlap-css" not in arms
    ):
        raise ValueError(
            "tree-native-parser-full-process-overlap-css requires the "
            "tree-native-parser-process-overlap-css control"
        )
    return arms


def parse_arms(value):
    return validate_arm_sequence(
        item.strip() for item in value.split(",") if item.strip()
    )


def infer_arms(rows):
    selected = {
        row["naivefox_arm"] for row in rows if row.get("naivefox_arm") != REFERENCE_ARM
    }
    invalid = sorted(selected - set(SUPPORTED_ARMS))
    if invalid:
        raise ValueError(f"invalid arm labels: {invalid}")
    arms = tuple(arm for arm in SUPPORTED_ARMS if arm in selected)
    return validate_arm_sequence(arms)


def schedule_rows(seed, protocol, count, scenarios, arms=DEFAULT_ARMS):
    arms = validate_arm_sequence(arms)
    if any(
        arm in arms
        for arm in (
            "tree-native-parser-document-start-resource-tree",
            "tree-resource-committed-overlap-tree",
            "tree-native-parser-resource-committed-tree",
            "tree-complete-resource-tree",
            "tree-early-overlap-resource-tree",
            "tree-resource-committed-overlap-page",
            "tree-native-parser-resource-committed-page",
            "tree-native-parser-resource-committed-page-http-connect",
        )
    ) and set(scenarios) != {"browser_page"}:
        raise ValueError("resource-tree arms require browser_page superblocks")
    if protocol != "h3" and "root-pmtud-control" in arms:
        raise ValueError("root-pmtud-control requires h3 superblocks")
    if protocol != "h3" and "document-handshake-confirmed" in arms:
        raise ValueError("document-handshake-confirmed requires h3 superblocks")
    if protocol != "h3" and "document-carrier-dispatch" in arms:
        raise ValueError("document-carrier-dispatch requires h3 superblocks")
    if protocol != "h3" and "document-cold-winner-handoff" in arms:
        raise ValueError("document-cold-winner-handoff requires h3 superblocks")
    if protocol != "h3" and "document-native-cache-open" in arms:
        raise ValueError("document-native-cache-open requires h3 superblocks")
    if protocol != "h3" and "tree-resource-committed-overlap-tree" in arms:
        raise ValueError("tree-resource-committed-overlap-tree requires h3 superblocks")
    if protocol != "h3" and "tree-resource-committed-overlap-page" in arms:
        raise ValueError("tree-resource-committed-overlap-page requires h3 superblocks")
    if protocol != "h3" and "tree-native-parser-resource-committed-tree" in arms:
        raise ValueError(
            "tree-native-parser-resource-committed-tree requires h3 superblocks"
        )
    if protocol != "h3" and any(
        arm in arms
        for arm in (
            "tree-complete-resource-tree",
            "tree-early-overlap-resource-tree",
        )
    ):
        raise ValueError("resource-tree timing arms require h3 superblocks")
    if protocol != "h3" and any(
        arm.startswith("tree-native-parser-")
        and arm
        not in (
            "tree-native-parser-document-start-overlap-css",
            "tree-native-parser-document-start-navigation-stop-css",
            "tree-native-parser-document-start-resource-tree",
            "tree-native-parser-resource-committed-page",
            "tree-native-parser-resource-committed-page-http-connect",
        )
        for arm in arms
    ):
        raise ValueError("native parser arms require h3 superblocks")
    rng = random.Random(f"{seed}:{protocol}:multi-arm-superblocks")
    rows = []
    for index in range(count):
        block = f"{protocol}_sb{index:06d}"
        scenario = scenarios[index % len(scenarios)]
        members = [
            ("firefox_a", REFERENCE_ARM),
            ("firefox_b", REFERENCE_ARM),
            *(("naivefox", arm) for arm in arms),
        ]
        rng.shuffle(members)
        for label, arm in members:
            rows.append({
                "protocol": protocol,
                "label": label,
                "naivefox_arm": arm,
                "scenario": scenario,
                "experiment_block": block,
            })
    return rows


def validate_superblocks(rows, expected_blocks=None, require_dataset=False, arms=None):
    if not rows:
        raise ValueError("superblock dataset has no rows")
    required = METADATA_FIELDS - {"schema_version", "session_id"}
    if require_dataset:
        required |= {"schema_version", "session_id"}
    if rows and not required.issubset(rows[0]):
        missing = sorted(required - set(rows[0]))
        raise ValueError(f"superblock dataset lacks metadata fields: {missing}")
    selected_arms = infer_arms(rows) if arms is None else validate_arm_sequence(arms)
    blocks = {}
    for row in rows:
        if row["naivefox_arm"] not in {*SUPPORTED_ARMS, REFERENCE_ARM}:
            raise ValueError(f"invalid arm label: {row['naivefox_arm']}")
        if row["naivefox_arm"] == "root-pmtud-control" and row["protocol"] != "h3":
            raise ValueError("root-pmtud-control requires h3 superblocks")
        if (
            row["naivefox_arm"] == "document-handshake-confirmed"
            and row["protocol"] != "h3"
        ):
            raise ValueError("document-handshake-confirmed requires h3 superblocks")
        if (
            row["naivefox_arm"] == "document-carrier-dispatch"
            and row["protocol"] != "h3"
        ):
            raise ValueError("document-carrier-dispatch requires h3 superblocks")
        if (
            row["naivefox_arm"] == "document-cold-winner-handoff"
            and row["protocol"] != "h3"
        ):
            raise ValueError("document-cold-winner-handoff requires h3 superblocks")
        if (
            row["naivefox_arm"] == "document-native-cache-open"
            and row["protocol"] != "h3"
        ):
            raise ValueError("document-native-cache-open requires h3 superblocks")
        key = (row["protocol"], row["experiment_block"])
        blocks.setdefault(key, []).append(row)
    if expected_blocks is not None:
        protocols = {row["protocol"] for row in rows}
        for protocol in protocols:
            count = sum(key[0] == protocol for key in blocks)
            if count != expected_blocks:
                raise ValueError(
                    f"{protocol} has {count} superblocks, expected {expected_blocks}"
                )
    expected_members = {
        ("firefox_a", REFERENCE_ARM),
        ("firefox_b", REFERENCE_ARM),
        *(("naivefox", arm) for arm in selected_arms),
    }
    for (protocol, block), members in sorted(blocks.items()):
        scenarios = {row["scenario"] for row in members}
        actual_members = {(row["label"], row["naivefox_arm"]) for row in members}
        if len(members) != len(expected_members) or actual_members != expected_members:
            raise ValueError(
                f"incomplete superblock {protocol}/{block}: "
                f"members={sorted(actual_members)}"
            )
        if len(scenarios) != 1:
            raise ValueError(
                f"superblock {protocol}/{block} spans scenarios: {sorted(scenarios)}"
            )
        if any(
            arm in selected_arms
            for arm in (
                "tree-native-parser-document-start-resource-tree",
                "tree-resource-committed-overlap-tree",
                "tree-native-parser-resource-committed-tree",
                "tree-complete-resource-tree",
                "tree-early-overlap-resource-tree",
                "tree-resource-committed-overlap-page",
                "tree-native-parser-resource-committed-page",
                "tree-native-parser-resource-committed-page-http-connect",
            )
        ) and scenarios != {"browser_page"}:
            raise ValueError("resource-tree arms require browser_page superblocks")


def write_csv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def materialize_arms(input_path, output_dir, expected_blocks=None, arms=None):
    with open(input_path, newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("superblock dataset has no header")
        rows = list(reader)
        fieldnames = list(reader.fieldnames)
    selected_arms = infer_arms(rows) if arms is None else validate_arm_sequence(arms)
    validate_superblocks(
        rows,
        expected_blocks,
        require_dataset=True,
        arms=selected_arms,
    )
    outputs = {}
    for arm in selected_arms:
        selected = [
            row
            for row in rows
            if row["naivefox_arm"] == REFERENCE_ARM
            or (row["label"] == "naivefox" and row["naivefox_arm"] == arm)
        ]
        destination = os.path.join(output_dir, arm, "features.csv")
        write_csv(destination, fieldnames, selected)
        outputs[arm] = destination
    return outputs


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    schedule = commands.add_parser("schedule")
    schedule.add_argument("--seed", type=int, required=True)
    schedule.add_argument("--protocol", choices=("h2", "h3"), required=True)
    schedule.add_argument("--blocks", type=int, required=True)
    schedule.add_argument("--scenarios", required=True)
    schedule.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--features", required=True)
    materialize.add_argument("--output-dir", required=True)
    materialize.add_argument("--expected-blocks", type=int)
    materialize.add_argument("--arms", default=None)
    args = parser.parse_args()
    if args.command == "schedule":
        if args.blocks <= 0:
            raise SystemExit("blocks must be positive")
        scenarios = [item for item in args.scenarios.split(",") if item]
        if not scenarios:
            raise SystemExit("at least one scenario is required")
        try:
            arms = parse_arms(args.arms)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        for row in schedule_rows(
            args.seed, args.protocol, args.blocks, scenarios, arms=arms
        ):
            print(
                row["label"],
                row["naivefox_arm"],
                row["scenario"],
                row["experiment_block"],
                sep="\t",
            )
    else:
        try:
            arms = parse_arms(args.arms) if args.arms is not None else None
            materialize_arms(
                args.features,
                args.output_dir,
                args.expected_blocks,
                arms=arms,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
