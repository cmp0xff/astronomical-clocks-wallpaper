# Location slice and coordinate locale verification (#3)

Test build: local debug `app-debug.apk` from `feat/3-current-location` at a845a5d, the rebase of the
review fixes onto `main` at 7dbff58 (APK SHA-256
cf285da99ffc8b7cc3cc667b51fcf3042d01fd836751d17f16dd2391ed323310).

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-09-28 | manual coordinates + relaunch | Saved `45.5, -120.25` as MANUAL; persisted through force-stop and relaunch |
| 2026-09-28 | use current location (grant) | Approximate-location prompt; stored a real network fix as CURRENT_COARSE; no failure logs |
| 2026-09-28 | refresh | Re-fetched cleanly; current value retained |
| 2026-09-28 | deny permission | Permission-denied toast; prior selection preserved |
| 2026-09-28 | location off → refresh | "Could not get the current location" toast; logged "network location provider disabled"; prior selection preserved |
| 2026-09-28 | corrupt stored prefs | Discarded invalid values; no crash; fell back to "No observing location set." |
| 2026-09-29 | manual coordinates + relaunch | Entered `45.5, -120.25`, saved with **Save coordinates** ("Location saved." toast); the display read `45.5, -120.25 (manual)` and held it through a force-stop and relaunch |
| 2026-09-29 | use current location (grant) | The prompt asked only for the approximate location; granting stored a real network fix as CURRENT_COARSE, the display switched to `(current)`, and no failure was logged |
| 2026-09-29 | refresh | A fresh provider registration completed after one update; the current value was retained |

The `2026-09-28` rows apply to cf30cf5 only, and their failure paths (denial, disabled location,
corrupt preferences) have not been re-run since; those remain historical evidence alongside the
automated failure tests. The three happy paths above were re-verified on the rebased build on
`2026-09-29`. The rebase changed no application source, so the re-run and the API 26/36 suite
together cover the PR #23 review fixes (manual-save cancellation, permission-flow recreation, cache
age, provider failure recovery, wrongly typed preferences, localized coordinate entry, and settings
scrolling) on the post-merge codebase.

## Coordinate locale fix (2026-09-29)

Test build: local debug `app-debug.apk` from `feat/3-current-location` with the coordinate locale fix
on top of 5db6f02 (APK SHA-256
34c600ef0c162f8a32bd1c93852e2091ca3de92cae6505839243476bfa0a57a1). Same physical device, Android 16
(API 36), the device's own `de-DE` locale, and no locale override.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-09-29 | manual entry, dot | Typed `1.2` / `2.3`; **Save coordinates** showed the "Location saved." toast, the display read `1.2, 2.3 (manual)`, and `observing_location.xml` held latitude 1.2 / longitude 2.3 as MANUAL |
| 2026-09-29 | manual entry, relaunch | Force-stop and relaunch restored `1.2, 2.3 (manual)` |
| 2026-09-29 | on-screen comma key | With the latitude field focused, Gboard's German numeric keypad accepted `,`, `0`, `5`, `.` in that order, leaving `,05.` in the field |
| 2026-09-29 | keyboard filter, before and after | The same `,`, `0`, `.` taps on a pre-fix build of 5db6f02 (APK SHA-256 f6e8a9e8759d5f321b71127ee3d59f2e4b2aae3e91eadabb105b6717b5bcabb1) left `0.` in the field: the layout filter admitted the dot and dropped the comma |
| 2026-09-29 | manual entry, comma | Entered `1,2` / `2,3` with `adb shell input text` (the virtual keymap's comma) and saved as `1.2, 2.3 (manual)` |

Coordinate entry uses `.` as its decimal separator in every locale and accepts the locale's separator
as an alias. `parseCoordinate` normalizes `.` to the locale decimal separator before parsing with the
locale-aware `NumberFormat`, and `CoordinateKeyListener` derives each field's accepted characters
from the same `textLocale`, so the character filter can no longer drop a separator the parser
accepts. That listener replaced the layout's `numberDecimal` filter, which under `de-DE` admitted `.`
but dropped the comma, leaving no fractional coordinate enterable on the deployment device.
`SettingsActivityLocaleTest` and `CoordinateKeyListenerTest` pin the behavior: `de-rDE` accepts `45,5`
and `8.5`, `ar-rEG` accepts `٤٥٫٥` and `45.5`, and `en-US` still filters `,` out of the field as a
grouping separator.

The `2026-09-29` manual-entry row in the table above the fix was driven with the app's locale
overridden to `en-US`, because `adb` synthetic input could not produce the comma the German keyboard
would; that override is no longer needed for manual entry, and the row stays as evidence for the
build it tested.

Unresolved limitations: the display now rounds coordinates to four decimals, as recorded under
[Coordinate display precision (2026-10-01)](2026-09-30-feat-24-site-timezone.md#coordinate-display-precision-2026-10-01).
The entry fields are seeded from the saved site
(#53), so an unchanged Save needs no retyping and keeps the precision below the fourth decimal; that
seeding landed with its interactive device checks unrun, folded into #42. The offline city chooser
remains outstanding under #3.

**Editorial note (2026-10-07).** This report was extracted from the original device guide.
Branch/revision references describe the original feature work. This cleanup corrects report dating,
cross-report navigation, and evidence scope where applicable; all historical observation rows,
run dates, measured values, and APK/source attribution are retained. No new measurements were made.
