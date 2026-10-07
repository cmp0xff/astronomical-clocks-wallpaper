# Orloj foundation verification (#4, #5)

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
pole. The southern inversion is the construction replaced in the
[southern plate report](2026-10-03-feat-4-southern-plate.md).

Clearing app data also drops the wallpaper binding, so the wallpaper had to be reapplied by hand
afterwards; the device was left with the Orloj wallpaper applied and a current-location site
restored. `am force-stop` was not used as the process-restart check, because it clears the binding
by platform design instead of restarting the service, and `am kill` does not select this process, so
the reboot row is the process-restart evidence.

The review fixes committed after this pass change documentation, one log string outside the render
path, and tests, so the rendering output observed above is unchanged and the observations stand for
the original feature branch's pushed revision, not the current build.

No failure was observed in the checks run. The cadence check
shows only that a frame is produced once per second; battery and frame-cost qualification remain #6.

**Editorial note (2026-10-07).** This report was extracted from the original device guide.
Branch/revision references describe the original feature work. This cleanup corrects report dating,
cross-report navigation, and evidence scope where applicable; all historical observation rows,
run dates, measured values, and APK/source attribution are retained. No new measurements were made.
