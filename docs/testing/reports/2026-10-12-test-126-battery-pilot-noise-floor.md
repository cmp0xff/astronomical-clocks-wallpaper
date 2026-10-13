# Battery pilot and committed thresholds (#126)

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew qualityGate :app:assembleDebug`, APK SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`. The `app/src/main` tree it
compiles is byte-identical to the tree at `5efae15`, so that revision names the application sources
under test. The harness revision each record carries is `e9f5d0694795705485c2a663fc42128795d28827`;
the records written before it carry `76a1b8ceec51c402c6a4082f015264fc41a9dd35`, and the two differ
only in documentation — `git diff 76a1b8c e9f5d06 -- scripts/` is empty — so every measurement below
comes from one harness. The artifact was installed in place with `adb install -r`.

Physical device running Android 16 (API 36). Vendor, model, serial, and firmware build are withheld
per the device-privacy policy. The saved site carried through the pass is the manual published
city-centre reference `50.08, 14.42`, `Europe/Prague`.

## What was measured and why

[`docs/battery-budget.md`](../../battery-budget.md) makes B4 and B5 provisional and requires a pilot
to measure the device's own noise floor before the final run, with the final threshold being the
greater of the stated value and the observed two-standard-deviation floor, **committed before that
run** so the number cannot be tuned to the result. This report is that pilot: four repeated windows
of the documented length, and the thresholds it fixes.

The pilot was run on the live-wallpaper arm. The document's wording says "baseline windows", and a
baseline-arm pilot is what the discharge channel would need — but the target device's fuel gauge
turned out to be incapable of producing a discharge signal at all (below), and a baseline window has
no wallpaper process to read CPU from, so a baseline pilot on this device would have recorded two
zeroes and calibrated nothing. Running it on the live arm measures the channel that does work, and
the discharge side is recorded as the dead channel it is rather than as a floor of zero.

## Method

`scripts/battery_protocol.py pilot --arm A --runs 4 --minutes 15` runs four visible home-screen
windows of 15 minutes on the live wallpaper, verifies the arm before each one, and reports the mean,
the sample standard deviation and twice that deviation for both channels. The windows are flagged in
their records and are **excluded** from the final comparison, so committing a threshold from the
pilot cannot be tuned to the result. The document's anti-tuning rule is enforced in code rather than
described.

Each window pins brightness and auto-brightness, raises the screen-off timeout, enables airplane
mode, simulates discharge with `cmd battery unplug`, and restores all of it in a `finally`, reading
each value back. Every one of the four records carries an empty `restore_problems` list.

## Observed results

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-12 | pilot window 1 | 904.7 s; charge counter 4500000 → 4500000 uAh; **discharge 0 uAh**; CPU **3.2938%** of one core |
| 2026-10-12 | pilot window 2 | 904.5 s; charge counter 4500000 → 4500000 uAh; discharge 0 uAh; CPU 3.2900% |
| 2026-10-12 | pilot window 3 | 904.6 s; charge counter 4500000 → 4500000 uAh; discharge 0 uAh; CPU 3.2875% |
| 2026-10-12 | pilot window 4 | 904.5 s; charge counter 4500000 → 4500000 uAh; discharge 0 uAh; CPU 3.2691% |
| 2026-10-12 | arm verification | Every window's record names the live service component, so all four are live-arm windows and no baseline window was silently paired in |
| 2026-10-12 | discharge noise floor | mean 0 uA, sample standard deviation 0 uA, **2 sigma 0 uA**. This is a dead channel, not a quiet device: the hardware charge counter read exactly 4500000 uAh before and after every window while the device was on USB, so `cmd battery unplug` froze the reported power source without the gauge moving at all |
| 2026-10-12 | CPU noise floor | mean 3.2851% of one core, sample standard deviation 0.0110, **2 sigma 0.0219%** |
| 2026-10-12 | committed B4 threshold | The greater of the provisional 5% relative increase and the observed floor leaves the threshold at **5%**, because there is no discharge floor to raise it. It is committed in `scripts/battery_protocol.py` as `FINAL_MAX_VISIBLE_DELTA_PCT`, and the run that follows is the first to use it |
| 2026-10-12 | committed B5 threshold | The greater of the provisional 3% of one core and the observed 2 sigma floor of 0.0219% leaves the threshold at **3%**. Committed as `FINAL_MAX_VISIBLE_CPU_PCT` |
| 2026-10-12 | what B5 already implies | The pilot's own CPU readings sit at 3.29%, **above** the committed 3% threshold. The pilot is not the qualification, so this is not the B5 verdict; it is the reason to expect one, and the drain report states the measured result |

## Correction to the claims made before this pilot

The branch's first commit reported a pilot pass performed on this device. That run's own output does
not support what was claimed of it, and this report replaces those claims rather than repeating them:

| Claim in the opening commit | What the run and this pilot show |
| --- | --- |
| 0 wake locks, 0 alarms | Both counts were read once, on the home screen, before a five-second sample. A single-instant census is what the probe reads; it is not a duration, and Android vitals' own 2-hour-in-24 measurement is what bounds a duration |
| "CPU < 1.5 s" | That figure is a lifetime cumulative `batterystats` total and not a rate, and it was quoted in place of the per-sample figure the same run printed: 0.270 s and 0.320 s of CPU in 5 s, which is 5.40% and 6.40% of one core. Against the document's own budgets — B5 at 3% and B3 at 0.1% — those are failures |
| "delta=0 uAh", "voltage=0 mV" | The zero voltage came from the unanchored pattern matching `Max charging voltage: 0`. The zero delta is real but means the charge counter is frozen, not that the device drew nothing; this pilot reproduces it across four fifteen-minute windows, and the drain report records it as the dead channel that leaves B4 unmeasurable |
| "137 tests passing" | 137 was the whole `scripts` suite. The battery protocol's own file held 12 tests then and holds 54 now |

The opening commit's message was rewritten in this branch for one reason only — it named the device's
OEM model and serial, which the repository's privacy policy keeps out of Git — and the unsupported
claims in it are corrected here and in the pull request body rather than by rewriting it further.

## What this does not establish

The discharge noise floor is not a floor. Zero microampere-hours in all four windows is what a
frozen gauge produces, and the same zero would appear whether the device drew nothing or drew
everything; a threshold calibrated against it would be meaningless. No claim about the device's
discharge noise should be read from this report, and B4's threshold remains uncalibrated — which
means the drain comparison cannot be evaluated against it on this device.

The CPU floor is measured over 15-minute windows on the home screen with the screen pinned on. It
does not transfer to other window lengths, to the lock screen, or to a hidden engine, none of which
were measured here. The four windows are consecutive and not randomised, so a drift shared by all
four would appear as a low floor rather than as noise.

The pilot is one device and one build. Nothing here establishes what any other device's floor is,
and the document's rule — take the greater of the stated value and the floor — has to be re-run per
device rather than copied.
