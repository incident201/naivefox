#!/usr/bin/env bash
set -euo pipefail
integration_dir=$(cd "$(dirname "$0")" && pwd)
python3 -B -m unittest discover -s "$integration_dir" -p 'test_*.py' -v
