#!/usr/bin/env bash

set -euo pipefail

integration_dir=$(cd "$(dirname "$0")" && pwd)
exec "$integration_dir/run-robustness-tests.sh" --protocol h3
