#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
quiet=0
[[ ${1:-} == --quiet ]] && quiet=1

if [[ ! -f "$ACTIVE_RUN_FILE" ]]; then
  [[ $quiet -eq 1 ]] || printf 'fixture is not running\n'
  exit 0
fi

RUN_DIR=$(<"$ACTIVE_RUN_FILE")
case "$RUN_DIR" in
  "$STATE_ROOT"/runs/*) ;;
  *)
    printf 'refusing invalid fixture run path: %s\n' "$RUN_DIR" >&2
    exit 1
    ;;
esac

fixture_user=
fixture_pass=
if [[ -f "$RUN_DIR/fixture.env" ]]; then
  source "$RUN_DIR/fixture.env"
  fixture_user=${NAIVEFOX_FIXTURE_USER:-}
  fixture_pass=${NAIVEFOX_FIXTURE_PASS:-}
fi

for name in caddy target; do
  pid_file="$RUN_DIR/$name.pid"
  [[ -f "$pid_file" ]] || continue
  pid=$(<"$pid_file")
  if [[ $pid =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for ((i = 0; i < 50; i++)); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
done

{
  [[ -f "$RUN_DIR/diagnostics.txt" ]] && cat "$RUN_DIR/diagnostics.txt"
  for log in caddy target; do
    if [[ -s "$RUN_DIR/$log.log" ]]; then
      printf '\n[%s.log]\n' "$log"
      tail -n 100 "$RUN_DIR/$log.log"
    fi
  done
} | sanitize_stream "$fixture_user" "$fixture_pass" >"$STATE_ROOT/last-diagnostics.txt"

rm -rf -- "$RUN_DIR"
rm -f -- "$ACTIVE_RUN_FILE"
[[ $quiet -eq 1 ]] || printf 'fixture stopped\n'

