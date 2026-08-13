#!/usr/bin/env bash

set -euo pipefail

integration_dir=$(cd "$(dirname "$0")" && pwd)
suites=(
  run-h3-raw-connect-tests.sh
  run-h3-socks-tests.sh
  run-h3-padded-tests.sh
  run-h3-robustness-tests.sh
  run-auto-protocol-tests.sh
  run-h3-capture-comparison.sh
)

for suite in "${suites[@]}"; do
  printf 'Running %s\n' "$suite"
  "$integration_dir/$suite"
done

printf '%s\n' 'NaiveFox complete local H3 integration suite passed'
