#!/usr/bin/env bash

set -euo pipefail

integration_dir=$(cd "$(dirname "$0")" && pwd)
suites=(
  run-control-tests.sh
  run-necko-tests.sh
  run-raw-connect-tests.sh
  run-socks-tests.sh
  run-padded-tests.sh
  run-robustness-tests.sh
  run-capture-comparison.sh
)

for suite in "${suites[@]}"; do
  printf 'Running %s\n' "$suite"
  "$integration_dir/$suite"
done

printf '%s\n' 'NaiveFox complete local integration suite passed'
