#!/bin/sh
# Source archive Linux users can download and install.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PARENT=$(dirname "$ROOT")
NAME=$(basename "$ROOT")
OUT="$ROOT/linux/dist"
mkdir -p "$OUT"
tar -C "$PARENT" \
  --exclude="$NAME/android/app/build" \
  --exclude="$NAME/android/.gradle" \
  --exclude="$NAME/android/local.properties" \
  --exclude="$NAME/android/dist" \
  --exclude="$NAME/windows/build" \
  --exclude="$NAME/windows/dist" \
  --exclude="$NAME/windows/LadenOps/bin" \
  --exclude="$NAME/windows/LadenOps/obj" \
  --exclude="$NAME/macos/build" \
  --exclude="$NAME/macos/dist" \
  --exclude="$NAME/macos/LadenOps/bin" \
  --exclude="$NAME/macos/LadenOps/obj" \
  --exclude="$NAME/linux/dist" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".git" \
  -czf "$OUT/Ldash-1.0-Linux.tar.gz" "$NAME"
(
  cd "$OUT"
  sha256sum Ldash-1.0-Linux.tar.gz > SHA256SUMS
)
ls -lh "$OUT/Ldash-1.0-Linux.tar.gz"
