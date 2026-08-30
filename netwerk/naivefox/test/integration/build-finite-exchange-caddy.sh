#!/usr/bin/env bash

set -euo pipefail
umask 077
source "$(cd "$(dirname "$0")" && pwd)/common.sh"
init_paths
"$INTEGRATION_DIR/setup.sh" >/dev/null
work=$(mktemp -d "$STATE_ROOT/finite-exchange-build.XXXXXX")
printf 'finite Caddy workdir: %s\n' "$work"
module="$TOOLS_DIR/go-module-cache/github.com/klzgrad/forwardproxy@v0.0.0-20250118002110-d62c80d3dd2c"
[[ -d "$module" ]]
cp -a "$module" "$work/forwardproxy"
chmod -R u+w "$work/forwardproxy"
cp "$INTEGRATION_DIR/finite_exchanges/finite.go" \
  "$INTEGRATION_DIR/finite_exchanges/finite_test.go" "$work/forwardproxy/"
patch --batch --fuzz=0 -d "$work/forwardproxy" -p1 \
  <"$INTEGRATION_DIR/finite_exchanges/forwardproxy.patch" >"$work/patch.log"
export PATH="$TOOLS_DIR/$GO_VERSION/bin:$PATH"
export GOCACHE="$TOOLS_DIR/go-build-cache"
export GOMODCACHE="$TOOLS_DIR/go-module-cache"
(
  cd "$work/forwardproxy"
  # The upstream ACL tests need two DNS names, but no external connections.
  unshare --net --mount --propagation private --mount-proc bash -c \
    'ip link set lo up && mount --bind "$1" /etc/hosts && exec go test -race ./...' \
    bash "$INTEGRATION_DIR/finite_exchanges/test-hosts" >"$work/tests.log" 2>&1
  "$TOOLS_DIR/bin/xcaddy" build "$CADDY_VERSION" \
    --output "$work/caddy.finite" \
    --with "$FORWARDPROXY_MODULE@$FORWARDPROXY_VERSION=$work/forwardproxy" \
    >"$work/build.log" 2>&1
)
sha256sum "$work/caddy.finite"
printf 'finite Caddy tests and build: %s\n' "$work"
