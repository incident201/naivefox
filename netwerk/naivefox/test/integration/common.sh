#!/usr/bin/env bash

set -euo pipefail

INTEGRATION_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SOURCE_ROOT=$(cd "$INTEGRATION_DIR/../../../.." && pwd)

source "$INTEGRATION_DIR/versions.env"

topobjdir() {
  if [[ -n "${MOZ_OBJDIR:-}" ]]; then
    printf '%s\n' "$MOZ_OBJDIR"
    return
  fi
  "$SOURCE_ROOT/mach" environment --format json |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["topobjdir"])'
}

init_paths() {
  OBJDIR=$(topobjdir)
  STATE_ROOT="$OBJDIR/naivefox-fixture"
  TOOLS_DIR="$STATE_ROOT/tools"
  CADDY_BIN="$TOOLS_DIR/bin/caddy"
  ACTIVE_RUN_FILE="$STATE_ROOT/active-run"
  mkdir -p "$TOOLS_DIR/bin" "$STATE_ROOT/runs"
}

find_certutil() {
  if command -v certutil >/dev/null 2>&1; then
    CERTUTIL=$(command -v certutil)
    CERTUTIL_LIBRARY_PATH=
  elif [[ -x "$OBJDIR/dist/bin/certutil" ]]; then
    CERTUTIL="$OBJDIR/dist/bin/certutil"
    CERTUTIL_LIBRARY_PATH="$OBJDIR/dist/bin"
  else
    printf 'certutil not found; build Firefox or install libnss3-tools\n' >&2
    return 1
  fi
}

run_certutil() {
  if [[ -n "$CERTUTIL_LIBRARY_PATH" ]]; then
    LD_LIBRARY_PATH="$CERTUTIL_LIBRARY_PATH${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
      "$CERTUTIL" "$@"
  else
    "$CERTUTIL" "$@"
  fi
}

wait_for_file() {
  local file=$1
  local pid=$2
  local description=$3
  local attempts=${4:-100}
  for ((i = 0; i < attempts; i++)); do
    [[ -s "$file" ]] && return 0
    if ! kill -0 "$pid" 2>/dev/null; then
      printf '%s exited before readiness\n' "$description" >&2
      return 1
    fi
    sleep 0.1
  done
  printf 'timed out waiting for %s\n' "$description" >&2
  return 1
}

wait_for_proxy() {
  local pid=$1
  local port=$2
  local ca=$3
  for ((i = 0; i < 150; i++)); do
    if curl --silent --output /dev/null --connect-timeout 1 \
      --max-time 2 --cacert "$ca" "https://localhost:$port/"; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      printf 'Caddy exited before readiness\n' >&2
      return 1
    fi
    sleep 0.1
  done
  printf 'timed out waiting for Caddy listener\n' >&2
  return 1
}

wait_for_h3_proxy() {
  local pid=$1
  local port=$2
  local log=$3
  for ((i = 0; i < 150; i++)); do
    if ss -H -lun "sport = :$port" | grep -F "127.0.0.1:$port" >/dev/null &&
      grep -Fq 'enabling HTTP/3 listener' "$log"; then
      if [[ -n $(ss -H -ltn "sport = :$port") ]]; then
        printf 'strict H3 fixture unexpectedly has a TCP listener\n' >&2
        return 1
      fi
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      printf 'Caddy exited before HTTP/3 readiness\n' >&2
      return 1
    fi
    sleep 0.1
  done
  printf 'timed out waiting for Caddy HTTP/3 listener\n' >&2
  return 1
}

sanitize_stream() {
  local user=${1:-}
  local pass=${2:-}
  local expressions=(
    -E
    -e 's/(Proxy-Authorization:)[^[:space:]]+/\1 <redacted>/Ig'
    -e 's/"Proxy-Authorization":\[[^]]*\]/"Proxy-Authorization":["<redacted>"]/Ig'
    -e 's/(basic_auth[[:space:]]+)[^[:space:]]+[[:space:]]+[^[:space:]]+/\1<redacted> <redacted>/Ig'
  )
  [[ -z "$user" ]] || expressions+=(-e "s/${user//\//\\/}/<redacted-user>/g")
  [[ -z "$pass" ]] || expressions+=(-e "s/${pass//\//\\/}/<redacted-password>/g")
  sed "${expressions[@]}"
}
