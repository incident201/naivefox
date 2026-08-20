#!/usr/bin/env bash

set -euo pipefail
umask 077

repo_root=$(git rev-parse --show-toplevel)
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
objdir=${NAIVEFOX_OBJDIR:-${MOZ_OBJDIR:-$repo_root/obj-naivefox-minimal}}
objdir=$(realpath -m -- "$objdir")
output=${1:-${NAIVEFOX_FIREFOX_REFERENCE_DIR:-$objdir/naivefox-capture-reference}}
output=$(realpath -m -- "$output")
case "$output" in
  "$objdir"/*) ;;
  *)
    printf 'reference Firefox directory must remain below the object directory: %s\n' "$output" >&2
    exit 2
    ;;
esac

reference_manifest="$script_dir/firefox-reference-manifest"
if [[ ! -f $reference_manifest ]]; then
  printf 'committed Firefox reference manifest is missing: %s\n' \
    "$reference_manifest" >&2
  exit 1
fi
manifest_value() {
  awk -F= -v key="$1" '$1 == key { print substr($0, index($0, "=") + 1); exit }' \
    "$reference_manifest"
}
default_url=$(manifest_value url)
default_sha256=$(manifest_value archive_sha256)
default_version=$(manifest_value version)
[[ $default_url == https://* && $default_sha256 =~ ^[0-9a-f]{64}$ && \
  -n $default_version ]] || {
  printf 'invalid committed Firefox reference manifest\n' >&2
  exit 1
}
url=${NAIVEFOX_FIREFOX_URL:-$default_url}
expected_sha256=${NAIVEFOX_FIREFOX_SHA256:-$default_sha256}
expected_version=${NAIVEFOX_FIREFOX_VERSION:-$default_version}
[[ $expected_sha256 =~ ^[0-9a-f]{64}$ && -n $expected_version ]] || {
  printf 'invalid Firefox reference override\n' >&2
  exit 2
}

if [[ -x "$output/firefox" && -f "$output/REFERENCE-MANIFEST" ]]; then
  if grep -Fqx "url=$url" "$output/REFERENCE-MANIFEST" && \
    grep -Fqx "archive_sha256=$expected_sha256" "$output/REFERENCE-MANIFEST" && \
    grep -Fqx "version=$expected_version" "$output/REFERENCE-MANIFEST"; then
    printf '%s\n' "$output"
    exit 0
  fi
  printf 'cached Firefox reference does not match the pinned manifest: %s\n' \
    "$output" >&2
  exit 1
fi
if [[ -e "$output" ]]; then
  printf 'refusing to reuse incomplete reference directory: %s\n' "$output" >&2
  exit 1
fi

command -v curl >/dev/null || { printf 'curl is required\n' >&2; exit 1; }
command -v tar >/dev/null || { printf 'tar is required\n' >&2; exit 1; }
command -v sha256sum >/dev/null || { printf 'sha256sum is required\n' >&2; exit 1; }

mkdir -p "$objdir"
tmp=$(mktemp -d "$objdir/naivefox-firefox-reference.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT
archive="$tmp/firefox.tar.bz2"

effective_url=$(curl --fail --location --silent --show-error --retry 3 \
  --proto '=https' --tlsv1.2 "$url" -o "$archive" -w '%{url_effective}')
case "$effective_url" in
  *.tar.xz) tar -xJf "$archive" -C "$tmp" ;;
  *.tar.bz2) tar -xjf "$archive" -C "$tmp" ;;
  *.tar.gz|*.tgz) tar -xzf "$archive" -C "$tmp" ;;
  *)
    printf 'official Firefox download has an unsupported archive URL: %s\n' "$effective_url" >&2
    exit 1
    ;;
esac
extracted=$(find "$tmp" -mindepth 2 -maxdepth 2 -type f -name firefox -executable -printf '%h\n' -quit)
if [[ -z "$extracted" || ! -f "$extracted/libxul.so" || ! -f "$extracted/libssl3.so" ]]; then
  printf 'official Firefox archive did not contain a runnable Linux x86_64 tree\n' >&2
  exit 1
fi

version=$(LD_LIBRARY_PATH="$extracted" "$extracted/firefox" --version 2>/dev/null)
sha256=$(sha256sum "$archive" | awk '{print $1}')
if [[ $sha256 != "$expected_sha256" ]]; then
  printf 'Firefox reference archive SHA-256 mismatch: expected %s, got %s\n' \
    "$expected_sha256" "$sha256" >&2
  exit 1
fi
if [[ $version != "$expected_version" ]]; then
  printf 'Firefox reference version mismatch: expected %s, got %s\n' \
    "$expected_version" "$version" >&2
  exit 1
fi
mv -- "$extracted" "$output"
{
  printf 'source=Mozilla official download\n'
  printf 'url=%s\n' "$effective_url"
  printf 'archive_sha256=%s\n' "$sha256"
  printf 'version=%s\n' "$version"
  printf 'downloaded_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$output/REFERENCE-MANIFEST"
chmod 0600 "$output/REFERENCE-MANIFEST"
printf '%s\n' "$output"
