#!/usr/bin/env bash

set -euo pipefail

integration_dir=$(cd "$(dirname "$0")" && pwd)
cd "$integration_dir"
python3 -m unittest -v \
  test_camouflage_analysis \
  test_camouflage_arm_analysis \
  test_camouflage_harness \
  test_h2_decrypted_parity_summary \
  test_h2_connect_priority_summary \
  test_h2_request_lifecycle_summary \
  test_h3_decrypted_arm_summary \
  test_firefox_repeat_navigation_summary \
  test_network_mutation_monitor \
  test_private_h3_lifecycle
