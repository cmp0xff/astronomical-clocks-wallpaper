# Research evidence: 1 Hz vs 1-minute visible redraw cadence and B5 budget (#136)

## Overview

Physical-device research investigation for [#136: research(perf): gather evidence on whether the 1 Hz visible redraw keeps CPU above the B5 budget](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/136).

The budget defined in [`docs/battery-budget.md`](../../battery-budget.md) specifies:

> **B5: Visible CPU Utilization**: **≤ 3% of one core** over ≥ 15 min while visible.

B5 states a CPU budget, not a redraw cadence. The ≤ 3% threshold is unchanged; the cadence a window
measures is whatever build the record's `apk_sha256` identifies. The former "at 1 Hz" wording in the
criterion described the cadence then shipped, and the earlier rows below were measured under it.

Prior empirical qualification runs ([PR #126 / report `2026-10-12-test-126-drain-comparison.md`](2026-10-12-test-126-drain-comparison.md) and [`2026-10-13-test-126-drain-comparison-on-battery.md`](2026-10-13-test-126-drain-comparison-on-battery.md)) repeatedly found the live wallpaper engine consuming 3.28%–3.65% of one core while visible on the home screen, consistently breaching the 3.0% B5 limit.

Issue #136 framed the core empirical question:

1. Does the 1 Hz redraw cadence account for the budget breach?
2. Does reducing the redraw cadence to once per minute bring CPU consumption within the ≤ 3.0% B5 budget?
3. Is a once-per-minute cadence visually distinguishable or aesthetically jarring?
4. What mechanisms account for the frame execution cost?

This report records controlled physical-device measurements comparing the baseline 1 Hz visible tick cadence against a 1-minute tick cadence under identical environmental controls.

---

## Hardware & Environment Attribution

- **Target Platform**: Physical device running Android 16 (API 36), display 1080x2408 (480 dpi).
- **Privacy Policy Compliance**: Hardware serial number, OEM name, marketing model, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy).
- **Saved Observing Site**: `Europe/Amsterdam`, read from the device's saved record for both builds.
- **Measured Dial Geometry**: centre `(540, 1217)` px, radius `464.4` px, frame `1080x2408` (from the engine's own `DialDisplay` diagnostics).
- **Clock-Tick Rate**: `getconf CLK_TCK` returns `100` on this device, so one CPU tick is 10 ms.
- **Power Source**: `simulated` discharge (`cmd battery unplug` applied by the protocol on the USB
  connection) with brightness pinned at 128, auto-brightness disabled, display held awake, and
  airplane mode enabled. The harness restores every setting it changes; each record reports
  `restore_problems: []`.
- **Baseline Test Build**: local debug `app-debug.apk`, SHA-256
  `3d66b8dbf5c227bb6fc7ce97e5484c4b5d8e550fe28b0f3977ce8bb344a61931`, built from `9ae118b` (1 Hz
  tick cadence).
- **Modified Test Build**: local debug `app-debug.apk`, SHA-256
  `3ce7bf7a22f0c1da314fba7d54896eb2f38a79723673c738ca5f427b6cf1acd2`, built from `ca40640` (the PR
  head after the viewport-redraw commit; this report's own commit does not change app sources).

---

## Build identity

The artifact digest matters here because a previous revision mixed up two builds:

- Rebuilding `9ae118b` with `./gradlew :app:assembleDebug` produces `3d66b8db…`, reproducibly: two
  separate worktrees built the same digest. This is the baseline used below.
- The pre-review report's baseline digest `3ef8bf79…` rebuilds exactly from `5efae15`, the tree the
  [2026-10-12 battery pass](2026-10-12-test-126-drain-comparison.md) used. It is **not** the
  `9ae118b` artifact: app sources changed between `5efae15` and `9ae118b` (location/settings and
  tablet-layout work, `#128`, `#131`, `#137`), and those changes are part of the APK. The older row
  below is therefore kept as historical but marked superseded.
- The pre-review modified digest `d5532f31…` was built from `525eec5`, before the viewport-redraw
  change; it is also superseded, because that change alters the APK.
- The final APK was produced by `./gradlew qualityGate :app:assembleDebug` from that same tree and
  passed `scripts/verify-apk.sh` (package, SDK metadata, wallpaper declaration, sole permission,
  debug flag, signature, bundled licence, and its recorded SHA-256).

The renderer itself is unchanged by this PR: it changes when frames are requested and adds a guarded
redraw on viewport changes, not how a frame is drawn. That is what the fixed-instant comparison below
verifies.

---

## Controlled Measurement Method

Both arms were measured using `python3 scripts/battery_protocol.py -s <serial> --state <dir> visible --arm A --minutes 15` targeting the physical device:

1. **Environmental controls**:
   - Pinned screen brightness to 128 (manual brightness mode).
   - Raised screen timeout to prevent premature sleep.
   - Pinned `stay_on_while_plugged_in` to keep the display active.
   - Enabled airplane mode.
   - Simulated discharge via `cmd battery unplug`, applied by the protocol so it can restore it.
   - All settings restored in `finally` and validated by readback (`restore_problems: []`).
2. **Measurement window**:
   - Continuous 15-minute home-screen visible sampling (30 samples at 30-second intervals checking `screen_on=True`, `on_battery=True`, `home_in_front=True`, `keyguard_locked=False`).
   - Process CPU ticks measured across `/proc/<pid>/stat` deltas over the window.
   - The APK digest is read back from the installed package, not taken from the host file.

---

## Empirical Results: Visible CPU (B5) Comparison

| Date | Build / Cadence | Window Duration | Power Source | Mean Discharge Rate | Process CPU % (of 1 core) | B5 Budget (≤ 3.0%) |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-17 | Superseded: `3ef8bf79…` (1 Hz, `5efae15` tree) | 914.7 s | simulated | 0 µA (frozen gauge) | 3.2810% | FAIL |
| 2026-10-17 | Superseded: `d5532f31…` (1 min, pre-review `525eec5`) | 915.8 s | simulated | 0 µA (frozen gauge) | 0.0863% | PASS |
| 2026-10-17 | Baseline `3d66b8db…` (1 Hz, `9ae118b`) | 914.4 s | simulated | 0 µA (frozen gauge) | **3.3869%** | **FAIL** (> 3.0%) |
| 2026-10-17 | Final `3ce7bf7a…` (1 min + viewport redraw) | 915.5 s | simulated | 0 µA (frozen gauge) | **0.0786%** | **PASS** (≤ 3.0%) |

### Key Metrics Summary

- **Baseline CPU**: 3.3869% of one core (`cpu_pct` 3.38691871584622 over 914.4 s at 100 ticks/s,
  about 3,097 ticks, i.e. about 30.97 s of CPU).
- **Final CPU**: 0.0786% of one core (`cpu_pct` 0.07864676024160773 over 915.5 s at 100
  ticks/s, about 72 ticks, i.e. about 0.72 s of CPU).
- **Absolute CPU Reduction**: **-3.3083%** of one core.
- **Relative CPU Reduction**: **97.68%** drop in visible CPU utilization.

Both 15-minute runs passed all 30 environmental integrity samples with zero restore problems. B4 is
not measured by this pass (no stock baseline arm was run), so the report prints `NOT MEASURED` for
it and makes no drain claim.

---

## Fixed-instant frame comparison (#136 criterion 3)

Issue #136 asks for a side-by-side comparison at a fixed virtual time: the rendered output where the
hand has not stepped must be identical, and a stepped frame must be checked rather than assumed.

**Method.** The debug receiver accepts `--es instant <ISO>` and fixes the virtual clock, so both
builds render the same instant on demand. For each capture the harness sent the instant broadcast,
confirmed the service's acceptance log line, captured the screen, and checked the engine's reported
dial placement before and after the capture (`scripts/device_layer.py` `capture_frame`). Comparisons
are over a 1004×1004 crop centred on the dial, excluding the status bar; the crop is
`(38, 715)`–`(1042, 1719)` in the 1080×2408 frame. Pixel data is RGBA, stride 4; no image library is
used. With `X = 2026-06-21T12:34:30Z`. The baseline captures were taken with the 1 Hz APK installed;
the final captures were taken after re-installing the modified APK on the same device, with the same
saved site, palette, brightness, and display geometry.

| Pair | Question | Changed pixels | % of crop | Bounding box | Max channel Δ | Mean abs channel Δ | Hand-angle Δ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline `X` vs baseline `X` | capture determinism | 0 | 0.0000% | — | 0 | 0 | 0.000000° |
| Final `X` vs final `X` (recaptured) | capture determinism | 0 | 0.0000% | — | 0 | 0 | 0.000000° |
| Baseline `X` vs final `X` | **identity across builds** | **0** | **0.0000%** | — | **0** | **0** | **0.000000°** |
| Baseline `X+1min` vs final `X+1min` | identity at a second instant | **0** | **0.0000%** | — | **0** | **0** | **0.000000°** |
| Baseline `X` vs baseline `X+1s` | 1-second step | 4,546 | 0.4510% | (307,866)–(822,1381) | 48 | 0.005483 | +0.004813° |
| Baseline `X` vs baseline `X+1min` | 1-minute step, 1 Hz build | 15,043 | 1.4923% | (307,866)–(823,1381) | 228 | 0.457068 | +0.282779° |
| Final `X` vs final `X+1min` | 1-minute step, minute build | 15,043 | 1.4923% | (307,866)–(823,1381) | 228 | 0.457068 | +0.282779° |

Crop SHA-256 values record the same result more compactly: the two `X` crops and the two captures of
the final build at `X` all hash to `719dc8eb8a31b9bde151d6f70c64375bf6a1b73518f4761dad49a3c8912eaba9`,
and both builds' `X+1min` crops hash to
`a578724978859c420a0b46bde7cf5ba7968df6c881b043bfccac27befd6f99b4`.

**Full frames are not byte-identical, and should not be.** Their only differing pixels are in the
status bar (`y ≤ 52`: the system clock and notification icons); comparing full frames gives 98–4,101
changed pixels entirely outside the dial crop. The dial content below the status bar is identical.

**The step is the expected one.** `ClockState` derives the civil hand from
`LocalTime.toSecondOfDay() / 240`, so one minute is exactly 0.25° and one second 0.004167°. At the
measured 464.4 px radius that is 2.03 px and 0.034 px of arc respectively. The measured hand deltas
are +0.282779° and +0.004813°; the +0.0328° residual on the minute step is about 0.27 px at that
radius, consistent with the hand-edge rasterization floor (the earlier 30-minute virtual-time smoke
check recorded a +0.074° residual against +7.500°). The minute-step statistics are identical between
the two builds, which is the expected outcome: the endpoint frames are the same; only their cadence
differs.

The 1-second pair is not zero pixels, and the explanation is rasterization rather than motion: a
0.034 px displacement re-rasterizes the hand's antialiased edge, which changes 4,546 crop pixels with
a maximum channel delta of 48 and a mean absolute channel delta of 0.0055. The 1-minute pair moves
the same pixels by up to 228 levels, which is the visible step.

The changed-pixel bounding box spans the dial's moving content — the civil hand, the zodiac ring and
its signs, and the Sun and Moon markers — not a clipped or shifted dial.

---

## Observed minute-step transition

The fixed-instant comparison pins *content*, not *cadence*. To observe the running schedule, the
debug clock was reset to system time and the screen was sampled with raw `screencap` frames
(`decode_frame`, not `capture_frame`, whose diagnostics broadcast would force a draw) for 75 s. The
sample interval was about 0.8 s; the dial crop was compared frame to frame by SHA-256.

- Window: `2026-10-17T18:07:50.546Z` – `2026-10-17T18:09:04.931Z`, 98 captures.
- Transitions: exactly **2**, each at a minute boundary and each a single step between consecutive
  captures:
  - last pre-boundary `18:07:59.148Z`, first post-boundary `18:08:00.003Z` (bracket 0.855 s);
  - last pre-boundary `18:08:59.176Z`, first post-boundary `18:09:00.010Z` (bracket 0.834 s).
- The 78 captures between the two transitions — and the 11 frame-to-frame comparisons before the
  first — all had identical crop hashes: no intermediate redraws between boundaries.

---

## Visual Distinction and Aesthetics Analysis

Issue #136 requires checking whether the slower cadence is visually distinguishable from 1 Hz at the measured dial size:

1. **Angular velocity**:
   - The Prague Orloj hour hand completes one full revolution (360°) every 24 hours (1440 minutes = 86,400 seconds).
   - Angular rate: 0.25° per minute = 15 arcminutes per minute ≈ 0.004167° per second.
   - The zodiac ring is **not static**. `OrlojProjection.zodiacCircle` offsets the ring's centre
     along the local sidereal angle, and `eclipticPoint` rotates the ecliptic frame by the same angle,
     so the ring, its signs, and the Sun/Moon markers advance at the sidereal rate of
     360°/86164.09 s, i.e. **0.2507° per minute** — slightly faster than the civil hand.
   - Time-invariant plate elements — the Roman numeral ring, the temporal-hour curves, and the plate
     shading — are genuinely static and render identically; the fixed-instant comparison above shows
     they are byte-identical across builds.
2. **Display geometry & pixel displacement** (measured placement):
   - Dial radius is 464.4 px on the 1080x2408 (480 dpi) display.
   - At 1 Hz: arc displacement per frame is 2π × 464.4 × 0.004167°/360° ≈ 0.034 px (sub-pixel,
     imperceptible frame to frame).
   - At 1-minute cadence: arc displacement per tick is 2π × 464.4 × 0.25°/360° ≈ 2.03 px; the
     sidereal ring moves ≈ 2.03 px per minute (0.2507°).
   - At 480 dpi, 2.03 physical pixels corresponds to ≈ 0.68 dp or ≈ 0.107 mm of physical arc.
3. **Observational impact**:
   - For an astronomical 24-hour clock dial, a ~0.11 mm step every 60 seconds is imperceptible during
     passive viewing; the capture comparison records it as a localized change in the moving elements,
     not as a jump in the plate or the frame.
   - Stepping at minute boundaries aligns intuitively with civil minute rollovers (matching standard mechanical or digital clock expectations).
   - The one measured minute step changes 1.49% of the dial crop, with a maximum channel delta of 228
     on antialiased edges; the remaining 98.5% of the crop is unchanged.

---

## Mechanism Breakdown: What Accounts for the Frame CPU Cost

The measured 97.7% reduction in CPU utilization when reducing frame rate from 1 Hz (about 915 frames
per 15 min) to 1/60 Hz (about 15 frames per 15 min) reveals the CPU consumption mechanism:

1. **Per-frame CPU consumption**:
   - At 1 Hz, 914.4 s used about 30.97 seconds of single-core CPU across about 915 frames, or
     **about 33.9 ms of CPU time per frame**.
   - At 1-minute cadence, 915.5 s used about 0.72 seconds of CPU across about 15 frames — roughly
     47 ms per active frame including looper overhead — plus any viewport redraws, which a static
     home screen did not request.
2. **Components of the frame pipeline**:
   - **Coordinate calculations**: the wallpaper calls `AstronomyCalculator.dialGeometry`, which asks
     Astronomy Engine for the Sun's and Moon's geocentric ecliptic longitudes, the true-obliquity
     rotation, local sidereal time, and the moon phase. It does not call `sky()`, so a wallpaper frame
     does not compute planets, fixed stars, horizontal coordinates, refraction, or rise/set events.
   - **Canvas draw calls**: Canvas save/restore, transformation matrices for the zodiac dial, drawing
     dynamic sun/moon glyphs, anti-aliased hand lines, and counterweight geometries.
   - **Surface locking and synchronization**: the frame draws on the software canvas from
     `SurfaceHolder.lockCanvas()` and posts it with `unlockCanvasAndPost()`; there is no hardware
     canvas. This synchronization runs between the application Looper and SurfaceFlinger.
3. **Conclusion on mechanism**:
   - The CPU cost was predominantly the Canvas/ephemeris rendering loop rather than background idle
     overhead: removing 59 of 60 frames per minute dropped the same app on the same device under
     the same controls from 3.3869% to 0.0786%.
   - The viewport redraw added for launcher scrolling is draw-only, coalesced, and does not re-arm the
     tick; a static home screen does not request it.

---

## Verification & Smoke Checks (`device_smoke.py`)

Prior to the 15-minute measurement, the modified 1-minute cadence build was verified on the device using `scripts/device_smoke.py`:

```
Targeting ADB device: <withheld> (API 36)
Logcat start marker: 10-17 14:58:50.000
Waking the screen and showing the home screen...
Resetting virtual clock...
Capturing baseline frame t0...
Baseline hand angle at t0: 44.558°
Advancing virtual time +30 minutes via debug broadcast...
Capturing frame t1 at +30m...
Hand angle at t1 (+30m): 52.132°
Observed angular advance: 7.574° (expected: 7.500°, residual: +0.074°)
Testing surface recreation...
Restoring virtual clock, display size, and screen state...
Renderer log scan inconclusive: no matching warning records.
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-17 | virtual time travel (+30m) | Hand advanced 7.574° against 7.500° expected, residual +0.074°; broadcast confirmed by service log |
| 2026-10-17 | surface recreation | effective 1080x2408, `wm size 1080x2000` (active as requested) then `wm size reset` (verified); hand redrawn |
| 2026-10-17 | renderer log | Inconclusive: no matching warning records; rendering was not verified |

---

## Limitations

- **A frozen clock pins content, not cadence.** The fixed-instant captures show that a frame at a
  given instant is identical between builds; they do not exercise the schedule. The 3,211 ms delay
  from 12:34:56.789 is pinned by a unit test instead, and the transition section observes the running
  schedule directly.
- **Screen captures bracket the transition.** Sampling was about 0.8 s apart, so the transition is
  located between the last pre-boundary and first post-boundary capture, not at the exact vsync.
- **The dial crop excludes the status bar**, which legitimately differs between captures (clock and
  notification icons). Full-frame digests are recorded but are not the identity claim.
- **The viewport redraw is host-tested only.** Launcher scrolling was not captured on the device; the
  viewport callbacks, coalescing, and lifecycle guards are covered by Robolectric tests.
- **One window per build**, one device, one saved site, night palette, and pinned brightness; the
  result is a measurement under those controls, not a general battery guarantee.
- **Simulated discharge.** `cmd battery unplug` on USB freezes the fuel gauge, so the discharge
  channel reads 0 by construction and B4 is not measured. B5 uses `/proc/<pid>/stat` CPU ticks, which
  the simulation does not affect.
- **CPU is quantized at `CLK_TCK=100`.** One tick is 0.01 s of CPU, so one tick over the 914.4 s
  window is `(0.01 / 914.4) × 100 ≈ 0.0011%` of one core.
- **The hand-angle detector has a rasterization floor.** It reports +0.282779° for an exact +0.25°
  minute step (+0.0328°, about 0.27 px at the detected radius); the 30-minute smoke check's residual
  was +0.074°. The detector is a corroborating measure, not a second rendering reference.
- **No presentation-buffer trace** accompanies the captures; the transition count is a
  screen-capture observation.
