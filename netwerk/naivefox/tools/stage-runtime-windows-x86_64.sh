#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
objdir=${MOZ_OBJDIR:-$repo_root/obj-naivefox-windows-x86_64}
src=$objdir/dist/bin
out=${1:-$objdir/naivefox-package/naivefox-windows-x86_64}

objdir=$(realpath -m -- "$objdir")
out=$(realpath -m -- "$out")
case "$out" in
  "$objdir"/*) ;;
  *) echo "output must remain under the object directory" >&2; exit 2 ;;
esac
test -d "$src" || { echo "missing Windows dist/bin: $src" >&2; exit 2; }
test ! -e "$out" || { echo "refusing to overwrite $out" >&2; exit 2; }

tmp=$(mktemp -d "$objdir/naivefox-windows-package.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT
pkg=$tmp/naivefox-windows-x86_64
mkdir -p "$pkg"

required=(
  naivefox.exe xul.dll mozglue.dll nss3.dll softokn3.dll freebl3.dll
  dependentlibs.list application.ini chrome.manifest greprefs.js
)
for name in "${required[@]}"; do
  test -f "$src/$name" || { echo "missing staged input: $name" >&2; exit 1; }
  cp -aL -- "$src/$name" "$pkg/$name"
done

for dir in chrome localization; do
  test -d "$src/$dir" || { echo "missing staged directory: $dir" >&2; exit 1; }
  cp -aL -- "$src/$dir" "$pkg/$dir"
done

# The Mozilla build links the Microsoft CRT dynamically. Keep it optional so
# the package can use a system-installed VC++ redistributable; CI/package
# builders may set NAIVEFOX_VC_RUNTIME_DIR to bundle matching x64 DLLs.
if test -n "${NAIVEFOX_VC_RUNTIME_DIR:-}"; then
  for name in msvcp140.dll vcruntime140.dll vcruntime140_1.dll; do
    test -f "$NAIVEFOX_VC_RUNTIME_DIR/$name" || {
      echo "missing VC runtime DLL: $NAIVEFOX_VC_RUNTIME_DIR/$name" >&2
      exit 1
    }
    cp -aL -- "$NAIVEFOX_VC_RUNTIME_DIR/$name" "$pkg/$name"
  done
fi

cat > "$pkg/run-naivefox.cmd" <<'EOF'
@echo off
setlocal
set "HERE=%~dp0"
set "PATH=%HERE%;%PATH%"
"%HERE%naivefox.exe" %*
exit /b %ERRORLEVEL%
EOF

test -z "$(find "$pkg" -type l -print -quit)"
test -z "$(find "$pkg" \( -name '*.log' -o -name '*.pcap' -o -name '*.pcapng' -o -iname '*keylog*' -o -name 'cert9.db' -o -name 'key4.db' \) -print -quit)"
chmod 0755 "$pkg/run-naivefox.cmd"
mkdir -p "$(dirname -- "$out")"
mv -- "$pkg" "$out"
trap - EXIT
rm -rf -- "$tmp"
printf 'staged %s (%s)\n' "$out" "$(du -sh "$out" | cut -f1)"
