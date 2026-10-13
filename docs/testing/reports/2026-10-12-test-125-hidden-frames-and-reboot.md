# Hidden rendering and reboot restoration (#125)

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew qualityGate :app:assembleDebug`, APK SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`. The `app/src/main` tree it
compiles is the one at `55649bfc9d76864bc0c9226e0c40804295d6396f`, which is byte-identical to the
tree of the branch's base commit `5efae15` (`git diff 5efae15 55649bf -- app/src/main` is empty —
compared against the base rather than the `upstream/main` ref, which has moved on since and is not a
stable comparison); the branch head carries host tooling and documentation only. The artifact was
installed in place with `adb install -r`.

Physical device running Android 16 (API 36). Vendor, model, serial, and firmware build are withheld
per the device-privacy policy. The saved site carried through the pass is the manual published
city-centre reference `50.08, 14.42`, `Europe/Prague`; the device's own timezone is `Europe/Berlin`,
a different zone, which is what makes the restored record's zone discriminating.

## What was measured and why

#125 criterion 1 records why the #6 qualification does not evidence zero rendering while the engine
is hidden and the display is off. `mVisible=false` is a lifecycle state the framework was told; the
CPU window confirms the screen-off state only at its endpoints; and a bounded CPU reading — even a
zero-tick one — can still contain rendered frames. What was missing was a frame count from the
producer's own pipeline, taken across a window whose display state is sampled *inside* it.

Criterion 2 asks for the same for restarts: the #6 pass covered wake, lock/unlock, surface
recreation, and process recreation, but never a reboot, so time, astronomy, observing location and
settings across a restart were unproven.

## Method

`device_layer.capture_producer_window` starts `atrace --async_start -a <pid> gfx view` on the
wallpaper process, reads `dumpsys power`'s display state every two seconds for the whole wall-clock
span of the trace, and collects the buffer with `atrace --async_dump`. The display reads are discrete
instants, not a continuous record, and the property that consumes them is named
`display_off_at_every_sample` for that reason. A produced frame
is a `queueBuffer` mark attributed to that pid; only the `B|<pid>|<name>` half of each mark pair is
counted, because the `E` half is emitted nameless on this device — both halves were checked against
a real capture rather than assumed.

A count is only evidence when the console is known to work, so the hidden window is bracketed by
visible control windows: one before it, on the home screen, and one after it. `phase_hidden_frames`
refuses to record the hidden window at all when the opening control finds no frames, and fails the
check when the closing control stops working, so a zero can never be an unread counter. An
unusable trace raises rather than reporting zero.

The reboot check captured the app's `shared_prefs` files, the home wallpaper component, the process
id and `uptime`, then issued `adb reboot`, waited for the device and for `sys.boot_completed`, and
read the same state back. The full qualification harness was then run over the restarted device.

## Observed results

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-12 | build, install | `./gradlew qualityGate :app:assembleDebug` passed (93 tasks); installed in place with `adb install -r`, no other device state changed |
| 2026-10-12 | producer trace, visible control | 42 producer frames over 20 s; display sampled 10 times inside the window, on for all 10 |
| 2026-10-12 | producer trace, hidden with the display off | **0 producer frames over 60 s**; the display was read 30 times inside the window, every 2 s, and read off at all 30 samples, so the screen-off state is sampled across the window rather than checked only at its endpoints |
| 2026-10-12 | producer trace, visible control after | 42 producer frames over 20 s; display sampled 10 times, on for all 10 — the tracer still worked after the hidden window, which is what makes its zero a measurement |
| 2026-10-12 | repeat on the second run | The full harness re-ran the same phase over the same restarted device: 42 frames / 20 s, 0 frames / 60 s with 30/30 samples off, 42 frames / 20 s. The counts reproduced exactly |
| 2026-10-12 | reboot, before | `uptime` `up 6 min`; wallpaper process pid 17682; home component `…AstronomicalClocksWallpaperService`; `date` `Mon Oct 12 23:09:22 CEST 2026`; timezone `Europe/Berlin` |
| 2026-10-12 | reboot, after | `uptime` `up 0 min`; wallpaper process pid 4032; home component still `…AstronomicalClocksWallpaperService`; `date` `Mon Oct 12 23:10:21 CEST 2026`; timezone `Europe/Berlin` |
| 2026-10-12 | reboot, saved state | Every `shared_prefs` file read back byte-identical: `observing_location.xml` still `{"version":1,"latitude":50.08,"longitude":14.42,"source":"MANUAL","zoneId":"Europe/Prague"}`, with `dial_settings.xml` (`sun`/`moon`/`zodiac_ring` all true), `dial_display.xml` (`size`/`horizontal`/`vertical`/`brightness` all 100/50/50/100), `appearance_settings.xml` (`SYSTEM`) and `location_permission.xml` unchanged. The re-created `ActivityThread.IDS.xml` matches the file the framework wrote before the restart |
| 2026-10-12 | reboot, rendering after | The harness then qualified the restarted build on the same artifact: the dial was found at 166.311°, screen-off/wake preserved it at 166.360°, surface recreation at 166.416°, process recreation at 166.437° with pid 4040 → 15733, and the site's midnight rollover resolved in `Europe/Prague` with residuals +0.088°, +0.245°, +0.232°, +0.159° against the zone's absolute hand angles |
| 2026-10-12 | device state restored | The screen was left on the home screen with the live wallpaper bound; no display-size override, night mode, or screen-state setting was left changed. The frame phase does drive the screen rather than only reading it — it sleeps it to hide the engine, wakes it afterwards, and returns it to the home screen — so this row is the end state the run was left in, not a phase that never touched it |

The capturable evidence is the two control rows beside the hidden row: the same tracer that finds 42
frames in 20 s on the home screen finds none in 60 s with the display off, and finds 42 again
afterwards. The reboot rows are the `uptime` and pid transitions, which no state read alone could
show, sitting beside byte-identical preference files.

## What this does not establish

This is one device, one build, and one screen state. The hidden window was measured with the app's
own home-screen engine hidden by the display going off; the lit lock screen, a hidden-but-on
display, and preview instances were not measured, and #6's preview phase still records that
preview-engine cleanup was not inspected.

The trace's classification is by event name: a redraw that never reaches `queueBuffer` would not be
counted as a frame. The categories enabled are `gfx` and `view`; an event the tracer does not emit
cannot be seen. `atrace` also samples the process's marks, not the display pipeline's output, so the
row is "the producer handed no frame to the consumer", which is the budget's wording, not a claim
about the panel.

The screen-off state is sampled, not recorded continuously: the display was read once every two
seconds, so a display that came on and went off again between two reads would leave no mark in these
rows. What the pass bounds is the display being off at every one of the 30 reads across the window,
which is strictly more than the endpoint check it replaces and strictly less than a continuous
display-state trace. The frame count is what carries the hard claim: it is counted from the same
trace for the whole window, and it is zero.

The 42 frames in 20 s of the visible control are a rate observation, not a cadence measurement:
they are not resolved per second, no dropped-frame analysis was done, and the count includes
whatever the engine drew while the harness was capturing the window.

Whether a reboot *keeps* the wallpaper bound was observed for this one restart; the pass did not
try a force-stop or a low-memory kill before or after it, and the historical record that
`am force-stop` drops the binding to the stock wallpaper still stands as recorded elsewhere.

The battery and CPU budgets are **not** measured here. They are #126's, and its report states which
of its criteria were measured and which were not; this report measures frames and restart
restoration only.
