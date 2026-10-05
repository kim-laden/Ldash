#!/bin/sh
# Build the macOS installer. The Linux app is not started.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
DASH=$(CDPATH= cd -- "$ROOT/.." && pwd)
export PATH="$HOME/.dotnet:$PATH"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1

df -h "$ROOT" | awk 'NR==2 {print "disk", $4, "free"}'
python3 "$ROOT/make_icon.py"
python3 "$DASH/tests/test_machost_sensors.py"
dotnet build "$ROOT/LadenOps/LadenOps.csproj" -c Release --nologo

TOOLS="$HOME/.cache/laden-ops-mac-tools"
mkdir -p "$TOOLS"
if [ ! -x "$TOOLS/mkbom" ]; then
  rm -rf "$TOOLS/bomutils"
  curl -fL --retry 3 -o "$TOOLS/bomutils.tar.gz" "https://github.com/hogliux/bomutils/archive/refs/heads/master.tar.gz"
  tar -xzf "$TOOLS/bomutils.tar.gz" -C "$TOOLS"
  mv "$TOOLS"/bomutils-* "$TOOLS/bomutils"
  make -C "$TOOLS/bomutils"
  cp "$TOOLS/bomutils/build/bin/mkbom" "$TOOLS/mkbom" 2>/dev/null || cp "$TOOLS/bomutils/mkbom" "$TOOLS/mkbom"
fi

PY_TAG=20261001
PY_VER=3.12.15
PY_BASE="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}"
mkdir -p "$ROOT/build/cache"
fetch() {
  url=$1
  dest=$2
  if [ ! -s "$dest" ]; then
    curl -fL --retry 3 -o "$dest.part" "$url"
    mv "$dest.part" "$dest"
  fi
}
fetch "$PY_BASE/cpython-${PY_VER}+${PY_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz" \
  "$ROOT/build/cache/python-arm64.tar.gz"
fetch "$PY_BASE/cpython-${PY_VER}+${PY_TAG}-x86_64-apple-darwin-install_only_stripped.tar.gz" \
  "$ROOT/build/cache/python-x64.tar.gz"
fetch "https://curl.se/ca/cacert.pem" "$ROOT/build/cache/cacert.pem"

publish() {
  rid=$1
  dest=$2
  rm -rf "$dest"
  dotnet publish "$ROOT/LadenOps/LadenOps.csproj" -c Release -r "$rid" --self-contained true \
    -p:DebugType=none -p:DebugSymbols=false \
    -o "$dest"
}
publish osx-arm64 "$ROOT/build/publish-arm64"
publish osx-x64 "$ROOT/build/publish-x64"

assemble() {
  arch=$1
  rid_py=$2
  publish_dir=$3
  dest="$ROOT/build/apps/$arch/Laden Ops.app"
  rm -rf "$ROOT/build/apps/$arch"
  mac="$dest/Contents/MacOS"
  res="$dest/Contents/Resources"
  mkdir -p "$mac" "$res" "$mac/runtime/$arch"
  cp "$ROOT/Info.plist" "$dest/Contents/Info.plist"
  cp "$ROOT/laden.icns" "$res/laden.icns"
  cp "$ROOT/uninstall.command" "$res/uninstall.command"
  chmod 755 "$res/uninstall.command"
  cp -a "$publish_dir"/. "$mac/"
  find "$mac" -name '*.pdb' -delete
  tar -xzf "$ROOT/build/cache/$rid_py" -C "$mac/runtime/$arch" --strip-components=1
  cp "$DASH/windows/serve.py" "$mac/runtime/serve.py"
  cp "$DASH/host.py" "$mac/runtime/host.py"
  cp "$DASH/machost.py" "$mac/runtime/machost.py"
  cp "$ROOT/build/cache/cacert.pem" "$mac/runtime/cacert.pem"
  mkdir -p "$mac/web"
  cp "$DASH/web/index.html" "$DASH/web/app.js" "$DASH/web/vault.js" "$DASH/web/journal.js" \
    "$DASH/web/economy.js" "$DASH/web/styles.css" "$mac/web/"
  chmod 755 "$mac/LadenOps"
  if [ -d "$mac/runtime/$arch/bin" ]; then
    chmod 755 "$mac/runtime/$arch/bin/python3" "$mac/runtime/$arch/bin/python3.12" 2>/dev/null || true
  fi
}

assemble arm64 python-arm64.tar.gz "$ROOT/build/publish-arm64"
assemble x64 python-x64.tar.gz "$ROOT/build/publish-x64"
RCODE="$HOME/.cache/laden-ops-mac-tools/apple-codesign-0.29.0-x86_64-unknown-linux-musl/rcodesign"
if [ ! -x "$RCODE" ]; then
  curl -fL --retry 3 -o "$HOME/.cache/laden-ops-mac-tools/rcodesign.tar.gz" \
    "https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign/0.29.0/apple-codesign-0.29.0-x86_64-unknown-linux-musl.tar.gz"
  tar -xzf "$HOME/.cache/laden-ops-mac-tools/rcodesign.tar.gz" -C "$HOME/.cache/laden-ops-mac-tools"
fi
python3 "$ROOT/sign_machos.py" "$RCODE" \
  "$ROOT/build/apps/arm64/Laden Ops.app" \
  "$ROOT/build/apps/x64/Laden Ops.app"
python3 "$ROOT/verify_bundle.py" "$ROOT/build/apps/arm64/Laden Ops.app" arm64
python3 "$ROOT/verify_bundle.py" "$ROOT/build/apps/x64/Laden Ops.app" x64

mkdir -p "$ROOT/dist"
python3 "$ROOT/pack.py" \
  "$ROOT/build/apps/arm64/Laden Ops.app" \
  "$ROOT/build/apps/x64/Laden Ops.app" \
  "$ROOT/dist/Ldash-1.0-macOS-Setup.pkg" \
  "$ROOT/postinstall" \
  "$TOOLS/mkbom" \
  "$ROOT/build/pkgwork"
(
  cd "$ROOT/dist"
  sha256sum Ldash-1.0-macOS-Setup.pkg > SHA256SUMS
)
ls -lh "$ROOT/dist/Ldash-1.0-macOS-Setup.pkg"
