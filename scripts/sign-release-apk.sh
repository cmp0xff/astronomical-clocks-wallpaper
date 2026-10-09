#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
source scripts/release-common.sh
require_release_tools
require_release_certificate
: "${RELEASE_KEYSTORE_PASSWORD:?Set RELEASE_KEYSTORE_PASSWORD}"
: "${RELEASE_KEY_ALIAS:?Set RELEASE_KEY_ALIAS}"
: "${RELEASE_KEY_PASSWORD:?Set RELEASE_KEY_PASSWORD}"
input=${1:?Pass the unsigned APK}
output=${2:?Pass the signed APK output}
[[ -f "$input" && "$input" != "$output" ]] || { echo 'ERROR: Invalid signing paths.' >&2; exit 1; }
umask 077
signing_dir=$(mktemp -d)
trap 'rm -rf "$signing_dir"' EXIT
if [[ -n "${RELEASE_KEYSTORE_BASE64:-}" ]]; then
  printf '%s' "$RELEASE_KEYSTORE_BASE64" | base64 --decode > "$signing_dir/release.jks"
else
  : "${RELEASE_KEYSTORE_PATH:?Set RELEASE_KEYSTORE_PATH or RELEASE_KEYSTORE_BASE64}"
  cp "$RELEASE_KEYSTORE_PATH" "$signing_dir/release.jks"
fi
chmod 600 "$signing_dir/release.jks"
"$zipalign" -P 16 -f 4 "$input" "$signing_dir/aligned.apk"
"$apksigner" sign --ks "$signing_dir/release.jks" --ks-key-alias "$RELEASE_KEY_ALIAS" \
  --ks-pass env:RELEASE_KEYSTORE_PASSWORD --key-pass env:RELEASE_KEY_PASSWORD \
  --v2-signing-enabled true --out "$output" "$signing_dir/aligned.apk"
