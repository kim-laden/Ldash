#!/bin/sh
# Build the release APK signed by Laden AS. Does not install it onto a phone.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
export JAVA_HOME="${JAVA_HOME:-$HOME/.cache/laden-ops-android/jdk}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/.cache/laden-ops-android/sdk}"
export PATH="$JAVA_HOME/bin:$PATH"
SIGN_DIR="$HOME/.config/laden-ops/signing"
mkdir -p "$SIGN_DIR"
chmod 700 "$SIGN_DIR"
umask 077
KS="$SIGN_DIR/laden-as.p12"
OLD_KS="$SIGN_DIR/laden-as.jks"
PASSFILE="$SIGN_DIR/laden-as.pass"
if [ ! -f "$KS" ]; then
  if [ ! -f "$PASSFILE" ]; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(24), end="")' > "$PASSFILE"
    chmod 600 "$PASSFILE"
  fi
  if [ -f "$OLD_KS" ]; then
    keytool -importkeystore -noprompt \
      -srckeystore "$OLD_KS" -srcstoretype JKS -srcstorepass:file "$PASSFILE" \
      -destkeystore "$KS" -deststoretype PKCS12 -deststorepass:file "$PASSFILE"
  else
    keytool -genkeypair -alias laden-as -keyalg RSA -keysize 3072 -validity 9125 \
      -dname "CN=Laden AS, O=Laden AS, C=NO" \
      -keystore "$KS" -storetype PKCS12 \
      -storepass:file "$PASSFILE" -keypass:file "$PASSFILE"
  fi
  chmod 600 "$KS"
fi
umask 022
export LDASH_KEYSTORE="$KS"
LDASH_KEYSTORE_PASS=$(cat "$PASSFILE")
export LDASH_KEYSTORE_PASS
export LDASH_KEY_PASS="$LDASH_KEYSTORE_PASS"
python3 "$ROOT/make_icon.py"
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > "$ROOT/local.properties"
cd "$ROOT"
"$HOME/.cache/laden-ops-android/gradle/bin/gradle" --no-daemon assembleRelease
mkdir -p "$ROOT/dist"
cp -f "$ROOT/app/build/outputs/apk/release/app-release.apk" "$ROOT/dist/Ldash-1.0-Android.apk"
unset LDASH_KEYSTORE_PASS LDASH_KEY_PASS
(
  cd "$ROOT/dist"
  sha256sum Ldash-1.0-Android.apk > SHA256SUMS
)
APKSIGNER=$(find "$ANDROID_HOME/build-tools" -name apksigner -type f | sort | tail -1)
FP=$("$APKSIGNER" verify --print-certs "$ROOT/dist/Ldash-1.0-Android.apk" | awk -F': ' '/SHA-256 digest/{print $2; exit}')
printf '%s\n' "Publisher: Laden AS" "Package: org.laden.opsdash" "Certificate SHA-256: $FP" > "$ROOT/dist/PUBLISHER.txt"
chmod 644 "$ROOT/dist/Ldash-1.0-Android.apk" "$ROOT/dist/SHA256SUMS" "$ROOT/dist/PUBLISHER.txt"
ls -l "$ROOT/dist/Ldash-1.0-Android.apk"
echo "Publisher certificate SHA-256: $FP"
