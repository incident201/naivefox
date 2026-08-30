#!/usr/bin/env bash

set -euo pipefail

integration_dir=$(cd "$(dirname "$0")" && pwd)
python3 -m unittest -v "$integration_dir/test_camouflage_analysis.py"
python3 -m unittest -v "$integration_dir/test_camouflage_arm_analysis.py"
python3 -m unittest -v "$integration_dir/test_camouflage_harness.py"
python3 -m unittest -v "$integration_dir/test_h2_decrypted_parity_summary.py"
python3 -m unittest -v "$integration_dir/test_h2_connect_priority_summary.py"
python3 -m unittest -v "$integration_dir/test_h2_request_lifecycle_summary.py"
python3 -m unittest -v "$integration_dir/test_h3_decrypted_arm_summary.py"
python3 -m unittest -v "$integration_dir/test_firefox_repeat_navigation_summary.py"
python3 -m unittest -v "$integration_dir/test_network_mutation_monitor.py"
python3 -m unittest -v "$integration_dir/test_private_h3_lifecycle.py"
