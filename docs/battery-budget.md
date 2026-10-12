# Battery and Resource Acceptance Budget (#126)

This document establishes the empirical battery and resource acceptance criteria for Astronomical Clock Wallpaper, anchoring the project's quality reputation and "battery efficiency" claims in measured evidence.

## Background and industry anchors

Operating continuously as an Android system `WallpaperService`, the wallpaper must respect device resource budgets and avoid degrading battery life:

- **Android Vitals excessive wake locks**: Android Vitals penalizes apps holding non-exempt partial wake locks for 2 or more cumulative hours within a 24-hour period. Because a wallpaper has no legitimate background processing requirement, this project enforces a **strict zero-tolerance** standard: zero partial wake locks and zero repeating alarms.
- **Rendering lifecycle contract**: Android platform guidance mandates that a live wallpaper must halt drawing loops immediately when obscured, asleep, or lacking a surface. Zero frames may be rendered while hidden or screen-off.
- **Hardware charge counter**: Software battery estimates (e.g. per-app attribution in `dumpsys batterystats`) are model-driven approximations with known noise. This protocol uses the fuel-gauge hardware charge counter (`dumpsys battery` `Charge counter`) under simulated unplugged conditions (`cmd battery unplug`) as the primary ground truth.

## Acceptance Criteria Matrix

| Criterion | Target Budget | Verification Methodology |
| --- | --- | --- |
| **B1: Hidden/Screen-Off Frames** | **0 frames** (hard requirement) | Frame counter query (`dumpsys gfxinfo` / SurfaceFlinger latency) across a 10-minute screen-off window |
| **B2: Wake Locks & Alarms** | **0 wake locks**, 0 repeating alarms | Inspection of `dumpsys power` and `dumpsys alarm` during idle and hidden phases |
| **B3: Hidden CPU Utilization** | **≤ 0.1% of one core** over 10 min | `/proc/<pid>/stat` deltas over an obscured/screen-off duration |
| **B4: Visible Screen-On Drain** | **Provisional ≤ 5% relative increase** vs baseline | A/B/A/B alternating 15-minute runs against stock `ImageWallpaper` and a black static wallpaper |
| **B5: Visible CPU Utilization** | **Provisional ≤ 3% of one core** at 1 Hz | `/proc/<pid>/stat` process ticks during visible home-screen ticking |
| **B6: Memory Stability** | **Within existing PSS budget** | `dumpsys meminfo` PSS sampling across a 30-minute window |

## Methodology and Noise Floor Policy

1. **Simulated Discharge**: Using `cmd battery unplug`, the device operates from its battery fuel gauge while remaining accessible via USB ADB for telemetry collection. The simulation is reset via `cmd battery reset` upon completion or interruption.
2. **Environmental Controls**:
   - Screen brightness pinned at a constant level (manual slider or fixed lux).
   - Airplane mode enabled to eliminate cellular/Wi-Fi radio noise.
   - Background tasks and non-essential apps stopped.
3. **Dual Baselines**:
   - Primary baseline: OEM stock `ImageWallpaper`.
   - Secondary baseline: Pure black static wallpaper (to account for OLED panel characteristics where black pixels consume zero power).
4. **Provisional vs Final Budget (Noise Floor Rule)**:
   Criteria B4 and B5 are initially provisional. An initial pilot run measures the device's baseline variance (noise floor). If the 2σ variance of the baseline measurements exceeds the provisional target, the final threshold is updated to match the empirical noise floor before final qualification runs, preventing retrospective adjustment of acceptance targets.
