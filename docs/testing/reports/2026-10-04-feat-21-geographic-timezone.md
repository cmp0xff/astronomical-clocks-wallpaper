# Geographic timezone and selector verification (#21, #24)

Automated coverage resolves each site's geographic zone offline, keeps a confirmed zone across
refreshes, recreation, and phone-timezone changes, and exercises the estimate confirmation and the
picker through Robolectric on API 26 and API 36. Those checks do not establish physical-device
behavior; the rows below do, on a reduced pass.

Test build: local debug `app-debug.apk` from `feat/21-manual-timezone` at 376c138 (APK SHA-256
4e76c6e42b8bc4f7757c72f493f1be61b0a76f9e42d283ceca8c5d503f254a52), installed in place with
`adb install -r` over the previous debug build.

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install + version | `qualityGate` and `:app:assembleDebug` clean, `verify-apk.sh` passed; installed in place and Settings read `0.1.0-debug` (versionCode 1, minSdk 26, targetSdk 37). The phone was on `Europe/Prague` and the saved site read `50.0800, 14.4200 (manual)` with `Timezone: Europe/Prague` |
| 2026-10-04 | manual site, suggestion | Entered the published Tokyo coordinates `35.6762, 139.6503` by hand; the timezone button updated to **Timezone: Asia/Tokyo** while the phone zone stayed `Europe/Prague`, so the suggestion is geographic and not the phone's zone |
| 2026-10-04 | estimate confirmation | Tapping **Save coordinates** opened a dialog titled **Estimated timezone** reading "Timezone Asia/Tokyo was estimated from the nearest IANA reference point and can be wrong near a border", with **Choose…**, **Cancel**, and **Save**. With the dialog up, the `run-as` record still held the previous site, `{"latitude":50.08,"longitude":14.42,"source":"MANUAL","zoneId":"Europe/Prague"}`: the estimate was offered, not written |
| 2026-10-04 | Choose… overrides the estimate | Tapping **Choose…** opened the picker over the dialog and picking `Pacific/Auckland` closed both and saved. The record then read `{"latitude":35.6762,"longitude":139.6503,"source":"MANUAL","zoneId":"Pacific/Auckland"}`, the screen showed `35.6762, 139.6503 (manual)` with `Timezone: Pacific/Auckland`, and a "Location saved." toast appeared, so the picker's choice — not the estimate that prompted it — is what persisted |
| 2026-10-04 | picker pre-selection | Reopening the picker with `Pacific/Auckland` saved opened the list scrolled to it with its radio filled, not at the alphabetically first entry; the entries shown ran `Pacific/Auckland`, `Pacific/Bougainville`, `Pacific/Chatham`, …, `Pacific/Honolulu`, with no `Africa/*` row visible |
| 2026-10-04 | dial civil hour follows the site | With the site saved on `Pacific/Auckland` and the phone moved to `Asia/Kolkata` by `cmd alarm set-timezone`, the home-screen capture at 1791120867 UTC measured the civil hand at **218.6111°**. The site's civil time then was 02:34:27 NZDT, whose expectation is **218.6125°**, a residual of **−0.0014°**; one second later the residual was **−0.0056°**. The phone's own zone would have put the same instant at 19:04:27 IST, an expectation of 106.1125°, which is **112.5°** away from what was drawn |
| 2026-10-04 | site survives a phone-zone change | With the phone on `Asia/Kolkata` (status bar 19:04 IST) the screen still read `35.6762, 139.6503 (manual)` and `Timezone: Pacific/Auckland`, and the record was byte-identical to the one written while the phone was on `Europe/Prague` |
| 2026-10-04 | force-stop and relaunch | `am force-stop` emptied `pidof`, and the record still held the Tokyo site on `Pacific/Auckland`; relaunching Settings showed the same coordinates and `Timezone: Pacific/Auckland` with no dialog. The force-stop dropped the wallpaper binding to the stock `ImageWallpaper`, as #42 and #28 also record, and the wallpaper was reapplied afterwards |
| 2026-10-04 | restore and the confirmation's Save path | Re-entering the original `50.08, 14.42` suggested `Europe/Prague`; the dialog was shown again and the record was still untouched while it was up, and accepting it with **Save** wrote `{"latitude":50.08,"longitude":14.42,"source":"MANUAL","zoneId":"Europe/Prague"}`. That both exercises the dialog's Save button on the device and restores the site the pass found |

The dial row is the same claim as the `saved-site timezone` row in the [site-timezone report](2026-09-30-feat-24-site-timezone.md),
re-checked against the build the original feature branch produced. The probe finds the dial centre in the hub's inner disc, which is painted
at the dial origin, and measures the civil hand as the densest 1-degree cluster of hand-coloured
pixels, because the same colour also paints the twelve zodiac sign glyphs. One pixel of centre error
moves the measured bearing by about 0.16 deg at the hand's tip radius of 355.1 px, and the frame is
quantised to one second of civil time, 0.0042 deg, so the observed residual is inside the probe's own
resolution rather than a claim about the renderer's accuracy. The separation the row rests on is the
112.5 deg between the site's zone and the phone's, which is two orders of magnitude larger than that
resolution.

The confirmation row is what the reduced pass exists to establish: the `run-as` record read was taken
while the dialog was on screen in both cases, and it showed the previous site each time, so the rows
distinguish "offered" from "saved" rather than inferring the gate from the dialog's presence.

Four device settings were changed for this pass and restored afterwards: the phone timezone, moved to
`Asia/Kolkata` for its rows and set back to `Europe/Prague`; `stay_on_while_plugged_in`, set to USB
because the screen kept sleeping mid-sequence and restored to its previous value of 0;
`screen_off_pocket`, set to 0 and restored to 1; and `proximity_sensor`, set to 0 and restored to 1,
because an accidental-touch-protection overlay armed by the proximity sensor was intercepting the
injected input and had to be dismissed and disabled to drive the UI at all. The screen timeout was
read back unchanged at 300000 ms. The device was left with the wallpaper applied to the home screen,
the phone on `Europe/Prague`, and the saved site back on manual Prague `50.08, 14.42`.

**What this pass did not run.** The reboot row is deliberately not run, per the reduction agreed for
this pass. `am force-stop` is not accepted as process-recreation evidence (it is the row above, and it
clearly does not restart the service: it dropped the wallpaper binding instead), so this pass
establishes record persistence across a process restart but not service re-creation, and #27's own
reboot row remains the most recent process-recreation evidence on this device. The current-location
flow's estimate dialog was covered by Robolectric and by this pass's manual path, but no acquisition
was taken during the pass, because acquiring one would have written the device's own position, which
this project does not publish. Always On Display remains out of scope per #2, and battery and frame
cost remain #6. One cosmetic observation is outside this change: `android.R.string.cancel` renders in
the device's language ("Abbrechen") beside the app's own English strings in both dialogs, as the
picker already did before this branch.

## Searchable picker (2026-10-04)

Automated coverage types a query, narrows the adapter, and picks from the narrowed rows under
Robolectric. The rows below re-check that behavior on the physical device against the build this
branch now produces, with every tested site entered by hand from published coordinates.

Test build: local debug `app-debug.apk` from `feat/21-manual-timezone` at 43229f0 (APK SHA-256
5e64ade776b7f2df481a6a5b24a692d66762fa59215672b29ed3d34707a60612), installed in place with
`adb install -r` over the previous debug build.

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install + version | `qualityGate` and `:app:assembleDebug` clean, `verify-apk.sh` passed; installed in place and Settings read `0.1.0-debug` (versionCode 1, minSdk 26, targetSdk 37). The phone was on `Europe/Prague` |
| 2026-10-04 | hand-entered site | Entered the published Prague coordinates `50.0800, 14.4200` by hand; the timezone button read **Timezone: Europe/Prague** from the geographic suggestion, and the saved record still held the site the pass found until **Save coordinates** was tapped |
| 2026-10-04 | picker opens on the saved zone | Tapping the timezone button opened **Select timezone** with an empty **Filter timezones** box and the list scrolled to `Europe/Prague` with its radio filled, not to the alphabetically first entry. On the build before the scroll fix the same dialog opened at `Africa/Abidjan` with `Europe/Prague` checked but hundreds of rows below the fold, so `list.setSelection` was added and this row is the check for it |
| 2026-10-04 | typing narrows the list | Typing `new york` in the filter narrowed the hundreds of rows to the single row `America/New_York`; the timezone button behind the dialog still read `Timezone: Europe/Prague`, so a query that only narrows commits nothing |
| 2026-10-04 | a filtered row stores and displays | Tapping the `America/New_York` row closed the dialog and the button read `Timezone: America/New_York`; **Save coordinates** showed a "Location saved." toast, the screen read `50.0800, 14.4200 (manual)` with `Timezone: America/New_York`, and the `run-as` record became `{"latitude":50.08,"longitude":14.42,"source":"MANUAL","zoneId":"America\/New_York"}` — the row the filter offered, and no other |
| 2026-10-04 | a matchless query | Reopening the picker and typing `zzzzzz` emptied the list, showed **No matching timezone**, and left the button reading `Timezone: America/New_York`; the `run-as` record read while the dialog was up was byte-identical to the saved one, so the query committed nothing |
| 2026-10-04 | restore | The record was restored to the one the pass found — a `current` record for the Prague area, coordinates withheld as an acquired fix — and Settings read that record's coordinates with `Timezone: Europe/Prague`. The phone's wallpaper was reapplied to the home screen afterwards (see below) |

The first picker row is the reason this subsection exists alongside the earlier section: the picker
offers hundreds of entries, so opening it on the alphabetically first row would hide the zone
actually in effect several hundred rows down. The scroll fix has no Robolectric assertion — with no
layout pass, `ListView.getSelectedItemPosition()` reports `-1` — so this device row, not the unit
suite, is what establishes it. The filter and the no-match row are what make a hundreds-long list
usable: a query that reaches no listed zone shows **No matching timezone** and cannot commit a zone
the list never offered.

The record was restored by writing it back after `am force-stop` rather than through the UI, because
storing a picked zone rewrites the record's `source` from `current` to `manual`, and the
originally-found `current` record cannot be re-created without acquiring a new fix, which this pass
does not do.

Two device settings were changed for this pass and restored: `screen_off_pocket`, set to 0 so the
screen could not sleep mid-pass and restored to 1; and `stay_on_while_plugged_in`, raised to USB (2)
by `svc power stayon usb` to keep the screen awake and reset to 0, this device's previously recorded
resting value, since the command was issued before the pass read it. `proximity_sensor` read back
unchanged at 1, and the screen timeout at 300000 ms. The phone timezone was never changed, and no
accidental-touch-protection overlay armed during the pass. Because `am force-stop` was used to
reload the restored record, the wallpaper binding dropped to the stock `ImageWallpaper` and was
reapplied to the home screen through the app's **Open wallpaper preview**; the lock screen was left
as found.

**What this subsection did not run.** Only the picker was exercised here; the estimate-confirmation,
dial-hour, and force-stop rows stay as recorded in the section above. The reboot row and
process-recreation evidence are unchanged. No current-location fix was acquired, because doing so
would write the device's own coordinates.

**Editorial note (2026-10-07).** This report was extracted from the original device guide.
Branch/revision references describe the original feature work. This cleanup corrects report dating,
cross-report navigation, and evidence scope where applicable; all historical observation rows,
run dates, measured values, and APK/source attribution are retained. No new measurements were made.
