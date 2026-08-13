#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

NAIVEFOX_BIN="$OBJDIR/dist/bin/naivefox"
if [[ ! -x "$NAIVEFOX_BIN" ]]; then
  printf 'NaiveFox binary not found; run ./mach build first\n' >&2
  exit 1
fi

cleanup() {
  status=$?
  if [[ $status -ne 0 && -n ${RUN_DIR:-} && -d $RUN_DIR ]]; then
    for log in necko-untrusted.log necko-trusted.log necko-hostname.log; do
      if [[ -f $RUN_DIR/$log ]]; then
        cp -- "$RUN_DIR/$log" "$SOURCE_ROOT/artifacts/$log"
      fi
    done
  fi
  "$INTEGRATION_DIR/stop.sh" --quiet || true
  return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$INTEGRATION_DIR/start.sh"
RUN_DIR=$(<"$ACTIVE_RUN_FILE")
source "$RUN_DIR/fixture.env"

export LD_LIBRARY_PATH="$OBJDIR/dist/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MOZ_CRASHREPORTER_DISABLE=1
trusted_url="https://localhost:$NAIVEFOX_FIXTURE_PROXY_PORT/"
wrong_hostname_url="https://127.0.0.1:$NAIVEFOX_FIXTURE_PROXY_PORT/"

if timeout 30 "$NAIVEFOX_BIN" --profile "$NAIVEFOX_FIXTURE_UNTRUSTED_PROFILE" \
  --fetch "$trusted_url" >"$RUN_DIR/necko-untrusted.log" 2>&1; then
  printf 'untrusted NSS profile unexpectedly accepted the fixture CA\n' >&2
  exit 1
fi

timeout 30 "$NAIVEFOX_BIN" --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  --fetch "$trusted_url" >"$RUN_DIR/necko-trusted.log" 2>&1
rg -q '^HTTP status: 200$' "$RUN_DIR/necko-trusted.log"

if timeout 30 "$NAIVEFOX_BIN" --profile "$NAIVEFOX_FIXTURE_TRUSTED_PROFILE" \
  --fetch "$wrong_hostname_url" >"$RUN_DIR/necko-hostname.log" 2>&1; then
  printf 'trusted NSS profile unexpectedly accepted a wrong hostname\n' >&2
  exit 1
fi

printf 'NaiveFox Necko/NSS fixture tests passed\n'
