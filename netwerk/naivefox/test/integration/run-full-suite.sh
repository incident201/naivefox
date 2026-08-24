#!/usr/bin/env bash

set -euo pipefail

integration_dir=$(cd "$(dirname "$0")" && pwd)

printf '%s\n' 'Running complete H2 suite'
"$integration_dir/run-local-suite.sh"
"$integration_dir/run-h2-config-tests.sh"
"$integration_dir/run-config-runtime-behavior-tests.sh"
"$integration_dir/run-malformed-socks-tests.sh"

printf '%s\n' 'Running complete H3 suite'
"$integration_dir/run-h3-suite.sh"
"$integration_dir/run-h3-config-tests.sh"

printf '%s\n' 'Running passive camouflage structural gate'
"$integration_dir/run-camouflage-self-tests.sh"
"$integration_dir/run-camouflage-suite.sh" --mode gate --protocol both

printf '%s\n' 'NaiveFox complete H2 and H3 integration suite passed'
