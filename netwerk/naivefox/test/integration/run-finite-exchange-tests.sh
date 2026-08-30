#!/usr/bin/env bash

set -euo pipefail
umask 077
source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
if [[ ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK:-0} != 1 ]]; then
  printf 'finite exchange tests require isolated network mode\n' >&2
  exit 2
fi
if [[ ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED:-0} != 1 ]]; then
  "$INTEGRATION_DIR/setup.sh" >/dev/null
  exec unshare --net --mount-proc \
    "$INTEGRATION_DIR/run-camouflage-isolated-network.sh" "$0"
fi
trap '"$INTEGRATION_DIR/stop.sh" --quiet' EXIT
"$INTEGRATION_DIR/start.sh" --mode h2 --outer-h2-only
run_dir=$(<"$ACTIVE_RUN_FILE")
source "$run_dir/fixture.env"
export NAIVEFOX_FIXTURE_RUN_DIR NAIVEFOX_FIXTURE_PROXY_PORT
export NAIVEFOX_FIXTURE_HTTP_PORT NAIVEFOX_FIXTURE_HTTPS_PORT
export NAIVEFOX_FIXTURE_USER NAIVEFOX_FIXTURE_PASS NAIVEFOX_FIXTURE_CA
python3 "$INTEGRATION_DIR/finite_exchanges/probe.py"
