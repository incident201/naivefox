#!/usr/bin/env bash

set -euo pipefail

integration_dir=$(cd "$(dirname "$0")" && pwd)
python3 -m unittest -v "$integration_dir/test_camouflage_analysis.py"
