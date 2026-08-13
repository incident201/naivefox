#!/usr/bin/env bash

set -euo pipefail

integration_dir=$(cd "$(dirname "$0")" && pwd)

printf '%s\n' 'Running complete H2 suite'
"$integration_dir/run-local-suite.sh"

printf '%s\n' 'Running complete H3 suite'
"$integration_dir/run-h3-suite.sh"

printf '%s\n' 'NaiveFox complete H2 and H3 integration suite passed'
