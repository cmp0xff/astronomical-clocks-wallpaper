# Location-unavailable guidance with the network provider disabled (#128)

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew qualityGate :app:assembleDebug`, APK SHA-256
`4c8c0329d5ed2914da688ccff5b58420146fce80844c20359f48cc4fc60fd43b`. The `app/src/main` tree it
compiles is the one at `3ff5bc36fc32d54ec6416cff0f51ea3468ae4298`, so that revision identifies the
application sources under test; the report's own commit adds documentation only. The artifact was
installed in place with `adb install -r` over the previous debug build.

Physical device running Android 16 (API 36). Vendor, model, serial, and firmware build are withheld
per the device-privacy policy. The saved site carried through the pass is the manual published
city-centre reference `50.08, 14.42`, `Europe/Prague`, not a personal location.

## What was measured and why

#128 acceptance criterion 3 asks for a physical-device record of what the app shows with the
network provider disabled, the setting restored afterwards, and any case that could not be
exercised stated as such. The host tests added in the same push assert that the disabled-provider
branch maps to `R.string.location_provider_disabled`, but they run under Robolectric: what they
establish is the resource lookup, not what the device displays, and a resource that resolves can
still be wrong on screen.

## Method

The network provider was disabled by turning device location off — `settings put secure
location_mode 0` — rather than by removing a provider, so that the app still registered a provider
and reached the enabled-state check it actually tests. The resulting state was read back from
`dumpsys location` before the tap. The intended action was driven with `input tap` on the
`use_current_location` button's own `uiautomator dump` bounds (centre `540, 836`), and the toast
was read from `dumpsys notification --noredact`, whose `Toast Queue` section carries the exact
string, rather than inferred from a screenshot alone. The screen was held on for the pass with
`svc power stayon usb` and `settings put system screen_off_pocket 0`, and both were reverted
afterwards.

## Observed results

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-12 | build, verify, install | `./gradlew qualityGate :app:assembleDebug` passed (93 actionable tasks); `scripts/verify-apk.sh` verified the application id, SDK levels, debug flag, permissions, and wallpaper declaration, and reported the APK signed under the v2 scheme with the Android debug certificate; `adb install -r` replaced the previous debug build in place |
| 2026-10-12 | before: location state | `settings get secure location_mode` → `3`; `cmd location is-location-enabled` → `true` |
| 2026-10-12 | before: saved site | Settings screen read `50.0800, 14.4200 (manual)` / `Timezone: Europe/Prague` |
| 2026-10-12 | after: location state | `settings get secure location_mode` → `0`; `cmd location is-location-enabled` → `false`; `dumpsys location` showed `network provider: service: ProviderRequest[OFF]` with `user 0: enabled=false`, so `isProviderEnabled(NETWORK_PROVIDER)` is false |
| 2026-10-12 | after: app permission | `ACCESS_COARSE_LOCATION: granted=true` for the app on user 0, so the tap reached the acquisition path rather than the permission-request path |
| 2026-10-12 | after: tap **USE CURRENT LOCATION** | `Toast Queue` held exactly one record for the app: `TextToastRecord{… isSystemToast=false … text=Network-based location is turned off. Turn it on, or enter coordinates manually. duration=1}` — the string the build under test carries, `duration=1` being `Toast.LENGTH_LONG` |
| 2026-10-12 | after: screenshot | The capture shows the same message in the toast card over the settings screen, with the status bar's location icon struck through |
| 2026-10-12 | after: saved site | Unchanged: `shared_prefs/observing_location.xml` still held `{"version":1,"latitude":50.08,"longitude":14.42,"source":"MANUAL","zoneId":"Europe/Prague"}`, so the failed acquisition preserved the previous selection |
| 2026-10-12 | device state restored | `settings put secure location_mode 3` read back as `3` with `cmd location is-location-enabled` → `true` and `dumpsys location` reporting the network provider `enabled=true`; `settings put system screen_off_pocket 1` read back as `1`; `settings delete global stay_on_while_plugged_in` returned the key to its pre-pass unset state (`settings get` → `null`, as recorded before the pass); the home-screen wallpaper binding still named the debug service component |

The capturable evidence is the screenshot of the toast card and the `Toast Queue` record beside it:
the record carries the full string where the card shows only an elided form, and the two agree.

## What this does not establish

**`PROVIDER_UNAVAILABLE` could not be exercised on this device.** That branch needs a build
without a network provider registered at all, and this device registers one; removing it would
mean running a different build, not a different device setting. The branch therefore rests on its
host test (`LocationProviderTest.fetchNullWithoutNetwork`) only, per the criterion's own
instruction to state any case that could not be exercised.

Only the disabled-provider case was driven. The unavailable, timeout, and permission-denied messages were not exercised on this device; the disabled-provider string was the only message changed in this push, and it was the subject of the device check. The other strings are unchanged from the previous build.

The pass establishes what the app displayed for one device, one build, and one location state. It
does not establish that a user acts on the message, and #128's own product feature gate records
that the audience and the friction are unverified. No restart, reboot, or process-death variant
was run: the toast is shown by a live activity, and nothing here covers the message after
recreation.
