#!/bin/sh
# Build LadenOpsSetup.exe. Runs on Linux and produces a Windows installer.
set -eu
cd "$(dirname "$0")"
ROOT="$(pwd)"
DOTNET="${DOTNET:-$HOME/.dotnet/dotnet}"
STAGE="$ROOT/build/payload"
DIST="$ROOT/dist"
PY_VER="${PY_VER:-3.12.8}"
PY_ZIP="python-$PY_VER-embed-amd64.zip"
PY_URL="https://www.python.org/ftp/python/$PY_VER/$PY_ZIP"
WV_URL="https://go.microsoft.com/fwlink/p/?LinkId=2124703"

if [ ! -x "$DOTNET" ]; then
  echo "Installing the .NET SDK into $HOME/.dotnet"
  curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
  bash /tmp/dotnet-install.sh --channel 8.0 --install-dir "$HOME/.dotnet"
fi

python3 "$ROOT/make_icon.py"
export DOTNET_ROOT="$HOME/.dotnet"
export PATH="$HOME/.dotnet:$PATH"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1

"$DOTNET" publish "$ROOT/LadenOps/LadenOps.csproj" -c Release -r win-x64 --self-contained true -o "$ROOT/build/publish"

rm -rf "$STAGE"
mkdir -p "$STAGE/runtime" "$STAGE/web" "$STAGE/redist" "$DIST"
cp -a "$ROOT/build/publish/." "$STAGE/"
cp -a "$ROOT/../web/." "$STAGE/web/"
cp "$ROOT/laden.ico" "$STAGE/laden.ico"
cp "$ROOT/serve.py" "$ROOT/../host.py" "$ROOT/../winhost.py" "$STAGE/runtime/"

if [ ! -f "$ROOT/build/$PY_ZIP" ]; then
  curl -fL --retry 3 -o "$ROOT/build/$PY_ZIP" "$PY_URL"
fi
unzip -q -o "$ROOT/build/$PY_ZIP" -d "$STAGE/runtime"
if [ ! -f "$STAGE/redist/MicrosoftEdgeWebview2Setup.exe" ]; then
  curl -fL --retry 3 -o "$STAGE/redist/MicrosoftEdgeWebview2Setup.exe" "$WV_URL"
fi

# Keep the embeddable path list, and make sure the shipped scripts stay importable.
cat > "$STAGE/runtime/python312._pth" <<'EOF'
python312.zip
.
import site
EOF

WINEPREFIX="${WINEPREFIX_LADEN:-$HOME/.cache/laden-ops-wine}"
export WINEPREFIX
mkdir -p "$WINEPREFIX"
if [ ! -x "$WINEPREFIX/drive_c/innosetup/ISCC.exe" ]; then
  echo "Installing Inno Setup into the build prefix"
  curl -fL --retry 3 -o "$ROOT/build/innosetup.exe" "https://github.com/jrsoftware/issrc/releases/download/is-6_4_0/innosetup-6.4.0.exe"
  wine "$ROOT/build/innosetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=C:\\innosetup
fi
wine "$WINEPREFIX/drive_c/innosetup/ISCC.exe" "Z:${ROOT}/installer.iss"
(
  cd "$DIST"
  sha256sum Ldash-1.0-Windows-Setup.exe > SHA256SUMS
)
echo "Installer: $DIST/Ldash-1.0-Windows-Setup.exe"
