#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
  echo 'The desktop DMG smoke test requires macOS.' >&2
  exit 1
fi
if [[ "$#" != 1 || ! -f "$1" || "$1" != *.dmg ]]; then
  echo 'Supply exactly one existing Gideon DMG.' >&2
  exit 1
fi
smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/gideon-dmg-smoke.XXXXXX")
mount_dir="$smoke_dir/mount"
mkdir "$mount_dir"
mounted=0
cleanup() {
  if [[ "$mounted" == 1 ]]; then
    hdiutil detach "$mount_dir" -quiet || return 1
  fi
  rm -rf "$smoke_dir"
}
trap cleanup EXIT
hdiutil attach "$1" -readonly -nobrowse -mountpoint "$mount_dir" -quiet
mounted=1
app="$mount_dir/Gideon.app"
shell_binary="$app/Contents/MacOS/Gideon"
backend="$app/Contents/Resources/backend-dist/gideon-backend/gideon-backend"
test -f "$app/Contents/Info.plist"
test -f "$app/Contents/Resources/app/package.json"
test -x "$shell_binary"
test -x "$backend"
test "$(lipo -archs "$shell_binary")" = arm64
test "$(lipo -archs "$backend")" = arm64
export GIDEON_HOME="$smoke_dir/home"
export GIDEON_WORKSPACE="$smoke_dir/workspace"
mkdir "$GIDEON_HOME" "$GIDEON_WORKSPACE"
test "$(ELECTRON_RUN_AS_NODE=1 "$shell_binary" -p 'process.arch')" = arm64
"$backend" --version

python3 "$(dirname "$0")/smoke_packaged_desktop.py" "$backend"
