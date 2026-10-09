#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Exercise the real pinned signing tools with disposable keys, never production inputs.
set -euo pipefail
source scripts/release-common.sh
require_release_tools
unsigned=${1:-app/build/outputs/apk/release/app-release-unsigned.apk}
debug=${2:-app/build/outputs/apk/debug/app-debug.apk}
[[ -f "$unsigned" && -f "$debug" ]] || { echo 'Build both APK variants first.' >&2; exit 1; }
umask 077
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT
export RELEASE_KEYSTORE_PATH="$test_dir/test.jks"
export RELEASE_KEYSTORE_PASSWORD=disposable-test-password
export RELEASE_KEY_PASSWORD=disposable-test-password
export RELEASE_KEY_ALIAS=release-test
unset RELEASE_KEYSTORE_BASE64
keytool -genkeypair -keystore "$RELEASE_KEYSTORE_PATH" -storepass:env RELEASE_KEYSTORE_PASSWORD \
  -keypass:env RELEASE_KEY_PASSWORD -alias "$RELEASE_KEY_ALIAS" -keyalg RSA -keysize 2048 \
  -validity 1 -dname 'CN=Disposable Release Test' > "$test_dir/keytool.txt" 2>&1
export RELEASE_CERT_SHA256
RELEASE_CERT_SHA256=$(keytool -exportcert -keystore "$RELEASE_KEYSTORE_PATH" \
  -storepass:env RELEASE_KEYSTORE_PASSWORD -alias "$RELEASE_KEY_ALIAS" | shasum -a 256 | cut -d ' ' -f 1)
expect_failure() {
  local label="$1"
  shift
  if "$@" > "$test_dir/failure.txt" 2>&1; then
    echo "ERROR: Unexpected success: $label" >&2
    exit 1
  fi
  echo "Rejected: $label"
}
for tag in '0.2.0' 'v0.2' 'v01.2.0' 'v0.2.0/escape' 'v0.2.0;echo injected' 'v0.2.0-' 'v0.3.0'; do
  expect_failure "invalid/mismatched tag $tag" validate_release_tag "$tag"
done
# The future prerelease form is syntactically valid but must match its declaration.
expect_failure 'mismatched prerelease' validate_release_tag v0.2.0-beta.1
for variable in RELEASE_KEYSTORE_PATH RELEASE_KEYSTORE_PASSWORD RELEASE_KEY_ALIAS RELEASE_KEY_PASSWORD RELEASE_CERT_SHA256; do
  expect_failure "missing $variable" env -u "$variable" scripts/sign-release-apk.sh "$unsigned" "$test_dir/missing.apk"
done
expect_failure 'missing pinned tools' env ANDROID_HOME="$test_dir/no-sdk" scripts/verify-release-apk.sh "$unsigned" v0.2.0
scripts/sign-release-apk.sh "$unsigned" "$test_dir/signed.apk"
scripts/package-release.sh v0.2.0 "$test_dir/signed.apk"
(cd build/dist && shasum -a 256 -c AstronomicalClocksWallpaper-v0.2.0-sha256sums.txt)
[[ $(cat build/dist/AstronomicalClocksWallpaper-v0.2.0-signing-cert-sha256.txt) == "$RELEASE_CERT_SHA256" ]]
cmp "$test_dir/signed.apk" build/dist/AstronomicalClocksWallpaper-v0.2.0.apk
# The shared fingerprint normalizer and the configured-fingerprint comparison
# must accept colon-separated uppercase as well as contiguous lowercase form.
[[ $(normalize_certificate_sha256 'AA:BB:CC:dd') == 'aabbccdd' ]]
colon_certificate=$(printf '%s' "$RELEASE_CERT_SHA256" | sed 's/\(..\)/\1:/g; s/:$//' | tr '[:lower:]' '[:upper:]')
env RELEASE_CERT_SHA256="$colon_certificate" scripts/verify-release-apk.sh "$test_dir/signed.apk" v0.2.0
# A release versionCode must strictly exceed every other v* tag's declaration.
require_increasing_version_code 2 1
expect_failure 'versionCode equal to the highest released' require_increasing_version_code 2 2
expect_failure 'lowered versionCode' require_increasing_version_code 2 3
fixture="$test_dir/version-code-fixture"
mkdir -p "$fixture/app"
git -C "$fixture" init -q
printf '    versionCode = 1\n' > "$fixture/app/build.gradle.kts"
git -C "$fixture" add app/build.gradle.kts
git -C "$fixture" -c commit.gpgsign=false -c user.name='Release Test' \
  -c user.email='release-test@example.invalid' commit -q -m fixture
git -C "$fixture" -c tag.gpgsign=false tag v0.1.0
[[ $(cd "$fixture" && highest_prior_version_code v0.2.0) == 1 ]]
[[ $(cd "$fixture" && highest_prior_version_code v0.1.0) == 0 ]]
expect_failure 'unsigned APK' scripts/verify-release-apk.sh "$unsigned" v0.2.0
expect_failure 'wrong certificate' env RELEASE_CERT_SHA256="$(printf '%064d' 0)" scripts/verify-release-apk.sh "$test_dir/signed.apk" v0.2.0
expect_failure 'debug artifact' scripts/verify-release-apk.sh "$debug" v0.2.0
cp "$test_dir/signed.apk" "$test_dir/tampered.apk"
python3 - "$test_dir/tampered.apk" <<'PY'
import sys
from zipfile import ZipFile
with ZipFile(sys.argv[1], 'a') as archive:
    archive.writestr('assets/tampered.txt', 'changed after signing')
PY
expect_failure 'tampered APK' scripts/verify-release-apk.sh "$test_dir/tampered.apk" v0.2.0
"$apksigner" sign --ks "$RELEASE_KEYSTORE_PATH" --ks-key-alias "$RELEASE_KEY_ALIAS" \
  --ks-pass env:RELEASE_KEYSTORE_PASSWORD --key-pass env:RELEASE_KEY_PASSWORD \
  --v1-signing-enabled true --v2-signing-enabled false --v3-signing-enabled false \
  --out "$test_dir/v1.apk" "$unsigned"
expect_failure 'v1-only signature' scripts/verify-release-apk.sh "$test_dir/v1.apk" v0.2.0
keytool -genkeypair -keystore "$RELEASE_KEYSTORE_PATH" -storepass:env RELEASE_KEYSTORE_PASSWORD \
  -keypass:env RELEASE_KEY_PASSWORD -alias second -keyalg RSA -keysize 2048 \
  -validity 1 -dname 'CN=Second Disposable Signer' > "$test_dir/second.txt" 2>&1
"$apksigner" sign --ks "$RELEASE_KEYSTORE_PATH" --ks-key-alias "$RELEASE_KEY_ALIAS" \
  --ks-pass env:RELEASE_KEYSTORE_PASSWORD --key-pass env:RELEASE_KEY_PASSWORD \
  --next-signer --ks "$RELEASE_KEYSTORE_PATH" --ks-key-alias second \
  --ks-pass env:RELEASE_KEYSTORE_PASSWORD --key-pass env:RELEASE_KEY_PASSWORD \
  --v3-signing-enabled false --out "$test_dir/multiple.apk" "$unsigned"
expect_failure 'multiple signers' scripts/verify-release-apk.sh "$test_dir/multiple.apk" v0.2.0
# CI signs through RELEASE_KEYSTORE_BASE64, which must match the RELEASE_KEYSTORE_PATH result.
RELEASE_KEYSTORE_BASE64=$(base64 < "$RELEASE_KEYSTORE_PATH" | tr -d '\n')
env -u RELEASE_KEYSTORE_PATH RELEASE_KEYSTORE_BASE64="$RELEASE_KEYSTORE_BASE64" \
  scripts/sign-release-apk.sh "$unsigned" "$test_dir/signed-base64.apk"
cmp "$test_dir/signed.apk" "$test_dir/signed-base64.apk"
scripts/package-release.sh v0.2.0 "$test_dir/signed-base64.apk"
(cd build/dist && shasum -a 256 -c AstronomicalClocksWallpaper-v0.2.0-sha256sums.txt)
mkdir "$test_dir/temporary"
expect_failure 'invalid keystore encoding' env TMPDIR="$test_dir/temporary" RELEASE_KEYSTORE_BASE64='invalid encoding!' \
  scripts/sign-release-apk.sh "$unsigned" "$test_dir/invalid.apk"
[[ -z $(find "$test_dir/temporary" -mindepth 1 -print) ]] || { echo 'ERROR: Temporary keystore not cleaned up.' >&2; exit 1; }

# Intercept only manifest decoding to exercise metadata contracts against a valid signature.
mkdir -p "$test_dir/sdk/cmdline-tools/23.0/bin" "$test_dir/sdk/build-tools"
cp "$ANDROID_HOME/cmdline-tools/23.0/source.properties" "$test_dir/sdk/cmdline-tools/23.0/"
ln -s "$ANDROID_HOME/build-tools/36.0.0" "$test_dir/sdk/build-tools/36.0.0"
"$apkanalyzer" manifest print "$test_dir/signed.apk" > "$test_dir/original.xml"
cat > "$test_dir/sdk/cmdline-tools/23.0/bin/apkanalyzer" <<'STUB'
#!/usr/bin/env bash
cat "$RELEASE_TEST_MANIFEST"
STUB
chmod +x "$test_dir/sdk/cmdline-tools/23.0/bin/apkanalyzer"
export RELEASE_TEST_MANIFEST="$test_dir/manifest.xml"
for field in versionName versionCode package minSdkVersion targetSdkVersion permission service debuggable; do
  python3 - "$test_dir/original.xml" "$RELEASE_TEST_MANIFEST" "$field" <<'PY'
import sys
import xml.etree.ElementTree as ET
android = '{http://schemas.android.com/apk/res/android}'
root = ET.parse(sys.argv[1]).getroot()
field = sys.argv[3]
if field in ('versionName', 'versionCode'):
    root.set(android + field, '999')
elif field == 'package':
    root.set('package', 'invalid.package')
elif field in ('minSdkVersion', 'targetSdkVersion'):
    root.find('uses-sdk').set(android + field, '1')
elif field == 'debuggable':
    root.find('application').set(android + field, 'true')
elif field == 'permission':
    root.find('uses-permission').set(android + 'name', 'android.permission.INTERNET')
else:
    root.find('application/service').set(android + 'permission', 'invalid.permission')
ET.ElementTree(root).write(sys.argv[2])
PY
  expect_failure "incorrect $field" env ANDROID_HOME="$test_dir/sdk" scripts/verify-release-apk.sh "$test_dir/signed.apk" v0.2.0
done
for license in missing changed; do
  python3 - "$unsigned" "$test_dir/license.apk" "$license" <<'PY'
import sys
from zipfile import ZipFile
asset = 'assets/licenses/astronomy-engine-LICENSE.txt'
with ZipFile(sys.argv[1]) as source, ZipFile(sys.argv[2], 'w') as output:
    for entry in source.infolist():
        if entry.filename == asset:
            if sys.argv[3] == 'changed':
                output.writestr(entry, b'incomplete license')
        else:
            output.writestr(entry, source.read(entry))
PY
  scripts/sign-release-apk.sh "$test_dir/license.apk" "$test_dir/license-signed.apk"
  expect_failure "$license license" scripts/verify-release-apk.sh "$test_dir/license-signed.apk" v0.2.0
done
echo 'Release signing/package regression checks passed (disposable identity only).'
