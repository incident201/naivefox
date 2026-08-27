#!/usr/bin/env bash

set -euo pipefail
umask 077

if [[ $# -lt 1 || ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK:-0} != 1 ||
      ${NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED:-0} == 1 ]]; then
  printf 'invalid isolated camouflage network invocation\n' >&2
  exit 2
fi

ip link set lo up
# The default Linux loopback MTU is 65536.  Even with segmentation offloads
# disabled, that permits host-local TCP segments far larger than an ordinary
# Ethernet packet and makes HTTP/2 packet indices depend on the fixture's
# write boundaries.  Give both reference Firefox and NaiveFox the same
# physical-network-sized MTU before either endpoint starts.
ip link set lo mtu 1500
loopback_mtu=$(ip -o link show dev lo | sed -n 's/.* mtu \([0-9][0-9]*\).*/\1/p')
if [[ $loopback_mtu != 1500 ]]; then
  printf 'isolated loopback MTU is not 1500: %s\n' "$loopback_mtu" >&2
  exit 1
fi
# Preserve individual TCP segments and QUIC datagrams for passive packet
# indices and private decryption.  This changes only the one-shot namespace
# loopback device.
ethtool -K lo gro off gso off tso off \
  tx-udp-segmentation off tx-gso-list off
offload_state=$(ethtool -k lo)
for feature in tcp-segmentation-offload generic-segmentation-offload \
               generic-receive-offload tx-udp-segmentation tx-gso-list; do
  if ! rg -q "^${feature}: off(?: |$)" <<<"$offload_state"; then
    printf 'isolated loopback offload remained enabled: %s\n' "$feature" >&2
    exit 1
  fi
done
ip link add naivefox0 type dummy
ip address add 192.0.2.1/32 dev naivefox0
ip link set naivefox0 up
ip route add default dev naivefox0

export NAIVEFOX_CAPTURE_ISOLATED_NETWORK_ENTERED=1
exec "$@"
