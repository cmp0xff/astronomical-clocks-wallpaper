# Physical-device testing

Procedures and acceptance results for verifying the live wallpaper on a physical Android device.
This page is deliberately device-agnostic about identity: the device is referred to only as
"the physical device", and its model, OEM, and serial number are omitted from public surfaces per
the project's device-privacy policy. Android version is non-identifying and is recorded below; the
firmware build string embeds the model identifier, so it is withheld.

## Connecting

Prefer wireless debugging (Android 11+, API 30+) so no USB cable or vendor ID is needed:

```sh
adb pair <host>:<port> <pairing-code>   # one-time pairing from Developer options
adb connect <host>:<port>
adb devices                             # confirm the device is listed as "device"
```

A USB connection works too; the steps below are transport-independent.

## Install and launch

The application ID changed from `io.github.cmp0xff.astronomicalclockswallpaper` to
`io.github.godaniya.astronomicalclockswallpaper` in #47, so Android installs the new build as a
separate app. Remove any pre-migration debug build first; otherwise both stay installed and the
old wallpaper binding and saved data remain on the device. Saved site and dial settings do not
carry over to the new package.

```sh
adb uninstall io.github.cmp0xff.astronomicalclockswallpaper.debug   # only if a pre-migration build is installed
adb install -r app-debug.apk   # replaces a previous build of the same application ID in place
adb shell am start -n \
  io.github.godaniya.astronomicalclockswallpaper.debug/io.github.godaniya.astronomicalclockswallpaper.SettingsActivity
```

In **Astro Clocks**, tap **Open wallpaper preview**, then apply it to the home and lock screens.

## Inspecting state

```sh
adb shell dumpsys wallpaper                            # active component and visibility
adb shell pidof io.github.godaniya.astronomicalclockswallpaper.debug
adb logcat --pid=<pid> -v time                         # follow the running wallpaper process
adb shell screenrecord /sdcard/clock.mp4               # record; press Ctrl-C to stop
adb pull /sdcard/clock.mp4
```

## Lifecycle transitions

Each #2 transition is mapped to the `ClockEngine` callback it should trigger, so an observation can
be checked against the expected handler.

| Transition | Expected `ClockEngine` behavior |
| --- | --- |
| Preview open | `onCreateEngine`, then `onVisibilityChanged(true)` draws and schedules the next tick |
| Preview close | `onVisibilityChanged(false)` cancels the pending tick |
| Lock / unlock (lit lock screen) | `onVisibilityChanged(false)` / `(true)` across the surface switch |
| Screen off | `onVisibilityChanged(false)` cancels the pending tick |
| Screen on | `onVisibilityChanged(true)` redraws and reschedules from wall time |
| Surface recreation (no visibility change) | `onSurfaceDestroyed` cancels the pending tick; `onSurfaceChanged` redraws and reschedules while visible |
| Reboot | Process and engine recreated; clock resumes from the device wall time |

## Observed results

Test build: local debug `app-debug.apk` from `feat/2-device-feasibility` (SHA-256
881cb234654bd4967e3c9ca4c9a84003723ec8ac94f5ce2c80f2d133d58b5eef; logging-only change,
rendering unchanged).

Rendering was later extracted into `DialRenderer` on `feat/19-rendered-clock` with no intended
visual change. The reviewed renderer preserved the original `#D8B66A` dial colour. That build's host
verification on 2026-09-29 ran `DialRendererTest` on SDK 26 and 36: four cardinal-time checks
anchored orientation, and all three hands were independently checked at 12:20:43, 04:42:03, and
08:03:23. Literal expected angles, isolated 3x3 presence windows, and single-pixel overdraw probes
accounted for pixel rounding and antialiasing; the test documented the distances from other hands
and ticks. It also checked the tick bands, non-square canvas, palette, and renderer reuse on a
200x200 bitmap.

Regression checks first confirmed that the original-colour assertion rejected `#D8B26A`. Temporarily
setting each hand's length to zero, rotating it by 90 degrees, or doubling its length (one change at
a time) failed all three dispersed-time checks on both SDKs for every hand. All nine mutations were
restored before final verification.

That three-hand, 60-tick renderer was superseded by the Orloj foundation, which draws a single
24-hour hand on a 24-numeral scale; `DialRendererTest` now covers that renderer, and the checks above
stand only as the record for the `feat/19-rendered-clock` build. See the Orloj foundation section
below.

`AstronomicalClocksWallpaperServiceTest` covers resuming ticks after surface recreation. `WallpaperFrameTest`
injects null/throwing acquisition, drawing failures, and posting failures through the engine's real
frame operation. It verifies exception logging, one posting attempt for each acquired canvas, and a
subsequent successful scheduled frame. Unrelated drawing exceptions still propagate after posting.
These are host checks only; they do not verify a physical wallpaper surface.

`./gradlew qualityGate :app:assembleDebug` passed with 78 tests per build variant (SDK 26/36 combined),
including strict detekt and Android Lint. `scripts/verify-apk.sh` verified the local debug APK, SHA-256
851bdd8639d0f571569024bfbb5f27861f31ba94aa1c09634e54e1f3f5c205a7, the same artifact that was
installed on the physical device below.

Device verification on 2026-09-29 installed that APK and confirmed core rendering and ticking on the
physical device. Geometry was measured from `adb shell screencap` frames against the angles `ClockState`
produces: 60 tick strokes (12 long, 48 short) and three hands whose reach was 0.504, 0.754, and 0.851 x
the dial radius, matching the 0.50, 0.75, and 0.85 the renderer specifies for the hour, minute, and
second hand. At a capture near device time 17:01:49 the hour hand measured 151.03 degrees and the
minute hand 10.53, within 0.4 degrees of the 150.9 and 10.8 that wall time implies; the second hand
read 288.13 degrees, or 48 s. A lock-screen capture measured the same structure, 12 long and 48 short
ticks with hands reaching 0.505, 0.754, and 0.850 x radius, all three within 0.32 degrees of what
17:04:44 implies. Across 15 frames spanning 9.3 s the second hand's angle stayed within 0.15 degrees of
a whole 6 degree multiple, the discrete once-per-second step the engine schedules rather than a smooth
sweep. `adb logcat` for the wallpaper process reported no skipping-frame or renderer warnings while the
dial was visible.

The device's screenshot pipeline applies a colour transform (`#111923` reads back as `#131922`, and
`#D8B66A` as `#D1BC7E`), so pixel checks against a capture must allow for that offset. The shift is
far larger than the 4/255 green difference between `#D8B66A` and the rejected `#D8B26A`, so captures
cannot adjudicate that palette detail; `DialRendererTest` remains the authority for it.

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

The `2026-09-28` observations below are from the `feat/2-device-feasibility` build. The `2026-09-29`
observations are from `feat/19-rendered-clock` at `c3bd25b`, which carries the `DialRenderer`
extraction.

| Date | Surface | Transition | Observed |
| --- | --- | --- | --- |
| 2026-09-28 | preview | open | Clock advanced |
| 2026-09-28 | preview | close (apply) | Applied to home and lock screens via **Open wallpaper preview** → **Set wallpaper** |
| 2026-09-28 | home | apply | Clock advanced |
| 2026-09-28 | home | repeated lock/unlock | Clock advanced after each of three cycles; process survived |
| 2026-09-28 | home | screen off/on | Clock advanced |
| 2026-09-28 | lit lock | lock/unlock | Clock advanced |
| 2026-09-28 | lit lock | screen off/on | Clock advanced |
| 2026-09-28 | home + lit lock | reboot | Clock recovered; wallpaper persisted and service restarted |
| 2026-09-29 | preview | open | Dial rendered: dark slate background, gold dial, 60 ticks, 3 hands |
| 2026-09-29 | preview | close (apply) | Applied to home and lock screens via **Open wallpaper preview** → **Set wallpaper** |
| 2026-09-29 | home | apply | Measured 12 long + 48 short ticks and 3 hands at 0.504/0.754/0.851 x radius; hands matched the device clock |
| 2026-09-29 | home | ticking | Second hand held whole 6 degree steps, 6 degrees per second |
| 2026-09-29 | lit lock | apply | Same dial on the lock screen: 12 long + 48 short ticks, hands at 0.505/0.754/0.850 x radius |

Unresolved limitations: neither device-tested build had an observed failure. Reboot and
surface-recreation behavior is documented above but was not re-run for `feat/19-rendered-clock`; only
the `2026-09-28` rows cover those transitions. Always On Display is out of scope per #2.

## Location slice (#3)

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

### Coordinate locale fix (2026-09-29)

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
"Coordinate display precision (2026-10-01)" below. The entry fields are seeded from the saved site
(#53), so an unchanged Save needs no retyping and keeps the precision below the fourth decimal; that
seeding landed with its interactive device checks unrun, folded into #42. The offline city chooser
remains outstanding under #3.

## Saved-site timezone verification (#24)

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

### Refresh zone retention (2026-09-30)

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

### Coordinate display precision (2026-10-01)

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

## Orloj foundation verification (#4, #5)

Test build: local debug `app-debug.apk` from `feat/4-orloj-foundation` at 905ff8e (APK SHA-256
85191f20c2249d7430613b86bc5acde4547621cf48d82ad12d5add0eae4b2c08), the artifact installed on the
device below.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The single civil hand moves 0.004 degrees per second, so a stalled loop looks identical to a running
one and neither the eye nor a frame diff can tell them apart. Two instrumented checks replace
inspection. The hand angle is measured from `adb exec-out screencap` frames as the principal axis of
the `DialStyle.HAND` pixels (`#F4E5B8` reads back as about `#F1E5BD` through the screenshot
pipeline), and compared with the civil time the device's own clock implies. Repaint cadence is
measured with `atrace -a <pid> gfx view`, counting the wallpaper producer's
`lock`/`unlock`/`dequeueBuffer`/`queueBuffer` events; `dumpsys SurfaceFlinger --latency` is
unpopulated on this API level, so it could not be used.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | 24-hour scale, preview | XII top, XXIV bottom, VI left, XVIII right, with one hand; day and night shading and the zodiac ring drawn |
| 2026-10-03 | 24-hour scale, home | Same scale and hand; measured hand angle within 0.02 degrees of the civil time the device clock implies |
| 2026-10-03 | 24-hour scale, lit lock screen | Same scale and hand as home at the same instant; both lock-screen clocks agreed with the hand |
| 2026-10-03 | repaint cadence, preview | 6 producer buffer acquisitions in 6 s, deltas 0.999-1.000 s, so 1.000 Hz |
| 2026-10-03 | repaint cadence, home | 6 in 6 s, deltas 0.998-1.001 s, so 1.000 Hz |
| 2026-10-03 | repaint cadence, lit lock screen | 6 in 6 s, deltas 0.998-1.001 s, so 1.000 Hz |
| 2026-10-03 | lock screen stays live | Over 92 s the measured hand advanced 0.39 degrees against 0.38 implied by wall time, so the lock surface redraws rather than holding a snapshot |
| 2026-10-03 | Zodiac ring toggle | Turning it off removed the twelve hand-coloured sign glyphs on the next visible frame and turning it on restored them; the stored preference followed each change |
| 2026-10-03 | Day and night toggle | Turning it off left the sky and twilight pixel counts at zero over a plain night plate; turning it on restored both |
| 2026-10-03 | hide/show | A non-default choice (zodiac ring off) survived leaving to another app and returning |
| 2026-10-03 | surface recreation | `wm size 1080x2000` and then `wm size reset`: the dial re-centred, the choice still applied, cadence 6 in 6 s, and no renderer warnings |
| 2026-10-03 | reboot | New process, same wallpaper binding, the stored non-default choice still applied, hand within 0.02 degrees, cadence 6 in 6 s |
| 2026-10-03 | saved-site timezone | With the site saved while the phone was on `Europe/Prague`, moving the phone to `Asia/Kolkata` left the hand tracking Prague civil time within 0.01 degrees and 52.49 degrees away from Kolkata |
| 2026-10-03 | representative sites | Prague, Sydney, the equator, and both poles entered manually; the horizon adapted as described below |
| 2026-10-03 | no saved site | After clearing app data, Settings read "No observing location set." and the wallpaper drew only the 24-hour scale and hand, following the phone's zone |

The representative sites were entered by hand, never from the device's own position. With the zodiac
ring off, the horizon took the shape the geometric altitude equation predicts, and the measured day,
twilight, and night regions matched `OrlojProjection.altitudeDeg` at every site. At this revision the
plate was always the Prague north-pole projection, whose dial centre is the south celestial pole at
altitude -latitude: Prague (50.08) put the centre below the horizon with the night region inside an
outer day crescent, and Sydney (-33.87) inverted that picture, the centre in daylight with night as
the outer crescent. The equator put the horizon on a straight line through the hub, and the poles put
it on concentric circles, with night inside day at the north pole and day inside night at the south
pole. The southern inversion is the construction replaced in the next section.

Clearing app data also drops the wallpaper binding, so the wallpaper had to be reapplied by hand
afterwards; the device was left with the Orloj wallpaper applied and a current-location site
restored. `am force-stop` was not used as the process-restart check, because it clears the binding
by platform design instead of restarting the service, and `am kill` does not select this process, so
the reboot row is the process-restart evidence.

The review fixes committed after this pass change documentation, one log string outside the render
path, and tests, so the rendering output observed above is unchanged and the observations stand for
the pushed revision.

No failure was observed, and no unresolved limitation remains from this pass. The cadence check
shows only that a frame is produced once per second; battery and frame-cost qualification remain #6.

## Southern plate and Sun layer (#4, #5)

Test build: local debug `app-debug.apk` from `feat/4-orloj-foundation` at 96f972a (APK SHA-256
a13cd926bab76cc72347dc5b09bfb6c489b5b4395ccdf02fbf8ffdac0a388eec), the artifact installed on the
device below.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The sites were entered by hand through Settings. Sydney, the south pole, and the equator were read
from the home screen; the Sun-layer checks used the system live-wallpaper preview, which shows the
dial unobstructed. The screenshot pipeline applies a colour transform (the hand reads back about
`#F1E5BD`, not `#F4E5B8`), so these checks are structural rather than exact-colour, except the hand
angle, which is a principal-axis measurement of the hand pixels.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | southern nesting | Sydney (-33.87, 151.21) put night at the dial centre, a twilight annulus around it, and day outside; the zodiac ring stayed tangent to both tropics |
| 2026-10-03 | south pole | (0, -90) gave concentric night, twilight, and day annuli, night innermost |
| 2026-10-03 | equator | (0, 0) put the horizon on a straight line through the hub, day above and twilight then night below |
| 2026-10-03 | Sun layer off | With the zodiac ring off, turning **Sun** off removed the sky and twilight fills and the horizon and night contour strokes, leaving only the tropics, the equator, and the outer rim |
| 2026-10-03 | Sun layer on | Restoring it redrew the fills and both contour strokes on the next visible frame |
| 2026-10-03 | repaint cadence, preview | 8 producer frames in 7.0 s, deltas 0.983-1.016 s, so 1.001 Hz |
| 2026-10-03 | hand angle, preview | Measured hand angle 32.022 degrees against 32.029 degrees implied by the device clock, 0.007 degrees apart |

The equator plate was observed both on the home screen and in the preview; the southern plates on the
home screen. After the pass the saved site was restored to the device's current location
(50.1081, 14.4695) and the wallpaper was left applied. No failure was observed. The cadence check
shows only that a frame is produced once per second; battery and frame-cost qualification remain #6.

## Dial caching and render-failure containment (#39)

Test build: local debug `app-debug.apk` from `perf/39-dial-caching-resilience` at 8935527 (APK
SHA-256 8dd818135b89cd8810c78347fe3a61d51aa72585bbebcafcfaeb41bfa58da213), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The caching commit (5272a51) and its review fixes (8935527) change the steady-state render path from
re-sampling plate contours to redrawing pre-built `Path` objects, and route the first visible frame
through the same `runTick()` error containment as the posted ticks. This pass re-runs the cadence,
rendering, nesting, and lifecycle checks of the two preceding sections against that build, so a
change to either path would show up against the pre-caching record rather than against inspection.

The twelve zodiac sign glyphs are drawn in `DialStyle.HAND`, so the hand angle was measured with the
zodiac ring off, then the ring was restored; every other check ran with the ring on. The screenshot
pipeline's colour transform still applies, so these checks are structural rather than exact-colour,
except the hand angle, which is a principal-axis measurement of the hand pixels.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | build + install | `./gradlew qualityGate :app:assembleDebug` passed; `scripts/verify-apk.sh` verified the application ID, SDK levels, debug flag, permissions, wallpaper declaration, Astronomy Engine notice, and APK Signature Scheme v2 |
| 2026-10-03 | repaint cadence, preview | 6 producer frames in the 6 s trace window, deltas 0.999-1.002 s, so 1.000 Hz |
| 2026-10-03 | repaint cadence, home | 6 in 6 s, deltas 0.998-1.001 s, so 1.000 Hz |
| 2026-10-03 | repaint cadence, lit lock screen | 6 in 6 s, deltas 0.999-1.001 s, so 1.000 Hz |
| 2026-10-03 | 24-hour scale, home | XII top, XXIV bottom, VI left, XVIII right, one hand; 24 hour ticks at 15° spacing; day, twilight, and night fills, and the zodiac ring with its twelve sign glyphs |
| 2026-10-03 | 24-hour scale, lit lock screen | Same scale, ticks, fills, and ring as home at the same instant |
| 2026-10-03 | hand angle, home | Principal axis 67.950° against 67.933-67.936° implied by the device clock, 0.014-0.017° apart |
| 2026-10-03 | hand angle, lit lock screen | 68.083° against 68.092-68.095°, 0.009-0.012° apart |
| 2026-10-03 | hand angle, preview | 68.432° against 68.435-68.438°, 0.003-0.006° apart |
| 2026-10-03 | equator | (0, 0) put the horizon on a straight line through the hub, day above and twilight then night below |
| 2026-10-03 | Sydney | (-33.86785, 151.20732) put night at the dial centre, a twilight annulus around it, and day outside |
| 2026-10-03 | poles | (90, 0) and (-90, 0) each gave concentric day, twilight, and night annuli, day outermost and night innermost; the two renders were pixel-identical apart from the hand |
| 2026-10-03 | hide/show | 0 producer frames while another app was focused; 6 in 6 s within a second of returning home |
| 2026-10-03 | surface recreation | `wm size 1080x2000` and then `wm size reset`: the dial re-centred on the smaller surface, cadence 6 in 6 s, and no renderer warnings |
| 2026-10-03 | reboot | New process, the wallpaper binding and both stored choices survived, 24 ticks and the zodiac ring drew, and cadence held at 6 in 6 s |
| 2026-10-03 | renderer log | `adb logcat --pid=<pid>` was empty over a 90 s steady 1 Hz soak, and no "skipping frame" warning appeared at any check above |

The equator and Sydney nesting reproduces the preceding section's rows, and the poles' concentric
annuli match its south-pole row; that record was taken before the caching commit, so the cached paths
did not alter the plate geometry. The two poles agree because the southern plate is the radial
inversion of the northern one through the equator circle, so both hemispheres give the same ring
structure. The 2026-09-29 rows record 60 tick strokes (12 long, 48 short) for the earlier three-hand
dial; this dial draws 24 hour ticks, one per Roman numeral. After the pass the saved site was
restored to the device's current location and the wallpaper was left applied.

The follow-up commit that bounds the tick-loop failure log changes no rendering or scheduling
behaviour, so the checks above were re-run against its APK (SHA-256
3c3a5010f7b9c95ffe67173aaa6ef68e017f6d53dd105ada639ef89418a8ff7d). The home screen still drew its 24
hour ticks at 1.000 Hz (6 producer frames in the 6 s trace window, deltas 0.998-1.002 s) with an empty
renderer log. The throttling itself is not device-testable, because it needs a fault that repeats on
every tick and a healthy device does not produce one; `RepeatedFailureLogTest` and
`WallpaperFrameTest.repeatedDrawFailureLogsOnce` are its evidence, the same limitation as the
resilience claim below.

The review-response commit that keeps the Sun-layer paths out of the cache while that layer is
disabled changes no rendered output, and was checked on its own APK (SHA-256
bbf298cf187c6153b6eac04ed48fbcd724c694fafcc91a5e523567fc9f821ca5). With the Sun on, the home screen
drew 24 hour ticks at 1.000 Hz (6 producer frames in the 6 s trace window, deltas 0.999-1.001 s) with
the day, twilight, and night fills present and an empty renderer log. With the Sun off, the twilight
and day fills counted zero pixels while the tropic and equator grid circles stayed drawn, and
re-enabling the Sun restored both fills and both boundary strokes on the next frame.

**Unresolved limitation.** The resilience claim itself is not device-testable: the contained failure
is an injected first-frame `drawFrame()` exception, which cannot be produced on a healthy device
without a debug hook this build does not carry. `WallpaperFrameTest` and `WallpaperFoundationTest`
remain its evidence, and this pass verifies only that the containment path leaves the rendered output
and the 1 Hz tick loop unchanged. The cadence check still shows only that a frame is produced once
per second; battery and frame-cost qualification remain #6.

## Unchanged Save provenance and timezone retention (#42)

The changes in `fix/42-unchanged-save-provenance` prevent an untouched **Save coordinates** action
from silently converting a `CURRENT_COARSE` site to `MANUAL` and overwriting its preserved geographic
timezone with the phone's system default timezone. It also makes `LocationStore.load(repair = false)`
a pure read so that checking coordinates never triggers a repair write.

Host verification covers these paths across API 26 and 36 via Robolectric:
- `SettingsActivityTimezoneTest`: verifies that after acquiring a location on a site with a distinct
  geographic timezone, tapping Save coordinates untouched preserves `CURRENT_COARSE` and its timezone;
  verifies that cold-opening on a saved `CURRENT_COARSE` site and tapping Save coordinates without
  edits preserves `CURRENT_COARSE` and its timezone; and verifies that edited coordinates still switch
  to `MANUAL` and capture the device timezone.
- `SettingsActivityAcquisitionTest`: verifies refresh-then-save preserves `CURRENT_COARSE` and cancels
  in-flight location acquisition.
- `SettingsActivityTest`: verifies that an unchanged Save produces zero SharedPreferences writes after
  startup repairs an invalid timezone, shows the distinct "Coordinates unchanged; nothing to save."
  feedback, and verifies that a stored `-0.0` coordinate does not take the edited branch.
- `LocationStoreTest`: verifies that `load(repair = false)` does not persist repairs.

**Physical-device status.** On 2026-10-03, debug APK
`aba41ceec2fd69cddd899cb10e0fc8f48477e3c5b90b97b3c25f4ff7c2095c8c`
(source revision `02f5acb03788de650ebae7b30f1b7795319b46be`) was installed and exercised on a
connected Android 16 physical device. An existing `CURRENT_COARSE` site retained `(current)` and
`Europe/Prague` after an untouched Save and force-stop/relaunch; the Save visibly showed
"Coordinates unchanged; nothing to save." No new device location was requested. Earlier public
Prague-coordinate checks also covered manual Save, edited manual Save, and relaunch persistence.

## Zodiac compartments and vernal-equinox star (#43)

Test build: local debug `app-debug.apk` from `feat/43-zodiac-compartments` at 2f4660f (APK
SHA-256 460349fbd0c91c78fe83407cfd3c13fc956a3ae17da0a840f57c8fee717ccaaf), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The star is drawn at `eclipticPoint(0.0)`, which is `equatorRadius` from the dial centre, and it
shares that point with the divider between the ARI and PIS compartments. It is 0.04 sky radii across
against a 0.0075 divider stroke, so on this surface the star is about 13.6 px wide where a divider is
2.5 px, but both are `DialStyle.GOLD` and neither a screenshot nor the eye separates them by colour.
The star is therefore measured as the extra gold at one boundary relative to another: a 7 px disc is
centred on the sidereal-implied vernal equinox, which carries the star, and on the autumn equinox
180 degrees away, which carries only a divider. Both are on the equator circle, and the band's gold
rim starts 12.7 px from the ring centreline, so the rim is outside the disc. The expected bearing is
the local sidereal angle, Greenwich apparent sidereal time plus east longitude, computed from the
device clock and the saved site rather than read back from the renderer.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | build + install | `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` passed at the revision above; `adb install -r` replaced the previous build in place and the existing wallpaper binding re-rendered without being reapplied |
| 2026-10-03 | compartments, Prague | Twelve sign labels (ARI through PIS) around the ring, each centred in its own compartment, with a gold radial divider on every boundary between them |
| 2026-10-03 | star against a divider, Prague | 72 gold px in the 7 px disc at the vernal equinox against 33 px at the autumn equinox, 180 degrees away on the same circle, so the star is drawn rather than only the divider |
| 2026-10-03 | star bearing, Prague | Gold centroid bearing 340.514 deg against 340.549 deg implied by local sidereal time, 0.035 deg apart |
| 2026-10-03 | compartments, Sydney | Twelve labels each centred in its compartment with a divider on every boundary, at the placement the southern projection gives for -33.86785, 151.20732 |
| 2026-10-03 | star against a divider, Sydney | 71 gold px at the vernal equinox against 34 px at the autumn equinox, so the southern projection draws the star on the same terms |
| 2026-10-03 | star bearing, Sydney | Gold centroid bearing 117.979 deg against 117.994 deg implied by local sidereal time, 0.015 deg apart |
| 2026-10-03 | star follows sidereal time, Sydney | Re-captured 26 min later at the same site: the measured bearing moved from 117.979 to 124.456 deg while local sidereal time advanced from 117.994 to 124.416 deg, and that capture again read 69 px against 35 px at the autumn equinox |

Both sites were entered through the settings app's coordinate fields and Save button, not from the
device's own position, and the wallpaper binding stayed applied throughout: the app was never
force-stopped, so the captures above are of the same running process that the install replaced. The
device was left on the saved Prague coordinates after the pass; manual entry changes the site's
provenance from current location to manual, which only the display label reflects, and Use current
location restores it. This pass re-checked the ring and the star only: the cadence, surface
recreation, lock-screen, and location rows recorded for the preceding revisions were not re-run
against this build. The two compartment rows describe the dividers as they were drawn before the
reorientation recorded in the next subsection.

### Reoriented zodiac dividers (#43)

Test build: local debug `app-debug.apk` from `feat/43-zodiac-compartments` at d231ce8 (APK
SHA-256 2ab46dcaf5dfe8ddb9d5ce29c2a640fc252002b8c4f37c9aaa5cd21a60ecd922), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The dividers now run along rays from the dial centre rather than along the zodiac ring's own
radius, so a check that assumed the ring radius would no longer describe them. Frames come from
`adb exec-out screencap`. The dial centre is located from the gold hand hub and the scale from the
outer gold rim, both read off the frame rather than assumed. The projected boundary points, the
offset ring centre, and the local sidereal angle all come from Greenwich apparent sidereal time
plus the site's east longitude, computed from the device clock and the saved site, not read back
from the renderer: the 0 degree Aries star placed by that independent geometry lands within 0.3 px
of the drawn star at both sites, which validates the frame before any divider is fitted. Each
stroke's direction is then the principal axis of the `DialStyle.GOLD` pixels that lie inside the
night band within 5 px of its predicted ray; the two gold rim arcs sit 14 px out and are excluded,
because including them pulled the fitted line toward the ring's radius. The tilt is reported both
against the ring's own radius at the boundary and against the dial-centre ray, and the equinox
counts repeat the previous pass's 7 px disc.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | build + install | `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` passed at the revision above; `adb install -r` replaced the previous build in place and the existing wallpaper binding re-rendered without being reapplied |
| 2026-10-03 | dividers point at the pivot, Prague | Twelve gold strokes, one on every boundary and spanning the band: each fitted line passes 0.1-4.1 px from the dial centre and lies 0.0-1.1 deg from the ray from the centre through its boundary point |
| 2026-10-03 | obliquity of the tilt, Prague | Departure from the ring's own radius is 0.6 deg at the 0 deg Cancer boundary and 0.5 deg at 0 deg Capricorn (coincidence, at the fit's resolution), 12.0-12.5 deg at the four boundaries at 60/120/240/300, 20.3-21.0 deg at 30/150/210/330, and 22.9 deg at 0 deg Aries and 24.5 deg at 0 deg Libra, the two equinox boundaries, against the 23.44 deg true obliquity |
| 2026-10-03 | clip to the night band, Prague | Along each stroke's ray the gold run starts and ends within 2 px of the ring's inner and outer rim circles and is continuous across the band; no stroke gold lies beyond them, the further gold found within 20 px of the band belonging to the equator and sky-boundary circles, recognised by its radius |
| 2026-10-03 | compartments stay closed, Prague | The interior of all twelve compartments, 5 deg of ecliptic longitude past each boundary on the ring centreline, reads `DialStyle.NIGHT` |
| 2026-10-03 | star against a divider, Prague | 68 gold px in the 7 px disc at the vernal equinox against 34 px at the autumn equinox 180 degrees away, so the star still adds gold at a boundary that carries a reoriented divider |
| 2026-10-03 | dividers point at the pivot, Sydney | At -33.86785, 151.20732 the same twelve strokes pass 0.1-2.5 px from the centre and 0.0-0.6 deg from their rays |
| 2026-10-03 | obliquity of the tilt, Sydney | 0.5 deg at 0 deg Cancer and 0.3 deg at 0 deg Capricorn, 12.2-12.5 deg at 60/120/240/300, 20.5-20.8 deg at 30/150/210/330, and 24.0 deg at both equinox boundaries, so the southern plate tilts its dividers on the same terms |
| 2026-10-03 | star against a divider, Sydney | 68 gold px at the vernal equinox against 37 px at the autumn equinox |

Both sites were again entered through the settings app's coordinate fields and Save, and the app was
not force-stopped: the install's process restart brought up the new build, and the same running
process produced every capture. The device was left on the saved Prague coordinates, byte-identical
to those recorded before the pass. Only the ring's dividers were re-measured; the sign labels, the
compartments, the star, and the cadence, lifecycle, and location rows from the earlier passes were
not re-run against this build.

## Sun marker (#27)

Test build: local debug `app-debug.apk` from `feat/27-render-sun` at 4ffa962, the review fix on top of
the rebase at 80e1a9f (APK SHA-256
1e165b93b8cb1b07b1744ddfc86d3f81cc5c358aebdc064bfd94a07facd46b47), the artifact installed on the
device below. Subsequent commits on this branch (c0cb0ba and later) are documentation and host-test
changes only, leaving `app/src/main/` untouched and producing the identical APK bytecode and SHA-256.
At 4ffa962, `./gradlew qualityGate :app:assembleDebug` passed with 376 unit tests per build variant
(now 377 following subsequent host-test additions) and no detekt, ktlint, or Android Lint findings,
and `scripts/verify-apk.sh` verified the application ID, SDK levels, debug flag, permissions, wallpaper
declaration, Astronomy Engine notice, and APK Signature Scheme v2.

Same physical device as the earlier passes. Android version: 16 (API 36). Firmware build: withheld
(embeds the model identifier), per issue #20. The saved site was the device's own current-location
fix, so its coordinates are withheld as private data too; the checks below report residuals against
that site rather than repeating it. Sydney was entered by hand as `-33.86785, 151.20732`, which is not
the device's position.

**Method.** The civil hand turns 0.0041667 degrees per second and the Sun marker 0.0041781, so neither
the eye nor a frame diff can separate a marker that tracks the sky from one that holds a fixed
position. Each frame is therefore measured, not judged: every capture is bracketed by
`adb shell date -u` before and after, and three quantities are read off the frame and compared with
values computed from ERFA rather than read back from the renderer.

- The dial centre and scale come from a least-squares fit to the outer gold rim, whose radius is
  `OUTER_RADIUS = 1.37`; the sky radius is `min(w, h) * 0.43 / 1.37`. Every capture fitted to
  (539.5, 1203.5) with a rim radius of 465.1 px against 464.8 expected, on a 1080x2408 canvas whose
  centre is (540, 1204).
- The marker is the densest gold disc within the sky radius, excluding the hand hub. Gold is matched
  by hue shape after normalizing each pixel to its brightest channel, so the check does not depend on
  a literal ARGB value or on a screenshot colour transform.
- The civil hand's bearing is the far tip of the `DialStyle.HAND` pixels.

The independent expectation uses Greenwich apparent sidereal time from `erfa.gst06a`, the saved
site's east-positive longitude, and the Sun's geometric ecliptic longitude by the recipe recorded in
[`DialGeometryFixture.kt`](../app/src/test/kotlin/io/github/godaniya/astronomicalclockswallpaper/DialGeometryFixture.kt).
The marker's bearing is the Sun's hour angle — `H = local apparent sidereal angle − right ascension`,
noon at the top of the Roman scale — and its projected radius is
`tan(45° ± declination/2) / tan(45° + true obliquity/2)`, with the northern form above the equator and
the southern form below. The two handles are largely independent: the radius moves with the
declination, and so with the Sun's longitude, not only with the hour angle.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install | `qualityGate` 376 tests per variant at build time (377 at PR tip), no static-analysis findings; `verify-apk.sh` passed; APK SHA-256 `1e165b93…d46b47` installed with `adb install -r` over the existing binding |
| 2026-10-04 | marker, Prague | Measured bearing 182.907° against 182.881° expected, residual **+0.026°**; projected radius 206.9 px against 206.9 px, residual **+0.01 px**; 0.09 px apart on the dial |
| 2026-10-04 | distinct from the 0° Aries star | With the zodiac ring on, the star's five-pointed glyph sits at radius 0.657 (the equator circle) and bearing 15.8°, and the marker's radiant disc at radius 0.608 and bearing 185.4°; the two never share a probe |
| 2026-10-04 | marker, Sydney | Measured bearing 322.821° against 322.796° expected, residual **+0.025°**; projected radius 239.1 px against 240.0 px, residual **−0.95 px**; 0.95 px apart, and a different bearing and radius from Prague at the same instant |
| 2026-10-04 | marker follows sidereal time | Two captures 82.09 min apart with the same layers: the bearing advanced 20.363° against 20.527° expected, residual **−0.164°** (about 0.6 px), and the radius moved 206.9 → 206.3 px, so the marker advanced along the ecliptic rather than holding a position |
| 2026-10-04 | missing site | After clearing app data, Settings read "No observing location set." with both layer checkboxes greyed out and disabled, and the preview drew only the 24-hour scale and hand. Clearing also dropped the binding to the stock `ImageWallpaper`, as #47 records; after reapplying, the dial had no marker and no gold anywhere inside the sky radius |
| 2026-10-04 | Sun toggle | Off removed the sky and twilight fills, both contour strokes, and the marker, leaving the plain grid; on restored all of them on the next visible frame |
| 2026-10-04 | Zodiac ring toggle | Off removed the band, its labels and dividers, and the equinox star, leaving the marker as the only gold inside the sky radius; on restored them |
| 2026-10-04 | surface recreation | `wm size 1080x2000` then `wm size reset`: the dial re-centred to (539.5, 999.5) and back to (539.5, 1203.5), the marker holding the same normalized bearing and radius in both, with `WallpaperService` logging `handleResized` and `Session.relayout` |
| 2026-10-04 | screen off/on | The dial redrew with the marker present at the same normalized position, and `logcat` for the process held no renderer warning |
| 2026-10-04 | lit lock screen | With the device's lock screen temporarily enabled, the dial and marker drew behind the lock UI at the same normalized position; the overlay dims the palette, so this check is structural |
| 2026-10-04 | repaint cadence | 8 producer buffer acquisitions in the 7.000 s trace window, deltas 0.998-1.002 s, so 1.000 Hz |
| 2026-10-04 | reboot | `adb reboot`: new process, the wallpaper binding and the restored site survived, and the marker matched its expectation to **0.43 px** |
| 2026-10-04 | saved-site timezone | With the site on `Europe/Prague`, `cmd alarm set-timezone Asia/Kolkata` moved the phone to +05:30 and the status bar to 05:55 IST; the civil hand stayed within 0.035° of Prague civil time and the marker's bearing held at 203.779° in both frames, its own sidereal advance of 0.034° being below the probe's resolution |

The Prague and Sydney rows are the same claim at two hemispheres: the marker's bearing and projected
radius both match an expectation that never consults the renderer, in the northern plate that
`DialGeometryFixture.kt` also exercises and in the southern plate #27 additionally requires. The
residuals are of the same size as the measurement noise. The probe localizes a 7 px disc to about
0.5 px, which is 0.14° of bearing at this radius, and the engine's modelled ΔT differs from the
fixture's by about 6.5 s, which is a further 0.027° of rotation; the Prague residual of +0.026° is
that second term almost exactly. Nothing here therefore establishes an accuracy better than about
1 px, which is what the rows claim.

Two device settings were changed for this pass and restored afterwards: the lock screen, which the
device had disabled, was enabled for its row and set back to disabled; and the screen timeout was
raised to 30 minutes with stay-awake on so the screen would not sleep mid-pass, then restored to
5 minutes with stay-awake off. The saved site was restored to the device's current-location record
after Sydney and after the missing-site row, and the device was left on it with the wallpaper applied
and both layers at their defaults.

**What this pass did not run.** Battery and frame cost remain #6; the cadence check shows only that a
frame is produced once per second. The 0° Aries star's own position is `ZodiacRenderer`'s claim and
`DialRendererTest`'s to keep; this pass only checks that the marker is distinguishable from it. The
equinox-instant and season tests are host tests against fixtures. Always On Display remains out of
scope per #2. The screenshot pipeline returned the palette unchanged on these captures — `#D8B66A`
read back as `#D8B66A` — unlike the transform earlier passes recorded; the marker probe normalizes
each pixel before matching, so its result does not depend on which of the two applies.
