# Battery and Resource Acceptance Budget (#126)

This document establishes the empirical battery and resource acceptance criteria for Astronomical
Clock Wallpaper, anchoring the project's quality reputation and "battery efficiency" claims in
measured evidence.

## Background and industry anchors

Operating continuously as an Android system `WallpaperService`, the wallpaper must respect device
resource budgets and avoid degrading battery life:

- **Android Vitals excessive wake locks**: Android vitals reports partial wake lock use as
  *excessive* when all partial wake locks, added together, run for 2 or more hours in a 24-hour
  period. Because a wallpaper has no legitimate background processing requirement, this project
  enforces a **strict zero-tolerance** standard: zero partial wake locks and zero repeating alarms.
- **Rendering lifecycle contract**: the platform's own guidance for a wallpaper engine is that it
  "only use CPU while it is visible". Zero frames may be rendered while hidden or screen-off.
- **Hardware charge counter**: software battery estimates (for example the per-app attribution in
  `dumpsys batterystats`) are model-driven approximations with known noise. This protocol uses the
  fuel-gauge hardware charge counter (`dumpsys battery` `Charge counter`) as the primary ground
  truth, read with the device on its own battery when it is discharging and under simulated unplugged
  conditions (`cmd battery unplug`) otherwise.

## Acceptance Criteria Matrix

| Criterion | Target Budget | Verification Methodology |
| --- | --- | --- |
| **B1: Hidden/Screen-Off Frames** | **0 frames** (hard requirement) | Frame counter or producer-trace window while the engine is hidden and the display is off. **Not measured by this protocol**: the frame console is #125 criterion 1, which has since landed its own evidence — the [2026-10-12 producer-trace pass](testing/reports/2026-10-12-test-125-hidden-frames-and-reboot.md) reads 0 frames over 60 s, bracketed by visible controls that do render. This document does not claim it. |
| **B2: Wake Locks & Alarms** | **0 wake locks**, 0 repeating alarms | `dumpsys power` and `dumpsys alarm`, attributed by the app's UID and package; each dump's own census line must be present, so an unparsable probe is an error rather than a count of zero |
| **B3: Hidden CPU Utilization** | **≤ 0.1% of one core** over ≥ 10 min | `/proc/<pid>/stat` deltas over a screen-off duration whose display state is sampled throughout |
| **B4: Visible Screen-On Drain** | **≤ 12.0123% relative increase** vs the stock baseline — the committed noise floor, not the 5% provisional | A/B/A/B alternating runs of ≥ 15 minutes against stock `ImageWallpaper`; the paired 95% upper bound must also stay below the threshold. A black static wallpaper is the secondary baseline, required only where the device permits setting one |
| **B5: Visible CPU Utilization** | **≤ 3% of one core** at 1 Hz over ≥ 15 min | `/proc/<pid>/stat` process ticks during a visible home-screen window |
| **B6: Memory Stability** | **≤ 6144 kB** total-PSS growth, the budget the existing qualification harness runs at | `dumpsys meminfo` PSS sampling across a ≥ 30-minute window |

`scripts/battery_protocol.py` implements B2–B6 as separate bounded subcommands — `resources`,
`hidden`, `visible`, `pilot`, `memory`, and `report` — each of which writes one record and prints
its own evidence. No single invocation, and above all no single window, blocks for hours.

## Methodology and Noise Floor Policy

1. **Discharge mode**: a window is measured on the device's own battery when the device is genuinely
   discharging, and under a simulated unplug otherwise. On a device already on battery the harness
   leaves the live fuel gauge alone, because `cmd battery unplug` "force[s] battery unplugged,
   **freezing battery state**" (the device's own `adb shell cmd battery -h`) — applying it to a
   discharging device would re-create the frozen gauge the simulation exists to work around. When the
   device is on a charger, `cmd battery unplug` makes it report itself as running on battery while it
   stays on USB for telemetry, and `cmd battery reset` clears the override. The mode a window ran
   under is recorded as its `power_source` (`device` or `simulated`) and is part of the measurement
   identity the report checks, so a run cannot silently mix a live gauge with a frozen one; the
   override is cleared only when it was applied. The power source is re-sampled inside the window,
   and a window that saw a charger appear is refused as not a discharge measurement rather than
   recorded as one.
2. **Environmental Controls**:
   - Screen brightness pinned at a constant level with auto-brightness off.
   - Airplane mode enabled, to take the cellular and Wi-Fi radios out of the comparison.
   - Screen timeout raised for the duration of a window.
   - The device state a phase changes — brightness, auto-brightness, screen timeout, airplane mode,
     the stay-awake setting the visible windows need, and the display's own power state — is captured
     first and restored in a `finally`. Each step is restored independently, so one failing write
     cannot skip the others; every value is read back, and a restore that cannot be verified is
     recorded as a problem, printed, and makes the command exit non-zero rather than passing quietly.
3. **Dual Baselines**:
   - Primary baseline: OEM stock `ImageWallpaper`. This is the comparison B4 is gated on.
   - Secondary baseline: a black static wallpaper, so that a mostly dark dial on an OLED panel
     cannot flatter the result against a bright stock image. It is **required only where the device
     permits setting one**. The harness never swaps a wallpaper, and setting a static baseline
     rewrites the home image, which on a production build without root cannot be put back — so where
     it cannot be set, the report prints the black comparison as not measured and device-limited
     rather than staying silent, and what stays uncalibrated on that device is the dark-image control.
   - A state file that records only one of the two baselines is reported as exactly that: the report
     emits one block per documented baseline, so a missing one is visible rather than absent.
4. **Arm verification, not arm switching**: the harness never performs the wallpaper swap it
   measures. Before each window it checks that the device matches the requested arm — the live
   service component for arm A, and any static component for arm B — and refuses the window
   otherwise, so a mis-swapped wallpaper is recorded as a skipped window rather than a valid one.
   The screen's own luminance is recorded with every window and is deliberately **not** a gate: on
   the first device this was run against, the OEM stock wallpaper is a dark starfield whose median
   luminance sits at the black baseline's, so a brightness threshold would have refused valid stock
   windows while still not separating the two static baselines. Which static image a baseline
   carries is not decidable over ADB, and the report says so rather than implying it was checked.
5. **Noise Floor Rule**: B4 and B5 begin provisional because neither threshold can be stated before
   the device's own variation is known. The `pilot` subcommand is what replaces them: it runs
   repeated windows **on the live arm (A)**, of the same length as the final ones, and reports their
   mean, sample standard deviation, and the two-standard-deviation (2σ) noise floor. The live arm is
   the one whose discharge the B4 comparison is about, though arm B is accepted for a pilot that is
   deliberately calibrating the baseline instead; whichever arm ran is recorded in every pilot window.

   The floor must be read for what it is, because it is **not the statistic B4 tests**. A single
   arm's 2σ bounds how far one window's rate can move. B4's criterion is the paired 95% upper bound
   on the live-minus-baseline difference, and pairing removes the drift both arms share, so that
   interval is narrower than the single-arm spread the floor is taken from. A threshold set to the
   single-arm floor is therefore a *conservative* stand-in for the statistic actually tested, never a
   tighter one — and it cannot by itself predict whether a run will pass, because the interval's
   width also depends on the per-pair difference sd and on how many pairs were recorded. What that
   leaves uncalibrated is recorded rather than glossed: each report states the σ_d it observed, so the
   pair count a future run needs can be computed from measurement instead of guessed, and the
   threshold is committed to a fixed n before the run rather than re-read after it.

   The final threshold is the greater of the provisional value and the observed noise floor, and it is
   committed — the constants in `scripts/battery_protocol.py` are changed and committed, and a dated
   report under `docs/testing/reports/` records the pilot's values — **before** the final run. A pilot
   whose discharge channel never moved emits **no floor at all** and marks that channel unmeasured:
   a zero half-width over a zero mean is not a quiet device, it is an unread gauge, and committing it
   would present an unmeasured channel as a perfectly quiet one. Nothing measured in `pilot` windows
   is aggregated into the final comparison, so committing the threshold cannot be tuned to the result.

   Windowing is part of the criterion, not a default. Each phase's `--minutes` refuses a window
   shorter than its row above documents, and the report fails and discards a record whose own
   recorded duration is below that length, so a six-second window cannot be reported as a
   ten-minute measurement.
6. **Report**: the `report` subcommand aggregates the recorded windows. It refuses to pair a window
   sequence that is not an A/B alternation, compares each baseline separately, refuses fewer than
   the four alternations this issue mandates, and uses the Student t critical value for the actual
   degrees of freedom — expanded above the tabulated range rather than replaced by the normal value.
   B5 is checked against the average visible CPU its row defines, with the worst window reported
   beside it, and B6 against the worst recorded run. Every criterion prints pass, fail, or **not
   measured**, and any recorded restore failure is printed as a failure. A criterion that was never
   measured is reported as not measured, never as a pass.

## Sources

Retrieved 2026-10-12. Both anchors are quoted from primary documentation rather than restated.

- **Android vitals, partial wake locks** — [Excessive partial wake locks in Android vitals](https://developer.android.com/google/play/vitals/excessive-wakelock):
  "Android vitals reports partial wake lock use as *excessive* when all of the partial wake locks,
  added together, run for 2 or more hours in a 24-hour period." The same page records that the
  metric counts a wake lock "only if the wake lock is held when the app is in the background or is
  running a foreground service", that audio, location, and `JobScheduler` user-initiated wake locks
  are currently exempt, and that exceeding the threshold in more than 5% of sessions over 28 days
  can affect Play visibility. The zero-tolerance budget above is stricter than the vitals threshold
  by choice, as a wallpaper has no background work to justify any of it.
- **Platform rendering guidance** — [`WallpaperService.Engine#onVisibilityChanged(boolean)`](https://developer.android.com/reference/android/service/wallpaper/WallpaperService.Engine#onVisibilityChanged(boolean)).
  The javadoc, as pinned in AOSP `frameworks/base` at tag
  [`android-16.0.0_r1`](https://android.googlesource.com/platform/frameworks/base/+/android-16.0.0_r1/core/java/android/service/wallpaper/WallpaperService.java),
  reads: "Called to inform you of the wallpaper becoming visible or hidden." followed by "*It is
  very important that a wallpaper only use CPU while it is visible.*"
- **Tooling** — [`adb` shell commands](https://developer.android.com/tools/adb#dumpsys): `dumpsys`
  for the service state, and the device's own `cmd battery` help for the unplug/reset commands.
- **Statistics** — the two-sided 95% Student t critical values in `scripts/battery_protocol.py`
  come from the table in the NIST/SEMATECH e-Handbook of Statistical Methods, section 1.3.6.7.3
  ([`1.3.6.7.3`](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3672.htm)); beyond 30
  degrees of freedom the normal value 1.960 is used, where the two agree to three decimals.

## Recorded runs

Each run is a dated report under [`testing/reports/`](testing/reports/), carrying its device's
Android/API level, APK checksum and source revision, and the limits of what it measured:

- [2026-10-12 pilot and committed thresholds](testing/reports/2026-10-12-test-126-battery-pilot-noise-floor.md)
  — four 15-minute windows on one device, the floors they give, and the thresholds committed from them.
- [2026-10-12 B2, B3 and B6](testing/reports/2026-10-12-test-126-hidden-resources-memory.md)
  — 0 wake locks, 0 alarms, 0.0397% hidden CPU over 10 minutes, and +1633 kB PSS growth over 30 minutes.
- [2026-10-12 A/B/A/B drain comparison](testing/reports/2026-10-12-test-126-drain-comparison.md)
  — B5 measured and **failed** at 3.4552% of one core against the 3% budget, and B4 **not measured**
  because the fuel gauge on that device reports a constant charge counter while it is on USB.
- [2026-10-13 pilot, committed thresholds and A/B/A/B on battery](testing/reports/2026-10-13-test-126-drain-comparison-on-battery.md)
  — the first pass with the device genuinely discharging, so B4 is measurable: the live discharge
  channel's floor is 12.0123% of the mean, which raised the committed B4 threshold from the 5%
  provisional to 12.0123%, and the comparison then **failed** on its 95% upper bound (+14.449%)
  while B5 was measured and **failed** again at 3.7479% of one core.

The reports are the record. A state file is working data and is reset between runs so that the
pilot's windows cannot enter the final comparison; the floors the thresholds were committed from are
carried in `scripts/battery_protocol.py` beside them.
