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

The pass ran from 2026-10-13 23:31 to 2026-10-14 03:14 local, 3 h 43 m of wall clock carrying 3 h 04 m
of measured windows — the twelve durations below sum to 11020.3 s; the rest is the arm swaps and one
interruption below.

| Window | Arm | Duration | Charge-counter delta | Discharge rate | CPU | In-window samples |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | A (live) | 917.3 s | 162000 uAh | 635805 uA | 4.2540% of one core | 30/30 display on, on battery |
| 2 | B (stock) | 919.6 s | 193500 uAh | 757512 uA | — no process to read, as expected for a static wallpaper | 30/30 |
| 3 | A | 919.7 s | 94500 uAh | 369883 uA | 4.4219% | 30/30 |
| 4 | B | 916.4 s | 121500 uAh | 477296 uA | — | 30/30 |
| 5 | A | 917.6 s | 72000 uAh | 282485 uA | 4.3125% | 30/30 |
| 6 | B | 917.8 s | 76500 uAh | 300062 uA | — | 30/30 |
| 7 | A | 919.6 s | 81000 uAh | 317101 uA | 4.3128% | 30/30 |
| 8 | B | 916.3 s | 144000 uAh | 565727 uA | — | 30/30 |
| 9 | A | 919.4 s | 81000 uAh | 317180 uA | 4.3172% | 30/30 |
| 10 | B | 918.1 s | 139500 uAh | 547015 uA | — | 30/30 |
| 11 | A | 917.7 s | 76500 uAh | 300092 uA | 4.4164% | 30/30 |
| 12 | B | 920.8 s | 144000 uAh | 562982 uA | — | 30/30 |

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-13 | arm verification | Each of the twelve records names the component it was measured against — the live service for arm A, the static `ImageWallpaper` for arm B — so no window was recorded against the wrong arm |
| 2026-10-13 | power source | Every window recorded `power_source: device` and `on_battery` true at all 30 in-window samples, so no window was measured across a charger appearing |
| 2026-10-13 | device state restored | All twelve records carry `restore_problems: []`; brightness, brightness mode, the timeout, airplane mode and the stay-awake setting were read back at their pre-window values |
| 2026-10-13 | B2 | `resources`: 0 partial wake locks for uid 10026, 0 pending alarms and 0 jobs for the package. Partial locks only: the probe at this revision enumerated `PARTIAL_WAKE_LOCK` and nothing else, while the budget is now 0 wake locks of any type and the probe reconciles every `<TYPE>_WAKE_LOCK` entry against the dump's own census. Both landed later in the same pull request, so this row evidences the partial-lock criterion rather than the current one |
| 2026-10-13 | measurement identity | One `apk_sha256` and one `api_level` across all thirteen records, and one `power_source` and one `CLK_TCK` across the twelve windows, so the set aggregates as one configuration — the `resources` census carries neither of those last two, because it is not a discharge or CPU measurement. All thirteen come from the one device this report names. The `device_id` field the harness now records with every window postdates this pass, so these records carry none of it: the digest named in this report's header is the APK's, which every record does carry, and not the device's |
| 2026-10-13 | harness revision | The windows were measured at `594d68c`; the `resources` record carries `9808562`. `git diff 9808562 594d68c -- scripts/` is empty, so the same harness code measured both |

### Interruptions

One, recorded rather than smoothed over. **The twelfth window aborted at 750 s of 900 s**: the
wireless-debugging transport dropped mid-window, `adb` reported the device offline, and the in-window
restore could not run, so the device was left with airplane mode still enabled. The phase wrote no
record — a window that did not finish is not a measurement — and the aborted restore was surfaced on
stderr rather than swallowed. Airplane mode was cleared by hand, the device re-checked as arm B with
the display on and the launcher in front, and the window re-run from the start. **The re-run is the
twelfth pre-registered window, not a fifth-through-eighth added after seeing a result**: the count was
fixed before the pass, and no window was dropped or added for how it came out.

### Gauge resolution, measured from this run's own counters

Every charge-counter reading in the twelve windows is a multiple of **4500 uAh**, and there is no
smaller divisor across them, so the quantum this device reports at is 4.5 mAh rather than the 1000 uAh
the 2026-10-13 report carried before this pass measured it. That is 2.3%–6.2% of a single window's
delta, which is the same order
as the threshold the criterion is judged against. It is a property of the gauge and it applies to both
arms of a pair, so the paired difference is far less exposed to it than an absolute rate would be —
but any reading of a single window carries it, and the per-window rates below should be read with that
in mind.

## Verdicts

| Date | Criterion | Observed | Verdict |
| --- | --- | --- | --- |
| 2026-10-14 | **B4, relative increase against stock** | **−30.775%** point estimate; **95% CI upper bound −11.615%**; 6 pairs, df 5, t 2.571, σ_d 97676 uA (18.254% of the 535099 uA baseline mean), margin 19.16% | **PASS.** Both of B4's lines are inside the committed 12.0123% threshold, and the upper bound is inside it with 23.6 points to spare. The live wallpaper drew less than the stock baseline in **every one of the six pairs**, by between 17577 uA and 262890 uA |
| 2026-10-14 | **B5, visible CPU** | mean **4.3391%** of one core over the six live windows, worst 4.4219% | **FAIL** against the 3% budget. Every one of the six live windows is above it on its own (4.2540%, 4.4219%, 4.3125%, 4.3128%, 4.3172%, 4.4164%), so the verdict does not rest on the mean |
| 2026-10-14 | B2 | 0 wake locks, 0 alarms, 0 jobs | **PASS** |
| 2026-10-14 | B4 against the black baseline | no black windows recorded, and this pass declares the reason | **NOT MEASURED (DEVICE-LIMITED)**, declared to the report rather than inferred from the missing windows: setting a static wallpaper rewrites the home image and this production build has no root, so the black baseline could be neither backed up nor restored, and it would also destroy the stock baseline this comparison is measured against |
| 2026-10-14 | B3, B6 | no hidden or memory window in this pass | **NOT MEASURED.** Their measurements stand in the earlier reports and were not re-run here |

`python3 scripts/battery_protocol.py --state build/reports/battery-126-n6 report --black-unavailable
"setting a static wallpaper rewrites the home image and this production build has no root, so it could
be neither backed up nor restored"` prints these verdicts and exits 1, on B5 and on the two unmeasured
criteria. The device-limited black row is the one absence that does not gate, and it is a declaration:
without that flag the same state file reports the missing black baseline as an unmeasured criterion
and fails on it too.

## What this establishes, and what it does not

**It establishes** that on this device, this build and these window conditions, the paired 95% upper
bound on the live-versus-stock discharge difference is below the committed threshold at six pairs.
#126's criterion 3 can be read from this for the stock baseline.

**It does not establish** the comparison against the black static baseline, which stays device-limited
and unmeasured for the reason the earlier reports give. It does not explain the **direction** of the
difference: the live arm drew less than stock in every pair, and no mechanism is claimed for that. The
pass is one device, one build, one screen state, one site, and three hours of one evening.

**It corrects two figures the earlier reports carried.** The gauge quantum is 4.5 mAh, not 1 mAh, so
a single window's quantisation is 2.3%–6.2% rather than 0.3%. And this device's per-pair difference sd
came in at 18.254% of the baseline mean, against the 24.819% the 2026-10-13 pass measured — which is
what the pre-registration projected the pair count from, and the reason six pairs were enough.

**It does not settle whether B4's threshold and its statistic belong together.** The threshold was
committed from a single arm's 2σ; the criterion is judged on the paired 95% upper bound, which is the
narrower of the two. This run passed, but a pass at a favourable point estimate says nothing about that
mismatch: at the σ_d measured here (18.254%), six pairs leave a 19.16% margin, so the budget is
certified only if the live arm measures about 7% better than stock, and a device at parity would need
roughly twelve — the first count whose margin, 11.60%, fits inside 12.0123%. It is the
pre-registration's wider projected σ_d of 24.819% that gives the harder 14% and nineteen, which is why
six pairs were fixed before the run rather than chosen after it. The pair count is stated
here rather than acted on, because re-specifying B4 is a decision for the criterion's owner and not
something a passing run should quietly make on its behalf.
