#!/usr/bin/env bash

set -euo pipefail

readonly integration_dir=$(cd "$(dirname "$0")" && pwd)
exec "$integration_dir/run-throughput-benchmark.sh" --protocol h3
