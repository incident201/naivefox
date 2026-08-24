#!/usr/bin/env python3

import argparse
import csv
import os
import random

ARMS = ("off", "gate", "root")
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


def schedule_rows(seed, protocol, count, scenarios):
    rng = random.Random(f"{seed}:{protocol}:multi-arm-superblocks")
    rows = []
    for index in range(count):
        block = f"{protocol}_sb{index:06d}"
        scenario = scenarios[index % len(scenarios)]
        members = [
            ("firefox_a", REFERENCE_ARM),
            ("firefox_b", REFERENCE_ARM),
            *(("naivefox", arm) for arm in ARMS),
        ]
        rng.shuffle(members)
        for label, arm in members:
            rows.append(
                {
                    "protocol": protocol,
                    "label": label,
                    "naivefox_arm": arm,
                    "scenario": scenario,
                    "experiment_block": block,
                }
            )
    return rows


def validate_superblocks(rows, expected_blocks=None, require_dataset=False):
    if not rows:
        raise ValueError("superblock dataset has no rows")
    required = METADATA_FIELDS - {"schema_version", "session_id"}
    if require_dataset:
        required |= {"schema_version", "session_id"}
    if rows and not required.issubset(rows[0]):
        missing = sorted(required - set(rows[0]))
        raise ValueError(f"superblock dataset lacks metadata fields: {missing}")
    blocks = {}
    for row in rows:
        if row["naivefox_arm"] not in {*ARMS, REFERENCE_ARM}:
            raise ValueError(f"invalid arm label: {row['naivefox_arm']}")
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
        *(("naivefox", arm) for arm in ARMS),
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


def write_csv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def materialize_arms(input_path, output_dir, expected_blocks=None):
    with open(input_path, newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("superblock dataset has no header")
        rows = list(reader)
        fieldnames = list(reader.fieldnames)
    validate_superblocks(rows, expected_blocks, require_dataset=True)
    outputs = {}
    for arm in ARMS:
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
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--features", required=True)
    materialize.add_argument("--output-dir", required=True)
    materialize.add_argument("--expected-blocks", type=int)
    args = parser.parse_args()
    if args.command == "schedule":
        if args.blocks <= 0:
            raise SystemExit("blocks must be positive")
        scenarios = [item for item in args.scenarios.split(",") if item]
        if not scenarios:
            raise SystemExit("at least one scenario is required")
        for row in schedule_rows(args.seed, args.protocol, args.blocks, scenarios):
            print(
                row["label"],
                row["naivefox_arm"],
                row["scenario"],
                row["experiment_block"],
                sep="\t",
            )
    else:
        try:
            materialize_arms(args.features, args.output_dir, args.expected_blocks)
        except ValueError as error:
            raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
