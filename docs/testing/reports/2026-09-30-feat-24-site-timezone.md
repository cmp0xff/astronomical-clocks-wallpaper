# Saved-site timezone and coordinate precision verification (#24)

Automated coverage uses fixed instants and explicit zones for site time, Prague's spring and
autumn DST boundaries, preference updates across multiple engines, hidden/destroyed engines,
and recreation. Settings tests change the phone zone while an acquisition is in flight to
verify capture at save time, and acquire on top of an already-saved site to verify that a refresh
retains its zone rather than re-capturing the phone's. These Robolectric checks do not establish
physical-device behavior.

Test build: local debug `app-debug.apk` from `feat/24-site-timezone` at d21c6a7 (APK SHA-256
d5e0235d5d772044324b02505e8d19486d13912f55b8c77852b21781d7dd8335).

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-09-30 | save + display | Entered the neutral coordinates `35.68, 139.69`, which are not the device's location, and tapped **Save coordinates**; the display read `35.68, 139.69 (manual)` and `Timezone: Europe/Prague`, and the stored record held `"source":"MANUAL"` with `zoneId` `Europe/Prague` |
| 2026-09-30 | phone-zone change | Turned automatic zone selection off and moved the phone to `Asia/Kolkata` (UTC+05:30); `getprop persist.sys.timezone` and `date` both then showed +05:30, which is 3.5 h from the captured +02:00, and this was confirmed before any later observation |
| 2026-09-30 | core retention | Force-stop and relaunch while the phone was on `Asia/Kolkata` left the display and the record reading `Europe/Prague`. On the home screen at 20:38:36 UTC the dial's hands measured 320.5° and 234.0°; the saved site's civil time, 22:38:37 CEST, is 319.3° and 231.7°, while the status bar read 02:08 IST |
| 2026-09-30 | hide/show | Home, then system Settings, then Home: the dial redrew at the saved site's civil time (hands 324.0° and 275.0° at 20:45:26 UTC against 322.7° and 272.6° expected) and the provider pid was unchanged |
| 2026-09-30 | surface recreation | `wm size 1080x1921` then `wm size reset`: the dial recentred to (539,960) at radius 325 and back to (540,1170) at radius 324, the hands tracked the saved site throughout, and logcat showed `onSurfaceChanged`, `onSurfaceRedrawNeeded`, and a drawn window about 15 ms later in each direction, with no skipped or lost frames |
| 2026-09-30 | process restart | `am force-stop` emptied `pidof` and the provider did **not** rebind on returning home: the system fell back to its stock `ImageWallpaper` and the dial was gone until the wallpaper was applied again by hand, after which the saved site was still intact |
| 2026-09-30 | refresh inversion | With the phone restored to `Europe/Prague` and the saved site still on `Asia/Kolkata`, **Refresh location** replaced the manual record with a live coarse fix and re-captured the phone zone: the display and the record both flipped to `(current)` with `zoneId` `Asia/Kolkata` |
| 2026-09-30 | saved site, both directions | With the phone on `Europe/Prague` and the saved site on `Asia/Kolkata`, the dial showed the site's civil time (hands 70.0° and 109.5° at 20:47:57 UTC against 69.0° and 107.7° expected) while the status bar read 22:47 CEST, the reverse of the core retention row |

Applying the wallpaper by hand after a force-stop restores the saved site unchanged, but the system
does not rebind a force-stopped provider on its own, so a killed process leaves the stock wallpaper
in place. The `refresh inversion` row above records that build re-capturing the phone's current zone
and overwriting the saved one; that is the defect fixed by the build in **Refresh zone retention
(2026-09-30)** below, and the row stays as the d21c6a7 build's historical record.

A current-location acquisition with no site saved yet still captures the phone zone; establishing
each site's geographic timezone remains #24, with the offline city chooser in #21.

The `no skipped or lost frames` clause above is withdrawn as clean-log evidence: a completed empty
scan is inconclusive, and that run was not repeated with the hardened collection checks. Original
APK/source attribution is retained.

## Refresh zone retention (2026-09-30)

Test build: local debug `app-debug.apk` from `fix/24-refresh-zone-retention` at 12e4184, the fix
that keeps a saved site's zone when its coordinates are refreshed (APK SHA-256
eb78aa7167ead68e737ae11409432f5e9c533e0158fcf3fddcf59555abe61f6f).

Same physical device. Android version: 16 (API 36). Firmware build: withheld (embeds the model
identifier).

| Date | Check | Observed |
| --- | --- | --- |
| 2026-09-30 | save + baseline | Entered the neutral coordinates `35.68, 139.69` and tapped **Save coordinates** while the phone was on `Europe/Prague`; the display read `35.68, 139.69 (manual)` and `Timezone: Europe/Prague`, and the stored record held `"source":"MANUAL"` with `zoneId` `Europe/Prague` |
| 2026-09-30 | phone-zone change | Turned automatic zone selection off and moved the phone to `Asia/Kolkata` with `cmd alarm set-timezone`; `getprop persist.sys.timezone` read `Asia/Kolkata` and `date` read `Thu Oct 1 02:45:55 IST 2026`, which is 3.5 h from the baseline's +02:00, and this was confirmed before interpreting the refresh |
| 2026-09-30 | refresh retention | Tapped **Refresh location** with the phone still on `Asia/Kolkata`: the display and the record both flipped to `(current)`, so `source` became `CURRENT_COARSE` and the neutral coordinates were replaced by the live coarse fix, while the display's `Timezone:` line and the stored `zoneId` both stayed `Europe/Prague`. The live coordinates are the device's own position and are deliberately not recorded |

The run-as record read matched the on-screen `Timezone:` line in every row. The phone zone was
restored to `Europe/Prague` with automatic time-zone selection re-enabled afterwards. The pre-fix
behaviour this replaces is the `refresh inversion` row above, from the d21c6a7 build.

## Coordinate display precision (2026-10-01)

Test build: local debug `app-debug.apk` from `fix/24-refresh-zone-retention` with the coordinate
display fix on top of 12e4184 (APK SHA-256
6f2a54f349ce63de7fc87a3c413a1cca6264944013bc467cc6c2b9bbf457a409).

Same physical device. Android version: 16 (API 36). Firmware build: withheld (embeds the model
identifier).

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-01 | install + display | After reinstalling the APK, the record left by the run above — a `(current)` coarse fix — also rendered with four decimals on each side, with its `Timezone: Europe/Prague` line intact. The live coordinates are the device's own position and are deliberately not recorded |
| 2026-10-01 | high-precision entry | Entered `-33.86785` / `151.20732`, which are not the device's location, and tapped **Save coordinates**: the display read `-33.8678, 151.2073 (manual)`, while the stored record held `"latitude":-33.86785,"longitude":151.20732` unchanged, so only the display rounds. The fourth decimal is 8 rather than 9 because the stored `Double` is a hair below -33.86785 |
| 2026-10-01 | neutral entry | Entered the neutral `35.68` / `139.69` and tapped **Save coordinates**: the display read `35.6800, 139.6900 (manual)` with `Timezone: Europe/Prague`, and the stored record held `"source":"MANUAL"` with `zoneId` `Europe/Prague` |

The run-as record read matched the on-screen `Timezone:` line in every row. The device ends on the
neutral record with the phone zone: **Refresh location** was tried twice at the end of the session
to restore the device's own site, and both attempts timed out after 10 s —
`LocationProvider: network location update timed out after 10000ms`, followed by the toast logged
from `fetchCurrentLocation`'s failure branch — leaving the display and the record unchanged.

The display interpolates four decimals — about 11 m — and `.` in every locale, which matches the
coordinate entry convention recorded above. `formatCoordinate` rounds to four decimals and then
formats with `Locale.ROOT`, so the readout has a uniform width and does not change with the phone's
locale; the `Double.toString()` it replaces printed every digit the `Double` carries. `LocationStore`
is untouched: `save()` still writes the full `Double`, so a stored coordinate keeps the precision it
was entered with. Reading the rounded readout and retyping it would lose precision, but the entry
fields are seeded from the saved site (#53), so editing one coordinate leaves the other at full
precision; the interactive device checks for that seeding are unrun, folded into #42.
