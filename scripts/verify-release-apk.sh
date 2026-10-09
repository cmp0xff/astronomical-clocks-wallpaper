#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
: "${ANDROID_HOME:?Set ANDROID_HOME to your Android SDK directory}"

source scripts/release-common.sh
validate_release_tag "${2:?Pass the release tag as argument 2}"
require_release_tools
require_release_certificate
apk=${1:?Pass the signed APK as argument 1}
mkdir -p build/reports

if [[ ! -f "$apk" ]]; then
  echo "Release APK not found at: $apk" >&2
  exit 1
fi

"$apkanalyzer" manifest print "$apk" > build/reports/apk-release-manifest.xml

python3 - "$apk" "$release_version" "$release_code" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

android = '{http://schemas.android.com/apk/res/android}'


def check(condition, message):
    if not condition:
        print(f'Release APK verification failed: {message}', file=sys.stderr)
        raise SystemExit(1)


manifest = ET.parse('build/reports/apk-release-manifest.xml').getroot()
check(
    manifest.get('package') == 'io.github.godaniya.astronomicalclockswallpaper',
    'unexpected application ID: release package must be io.github.godaniya.astronomicalclockswallpaper',
)

sdk = manifest.find('uses-sdk')
check(sdk is not None, 'missing uses-sdk element')
check(sdk.get(android + 'minSdkVersion') == '26', 'minSdkVersion must be 26')
check(sdk.get(android + 'targetSdkVersion') == '37', 'targetSdkVersion must be 37')

check(manifest.get(android + 'versionName') == sys.argv[2], 'release versionName mismatch')
check(manifest.get(android + 'versionCode') == sys.argv[3], 'release versionCode mismatch')

permission_names = [e.get(android + 'name') for e in manifest.iter() if e.tag.startswith('uses-permission')]
check(
    permission_names == ['android.permission.ACCESS_COARSE_LOCATION'],
    'unexpected permissions: expected only ACCESS_COARSE_LOCATION',
)

application = manifest.find('application')
check(application is not None, 'missing application element')
debuggable = application.get(android + 'debuggable')
check(
    debuggable is None or debuggable == 'false',
    f'release application must not be debuggable (debuggable={debuggable})',
)

service = application.find('service')
check(service is not None, 'missing service element')
check(
    service.get(android + 'name')
    == 'io.github.godaniya.astronomicalclockswallpaper.AstronomicalClocksWallpaperService',
    'service name mismatch',
)
check(service.get(android + 'exported') == 'true', 'service must be exported')
check(
    service.get(android + 'permission') == 'android.permission.BIND_WALLPAPER',
    'service must require BIND_WALLPAPER',
)

action = service.find('intent-filter/action')
check(action is not None, 'missing intent-filter action')
check(
    action.get(android + 'name') == 'android.service.wallpaper.WallpaperService',
    'missing wallpaper intent-filter action',
)

metadata = service.find('meta-data')
check(metadata is not None, 'missing meta-data element')
check(metadata.get(android + 'name') == 'android.service.wallpaper', 'meta-data name mismatch')
check(metadata.get(android + 'resource'), 'meta-data must declare a resource')
print('Release APK application ID, SDK levels, debuggable flag, permissions, and service verified.')

license_asset = 'assets/licenses/astronomy-engine-LICENSE.txt'
expected_license = Path('app/src/main', license_asset).read_bytes()
check(expected_license, 'bundled Astronomy Engine license must not be empty')
with ZipFile(sys.argv[1]) as archive:
    check(license_asset in archive.namelist(), 'missing Astronomy Engine license')
    check(
        archive.read(license_asset) == expected_license,
        'Astronomy Engine license differs from the complete bundled upstream notice',
    )
print('Release APK Astronomy Engine license verified against the bundled upstream notice.')
PY

"$apksigner" verify --verbose --print-certs "$apk" | tee build/reports/apk-release-signature.txt
signature=build/reports/apk-release-signature.txt
if ! grep -Fxq 'Verified using v2 scheme (APK Signature Scheme v2): true' "$signature"; then
  echo 'Verification failed: APK Signature Scheme v2 must be verified.' >&2
  exit 1
fi
if ! grep -Fxq 'Number of signers: 1' "$signature"; then
  echo 'Verification failed: exactly one signer is required.' >&2
  exit 1
fi
certificate=$(normalize_certificate_sha256 "$(sed -n 's/^Signer #1 certificate SHA-256 digest: //p' "$signature")")
if [[ "$certificate" != "$release_certificate" ]]; then
  echo 'Verification failed: signing certificate does not match RELEASE_CERT_SHA256.' >&2
  exit 1
fi
if grep -Eq 'Signer #1 certificate DN: .*CN=Android Debug' "$signature"; then
  echo 'Verification failed: Android Debug signing identity.' >&2
  exit 1
fi
shasum -a 256 "$apk" | tee build/reports/apk-release-sha256.txt
echo 'Release APK passed all verification checks.'
