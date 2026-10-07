# Device feasibility and initial rendering verification (#2, #19)

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
