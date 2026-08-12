#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  printf 'the pinned fixture toolchain currently supports Linux x86_64 only\n' >&2
  exit 1
fi

GO_BIN=
if command -v go >/dev/null 2>&1 && [[ $(go version) == "go version $GO_VERSION linux/amd64" ]]; then
  GO_BIN=$(command -v go)
else
  go_root="$TOOLS_DIR/$GO_VERSION"
  GO_BIN="$go_root/bin/go"
  if [[ ! -x "$GO_BIN" ]]; then
    archive="$TOOLS_DIR/$GO_LINUX_AMD64_ARCHIVE"
    if [[ ! -f "$archive" ]] || ! printf '%s  %s\n' "$GO_LINUX_AMD64_SHA256" "$archive" | sha256sum --check --status; then
      curl --fail --location --retry 3 --output "$archive.tmp" \
        "https://go.dev/dl/$GO_LINUX_AMD64_ARCHIVE"
      printf '%s  %s\n' "$GO_LINUX_AMD64_SHA256" "$archive.tmp" | sha256sum --check --status
      mv "$archive.tmp" "$archive"
    fi
    extract_dir=$(mktemp -d "$TOOLS_DIR/.go-extract.XXXXXX")
    trap 'rm -rf -- "$extract_dir"' EXIT
    tar -C "$extract_dir" -xzf "$archive"
    mv "$extract_dir/go" "$go_root"
    rm -rf -- "$extract_dir"
    trap - EXIT
  fi
fi

export GOCACHE="$TOOLS_DIR/go-build-cache"
export GOMODCACHE="$TOOLS_DIR/go-module-cache"
mkdir -p "$GOCACHE" "$GOMODCACHE"

XCADDY_BIN=
if command -v xcaddy >/dev/null 2>&1 && xcaddy version 2>/dev/null | rg -q "^${XCADDY_VERSION}([[:space:]]|$)"; then
  XCADDY_BIN=$(command -v xcaddy)
else
  XCADDY_BIN="$TOOLS_DIR/bin/xcaddy"
  xcaddy_marker="$TOOLS_DIR/bin/xcaddy.version"
  if [[ ! -x "$XCADDY_BIN" || ! -f "$xcaddy_marker" || $(<"$xcaddy_marker") != "$XCADDY_VERSION" ]]; then
    GOBIN="$TOOLS_DIR/bin" "$GO_BIN" install \
      "github.com/caddyserver/xcaddy/cmd/xcaddy@$XCADDY_VERSION"
    printf '%s\n' "$XCADDY_VERSION" >"$xcaddy_marker"
  fi
fi

build_id="caddy=$CADDY_VERSION xcaddy=$XCADDY_VERSION module=$FORWARDPROXY_MODULE@$FORWARDPROXY_VERSION=$FORWARDPROXY_REPLACEMENT@$FORWARDPROXY_COMMIT go=$GO_VERSION"
caddy_marker="$TOOLS_DIR/bin/caddy.build-id"
if [[ ! -x "$CADDY_BIN" || ! -f "$caddy_marker" || $(<"$caddy_marker") != "$build_id" ]]; then
  caddy_tmp="$TOOLS_DIR/bin/caddy.tmp.$$"
  trap 'rm -f -- "$caddy_tmp"' EXIT
  PATH="$(dirname "$GO_BIN"):$PATH" "$XCADDY_BIN" build "$CADDY_VERSION" --output "$caddy_tmp" \
    --with "$FORWARDPROXY_MODULE@$FORWARDPROXY_VERSION=$FORWARDPROXY_REPLACEMENT@$FORWARDPROXY_COMMIT"
  chmod 0755 "$caddy_tmp"
  mv "$caddy_tmp" "$CADDY_BIN"
  printf '%s\n' "$build_id" >"$caddy_marker"
  trap - EXIT
fi

if ! "$CADDY_BIN" list-modules --packages | rg \
  '^http\.handlers\.forward_proxy[[:space:]]+github\.com/caddyserver/forwardproxy'; then
  printf 'Caddy is missing the pinned http.handlers.forward_proxy module\n' >&2
  exit 1
fi
if [[ $("$CADDY_BIN" version) != "$CADDY_VERSION"* ]]; then
  printf 'unexpected Caddy version: %s\n' "$("$CADDY_BIN" version)" >&2
  exit 1
fi

{
  printf 'fixture_build=%s\n' "$build_id"
  printf 'go=%s\n' "$("$GO_BIN" version)"
  printf 'xcaddy=%s\n' "$("$XCADDY_BIN" version)"
  printf 'caddy=%s\n' "$("$CADDY_BIN" version)"
  "$CADDY_BIN" list-modules --packages | rg '^http\.handlers\.forward_proxy[[:space:]]'
} >"$STATE_ROOT/setup-diagnostics.txt"

printf 'fixture dependencies ready in %s\n' "$TOOLS_DIR"
