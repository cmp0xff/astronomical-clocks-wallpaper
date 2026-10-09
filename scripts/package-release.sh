#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Package an already signed APK. Build and signing are separate operations.
set -euo pipefail
source scripts/release-common.sh
validate_release_tag "${1:?Pass the release tag}"
apk=${2:?Pass the signed APK}
scripts/verify-release-apk.sh "$apk" "$1"
mkdir -p build/dist
name="AstronomicalClocksWallpaper-v${release_version}"
cp "$apk" "build/dist/$name.apk"
(
  cd build/dist
  shasum -a 256 "$name.apk" > "$name-sha256sums.txt"
)
require_release_certificate
printf '%s\n' "$release_certificate" > "build/dist/$name-signing-cert-sha256.txt"
echo "Verified release assets: build/dist/$name"
