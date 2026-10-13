# Battery budget B4: a pre-registered six-pair A/B/A/B drain comparison on battery (#126)

This file is committed **before** the run it describes. The pair count, the window length, and the
threshold are fixed here so they cannot be chosen after the result is known — the same anti-tuning
discipline [`docs/battery-budget.md`](../../battery-budget.md) applies to the threshold. The observed
results are filled in by a later commit that follows the run.

Test build: local debug `app-debug.apk`, APK SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d` — the same artifact the
[2026-10-13 pass](2026-10-13-test-126-drain-comparison-on-battery.md) used. The installed package was
re-hashed on the device for this run rather than assumed: `pm path` resolved to
`/data/app/~~VrvjbyHIc2ahZwCsRqNY_g==/io.github.godaniya.astronomicalclockswallpaper.debug-ZSJePfa4MHD3X14YSQrvew==/base.apk`,
whose `sha256sum` is the same digest. Vendor, model, serial, and firmware build are withheld per the
device-privacy policy.

## Why six pairs, and why not four

The recorded run's criterion was applied to the **paired 95% upper bound** while its threshold was
committed from a **single arm's 2σ**, and the two are not the same statistic. From that run's own eight
windows (rates in µA, arm A = live, arm B = stock `ImageWallpaper`):

| | arm A | arm B | pair difference |
| --- | --- | --- | --- |
| pair 1 | 389170 | 601478 | −212308 |
| pair 2 | 353980 | 300256 | +53724 |
| pair 3 | 353727 | 493133 | −139406 |
| pair 4 | 370397 | 562478 | −192081 |

The baseline mean is 489336 µA and the per-pair difference sd is **σ_d = 121446 µA = 24.819% of the
baseline mean**. The interval's half-width at n pairs is `t(n−1)·σ_d/√n`, so the upper bound is
`point + margin`:

| pairs | windows | device time | t(n−1) | margin | upper bound, holding the recorded −25.038% |
| --- | --- | --- | --- | --- | --- |
| 4 (as run) | 8 | 2.0 h | 3.182 | 39.49% | +14.45% — FAIL |
| 5 | 10 | 2.5 h | 2.776 | 30.81% | +5.77% |
| **6 (this run)** | **12** | **3.0 h** | **2.571** | **26.05%** | **+1.01%** |
| 8 | 16 | 4.0 h | 2.365 | 20.75% | −4.29% |

Two things this table says, and one it does not:

- At n=4 the criterion is **unsatisfiable for a zero-effect device**: the margin alone is 39.49%
  against a 12.0123% threshold, so the live arm would have to measure far *better* than the baseline
  to pass. The recorded failure is therefore not evidence that the wallpaper draws too much.
- Because the recorded point estimate is already −25.038%, the upper bound needs to fall only about
  13 points, which six pairs achieves on this projection.
- **It does not say six pairs suffice in general.** The projection holds the recorded point estimate
  fixed. Six pairs certify the budget only if the live-minus-stock difference comes in at or below
  about −14%; a live arm at parity with stock would need roughly **19 pairs** before the margin alone
  fits under the threshold. If this run fails, that is the number to re-specify from, not a guess.

## Pre-registered method

- **n = 6 alternating pairs, 12 windows**, in the order A/B/A/B/A/B/A/B/A/B/A/B. Twelve separate
  15-minute invocations, not one long one: the shared-device rule forbids holding the device for
  hours, and a killed invocation must not take completed windows with it.
- **15-minute windows**, the minimum the acceptance matrix documents, and the same length the pilot
  and the recorded run used, so the rates stay comparable.
- **Committed threshold unchanged at 12.0123%** (`FINAL_MAX_VISIBLE_DELTA_PCT`). Nothing in this run
  re-opens it; the run is more data under the same rule.
- **Fresh state directory**, `build/reports/battery-126-n6`, so no pilot window and no window from
  the recorded run can enter the comparison.
- **The wallpaper swap stays outside the harness.** Arm A is bound through the app's own preview
  (`am start -a android.service.wallpaper.CHANGE_LIVE_WALLPAPER` → *Startbildschirm*); arm B is the
  stock `ImageWallpaper`, reached by `am force-stop` of the package. The lock slot is never touched:
  its rebinding is not reversible over adb. Before every window the harness re-reads the bound
  component and refuses the window when it does not match the arm being recorded.
- **The device is on its own battery**, driven over wireless debugging, because `cmd battery unplug`
  freezes the fuel gauge on a USB-attached device and every window then records 0 µAh. The harness
  records `power_source` per window and refuses one that saw a charger appear.
- **Every window is checked, not assumed**: the display is sampled every 30 s inside it, the
  charge-counter delta and CPU come from the same two samples, and the record carries the probed
  `CLK_TCK` so the CPU percentage is not scaled by a guess.

## What this run will and will not establish

It will establish, for this device, this build, and these window conditions, whether the paired 95%
upper bound on the live-versus-stock discharge difference is below the committed threshold at six
pairs — and if it fails, the σ_d it measures sizes the pair count that would be needed.

It will **not** establish what the live arm does against the black static baseline, which stays
device-limited and unmeasured here for the reason the earlier reports give. It will not explain the
*direction* of the difference if the live arm again draws less than stock. One device, one build, one
screen state, one site, and a three-hour window of a device's day are not a general battery claim.

## Observed results

To be recorded by the commit that follows the run.

## Verdicts

To be recorded by the commit that follows the run.
