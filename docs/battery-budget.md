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
  fuel-gauge hardware charge counter (`dumpsys battery` `Charge counter`) under simulated unplugged
  conditions (`cmd battery unplug`) as the primary ground truth.

## Acceptance Criteria Matrix

| Criterion | Target Budget | Verification Methodology |
| --- | --- | --- |
| **B1: Hidden/Screen-Off Frames** | **0 frames** (hard requirement) | Frame counter or producer-trace window while the engine is hidden and the display is off. **Not measured by this protocol**: the frame console is #125 criterion 1, and this document does not claim it. |
| **B2: Wake Locks & Alarms** | **0 wake locks**, 0 repeating alarms | `dumpsys power` and `dumpsys alarm`, attributed by the app's UID and package |
| **B3: Hidden CPU Utilization** | **≤ 0.1% of one core** over 10 min | `/proc/<pid>/stat` deltas over a screen-off duration whose display state is sampled throughout |
| **B4: Visible Screen-On Drain** | **Provisional ≤ 5% relative increase** vs baseline | A/B/A/B alternating 15-minute runs against stock `ImageWallpaper` and a black static wallpaper; the paired 95% upper bound must also stay below the threshold |
| **B5: Visible CPU Utilization** | **Provisional ≤ 3% of one core** at 1 Hz | `/proc/<pid>/stat` process ticks during a visible home-screen window |
| **B6: Memory Stability** | **≤ 6144 kB** total-PSS growth, the budget the existing qualification harness runs at | `dumpsys meminfo` PSS sampling across a 30-minute window |

`scripts/battery_protocol.py` implements B2–B6 as separate bounded subcommands — `resources`,
`hidden`, `visible`, `pilot`, `memory`, and `report` — each of which writes one record and prints
its own evidence. No single invocation, and above all no single window, blocks for hours.

## Methodology and Noise Floor Policy

1. **Simulated Discharge**: `cmd battery unplug` makes the device report itself as running on
   battery while it stays on USB for telemetry, and `cmd battery reset` clears the override. Both
   commands are documented by the device's own `adb shell cmd battery -h`.
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
   - Primary baseline: OEM stock `ImageWallpaper`.
   - Secondary baseline: a black static wallpaper, so that a mostly dark dial on an OLED panel
     cannot flatter the result against a bright stock image.
4. **Arm verification, not arm switching**: the harness never performs the wallpaper swap it
   measures. Before each window it checks that the device matches the requested arm — the live
   service component for arm A, and any static component for arm B — and refuses the window
   otherwise, so a mis-swapped wallpaper is recorded as a skipped window rather than a valid one.
   The screen's own luminance is recorded with every window and is deliberately **not** a gate: on
   the first device this was run against, the OEM stock wallpaper is a dark starfield whose median
   luminance sits at the black baseline's, so a brightness threshold would have refused valid stock
   windows while still not separating the two static baselines. Which static image a baseline
   carries is not decidable over ADB, and the report says so rather than implying it was checked.
5. **Noise Floor Rule**: B4 and B5 are provisional on purpose. The `pilot` subcommand runs repeated
   baseline windows of the same length as the final ones and reports their mean, sample standard
   deviation, and the two-standard-deviation (2σ) noise floor. The final threshold is the greater of
   the provisional value and the observed noise floor, and it is committed — that is, the constants
   in `scripts/battery_protocol.py` are changed and committed, and a dated report under
   `docs/testing/reports/` records the pilot's values — **before** the final run. Nothing measured
   in `pilot` windows is aggregated into the final comparison, so committing the threshold cannot be
   tuned to the result.
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
