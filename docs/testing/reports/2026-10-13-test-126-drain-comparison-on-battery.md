# Battery budget B4 and B5 on battery: the pilot, the committed thresholds, and the A/B/A/B run (#126)

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew qualityGate :app:assembleDebug`, APK SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`. The `app/src/main` tree it
compiles is byte-identical to the tree at `5efae15`, so that revision names the application sources
under test. Every harness record in this pass carries its own revision,
`b9b2b492a1a728c0c911f74497bd6939dcb01a55`, because the harness is what changed this round. The
artifact was installed in place with `adb install -r`.

Physical device running Android 16 (API 36). Vendor, model, serial, and firmware build are withheld
per the device-privacy policy. The saved site carried through the pass is the manual published
city-centre reference `50.08, 14.42`, `Europe/Prague`.

## What was measured and why

#126 criterion 3 asks for "a recorded A/B/A/B run against stock and against a black static
wallpaper, with the result stated against B4 and B5, including a failed result if it fails". The
[2026-10-12 pass](2026-10-12-test-126-drain-comparison.md) could not measure B4 at all: the device
was on USB, `cmd battery unplug` froze the fuel gauge, and every window recorded a charge-counter
delta of exactly 0 uAh. This pass runs the same comparison with the device **on its own battery**,
where the gauge moves, which is what makes B4 measurable for the first time.

## Method

### Power source

The harness now detects whether the device is already on its own battery and, when it is, leaves the
live fuel gauge alone. `cmd battery unplug` "force[s] battery unplugged, **freezing battery state**"
(the device's own `adb shell cmd battery -h`), so applying it to a discharging device would re-create
the frozen gauge the simulation exists to work around. Every window in this pass recorded
`power_source: device`, meaning no override was applied; the `cmd battery reset` restore step is
skipped when no override was applied, and the power source is re-sampled inside each window so a
window that saw a charger appear is refused rather than recorded.

Because the device must be discharging to be measured, it cannot be on USB, so these runs are driven
over wireless debugging. That puts the transport on the same Wi-Fi the harness's airplane-mode pin
touches, so the interaction was checked directly rather than assumed: with airplane mode held on for
220 s, `adb` and `ping` to the device stayed up and `wifi_on` never moved, and the harness's
per-window restore returned it. The pin therefore stays in the method; the one transport failure in
this pass (recorded below) was wireless-debugging flakiness that returned on the same port.

### Environmental controls

Each window pins brightness at 128 with auto-brightness off, raises the screen-off timeout to an
hour, holds the display awake on every power source, enables airplane mode, and restores all of it in
a `finally` with every value read back. The pre-window brightness was set to 128 and to manual mode
before the pass began, so every window starts from the same state. The harness pins brightness for
the duration of a window; a value that changed *inside* a window would invalidate it, which is why
the pass was restarted once when that happened (recorded below).

### The pilot

`docs/battery-budget.md` makes B4 and B5 provisional and requires a pilot to measure the device's own
noise floor before the final run, with the final threshold being the greater of the stated value and
the observed two-standard-deviation floor, **committed before that run**. The pilot runs four
15-minute windows on the live arm — a baseline (static) window has no wallpaper process to read CPU
from, so a baseline pilot would calibrate only the discharge channel and record the CPU channel as a
zero, which is not what this device's B5 needs. The windows are flagged in their records and are
excluded from the final comparison.

## Observed results: the pilot

| Date | Window | Observed |
| --- | --- | --- |
| 2026-10-13 | pilot window 1 | 918.7 s; charge counter delta 94500 uAh; discharge rate 370287 uA; CPU 3.6517% of one core |
| 2026-10-13 | pilot window 2 | 919.8 s; delta 99000 uAh; rate 387475 uA; CPU 3.6040% |
| 2026-10-13 | pilot window 3 | 917.8 s; delta 85500 uAh; rate 335382 uA; CPU 3.5739% |
| 2026-10-13 | pilot window 4 | 914.7 s; delta 94500 uAh; rate 371918 uA; CPU 3.5475% |
| 2026-10-13 | discharge noise floor | mean **366265 uA**, sample standard deviation 21999 uA, **2 sigma 43997 uA = 12.0123% of the mean** |
| 2026-10-13 | CPU noise floor | mean 3.5943% of one core, sample standard deviation 0.0447, **2 sigma 0.0894%** |
| 2026-10-13 | arm verification | Every window's record names the live service component, so all four are live-arm windows |
| 2026-10-13 | power source | Every window recorded `power_source: device` and `on_battery` true at all 30 in-window samples |
| 2026-10-13 | device state restored | All four records carry `restore_problems: []`; brightness, brightness mode, the timeout, airplane mode and the stay-awake setting were read back at their pre-run values |

### Committed thresholds

| Threshold | Committed value | Why |
| --- | --- | --- |
| `FINAL_MAX_VISIBLE_DELTA_PCT` | **12.0123%** | The greater of the provisional 5% and the measured 12.0123% floor. Raising it makes the criterion **more permissive**, not harder: a live-versus-baseline increase up to 12.0123% now passes where 5% would have failed it. What the wider floor buys is honesty rather than leniency — below it this device cannot separate a real difference from its own variation |
| `FINAL_MAX_VISIBLE_CPU_PCT` | **3.0%** | The greater of the provisional 3% and the measured 0.0894% floor, which leaves the provisional value standing |
| `PILOT_DISCHARGE_NOISE_FLOOR_UA` | 43997.06 uA | The discharge 2 sigma, carried beside the threshold it justifies |
| `PILOT_CPU_NOISE_FLOOR_PCT` | 0.0894% | The CPU 2 sigma, below the provisional threshold |

These were committed in `scripts/battery_report.py` in a commit that precedes the final run and
that contains this section; the run below is the first to use them.

## What the floors do and do not establish

**The discharge floor is this device's, not the wallpaper's.** Four windows spread from 335382 uA to
387475 uA on the *same* arm with the same pinned conditions. That spread is what the harness measures
when nothing is being compared, and it is a property of the gauge and the cell rather than of the
wallpaper: every charge-counter delta this pass recorded is a multiple of **4500 uAh**, so the gauge
reports in 4.5 mAh steps and a 15-minute window's 76500-153000 uAh carries **2.9%-5.9%**
quantisation, and a worn cell widens the rest. Nothing here says the wallpaper
is or is not power-hungry; it says this device cannot resolve a difference smaller than about 12%
between two 15-minute windows.

**Correction (2026-10-14).** This paragraph read the gauge's step as 1000 uAh and the effect as
roughly 0.3%, against a "~330000-390000 uAh" window. Those figures are the discharge *rates* in uA
that the sentence before it quotes, not the windows' charge-counter deltas in uAh, and the deltas in
this pass's own tables are all multiples of 4500 uAh — 94500, 99000 and 85500 in the pilot, and 76500
to 153000 in the run below. The step is therefore 4.5 mAh rather than 1000 uAh, which is what the
[six-pair pass](2026-10-13-test-126-drain-comparison-six-pairs.md) states from its own counters. No
measurement in this report changed; the paragraph's arithmetic and its unit did.

**The consequence is that B4's bar is now 12%, not 5%.** A live-versus-static difference smaller than
that will not be distinguishable from this device's own variation, and the report below says
whichever way the comparison comes out. The floor has to be re-measured per device, which is what the
document's rule requires anyway.

**The CPU floor is small and is not a floor for anything else.** 0.0894% of one core over 15-minute
home-screen windows does not transfer to other window lengths, to the lock screen, or to a hidden
engine.

## The final run: A/B/A/B against the stock baseline

### Method

Four alternations of a 15-minute visible home-screen window, arm A (the live wallpaper) then arm B
(the OEM stock `ImageWallpaper`), which is the minimum #126 mandates. The swap is performed outside
the harness — `am force-stop` for arm B and the app's own wallpaper preview for arm A — and confirmed
by the harness before each window: it reads the bound component and refuses a window whose device
does not match the arm it is about to be recorded against. A refused window writes no record, so a
mis-swapped wallpaper cannot enter the table. Each window pins the same conditions as the pilot,
records `power_source: device`, and samples the display state and the power source 30 times across
its duration.

### Observed results

| Date | Window | Duration | Charge-counter delta | Discharge rate | CPU | In-window samples |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-13 | pair 1, arm A (live) | 915.8 s | 99000 uAh | 389170 uA | 3.7006% of one core | 30/30 display on, on battery |
| 2026-10-13 | pair 1, arm B (stock) | 915.7 s | 153000 uAh | 601478 uA | no process to read, as expected for a static wallpaper | 30/30 |
| 2026-10-13 | pair 2, arm A | 915.3 s | 90000 uAh | 353980 uA | 3.7780% | 30/30 |
| 2026-10-13 | pair 2, arm B | 917.2 s | 76500 uAh | 300256 uA | — | 30/30 |
| 2026-10-13 | pair 3, arm A | 916.0 s | 90000 uAh | 353727 uA | 3.7371% | 30/30 |
| 2026-10-13 | pair 3, arm B | 919.8 s | 126000 uAh | 493133 uA | — | 30/30 |
| 2026-10-13 | pair 4, arm A | 918.5 s | 94500 uAh | 370397 uA | 3.7758% | 30/30 |
| 2026-10-13 | pair 4, arm B | 921.6 s | 144000 uAh | 562478 uA | — | 30/30 |
| 2026-10-13 | arm verification | Each of the eight records names the component it was measured against — the live service for arm A, the static `ImageWallpaper` for arm B — so no window was recorded against the wrong arm |
| 2026-10-13 | power source | Every window recorded `power_source: device` and `on_battery` true at all 30 in-window samples, so no window was measured across a charger appearing |
| 2026-10-13 | device state restored | All eight records carry `restore_problems: []` |

### Verdicts

| Date | Criterion | Observed | Verdict |
| --- | --- | --- | --- |
| 2026-10-13 | **B4, relative increase against stock** | **−25.038%** point estimate (**95% CI upper bound +14.449%**, 4 pairs, df 3, t 3.182) | **FAIL.** The point estimate is inside the 12.0123% budget — it says the live wallpaper drew *less* than the stock baseline — but the criterion requires the paired 95% upper bound to stay below the threshold as well, and **+14.449% is above 12.0123%**. The interval is too wide to certify the budget. It would also have failed the 5% provisional threshold, so the pilot's floor did not change this verdict |
| 2026-10-13 | **B5, visible CPU** | mean **3.7479% of one core** over the four live windows; worst window 3.7780% | **FAIL** against the committed 3% budget. Every one of the four live windows is above it on its own (3.7006%, 3.7780%, 3.7371%, 3.7758%), so the verdict does not rest on the mean |
| 2026-10-13 | B2 wake locks | 0 | PASS. Partial locks only: the probe at this revision enumerated `PARTIAL_WAKE_LOCK` and nothing else, while the budget is now 0 wake locks of any type, reconciled against the dump's own census. Both landed later in the same pull request |
| 2026-10-13 | B2 alarms | 0 | PASS |
| 2026-10-13 | B2 jobs | 0 | PASS |
| 2026-10-13 | B3 hidden CPU | 0.0404% of one core over 10 minutes | PASS against the 0.1% budget |
| 2026-10-13 | B6 PSS growth | +3578 kB over 30 minutes | PASS against the 6144 kB budget |
| 2026-10-13 | B4 against the black baseline | no black windows recorded, and this pass declares the reason | **NOT MEASURED (DEVICE-LIMITED)**, declared to the report rather than inferred from the missing windows: setting a static wallpaper rewrites the home image and this production build has no root, so the black baseline could be neither backed up nor restored |
| 2026-10-13 | B1 hidden frames | not measured by this protocol | OUT OF SCOPE — #125 criterion 1, whose producer-trace evidence landed separately in [the 2026-10-12 pass](2026-10-12-test-125-hidden-frames-and-reboot.md) |

`python3 scripts/battery_protocol.py --state build/reports/battery report --black-unavailable
"setting a static wallpaper rewrites the home image and this production build has no root, so it could
be neither backed up nor restored"` prints the table's verdicts, with the one difference below, and
**exits 1**: the run is a failed acceptance, and the command that reports it now fails with it. The
device-limited black row is the one absence that does not gate, and the flag is what declares it;
without the flag the same state file reports the missing black baseline as an unmeasured criterion
and fails on it too.

Replaying the same state file with the current reporter adds `B2 wake locks: NOT MEASURED`: these
records predate the coverage stamp added when the probe widened from `PARTIAL_WAKE_LOCK` to every
lock type, so the B2 wake-lock row above is the historical partial-lock result under the revision
that measured it, not the current all-type verdict. Its count and every other measurement are
unchanged.

## What the comparison does and does not establish

**B4 fails, and the reason is the interval rather than the direction.** Three of the four pairs show
the live arm drawing less than the stock arm; pair 2 is the outlier, where the stock arm drew
300256 uA against the live arm's 353980 uA. The paired estimate is −25.038%, but the spread across
four pairs leaves a 95% upper bound of +14.449%, above the 12.0123% budget. The honest reading is
that this device, over these eight windows, **could not resolve whether the live wallpaper stays
within the budget** — not that it is frugal, and not that it is power-hungry.

**The comparison's direction is not explained here.** The stock baseline drew *more* than the live
wallpaper on the mean, which is the opposite of what a naive reading would predict, and this report
does not establish why. What is recorded is that both arms were measured under the same pinned
brightness with the harness confirming the bound component each time, that the live arm shows a
consistently lower charge-counter delta, and that the stock arm's own windows vary from 300256 uA to
601478 uA — as wide a spread within one arm as between the arms. A mechanism is not claimed.

**The stock baseline is not the black baseline.** Arm B here is the device's OEM
`ImageWallpaper` — its starfield, as displayed. The screen's *median* luminance reads 0.0 for arm B
and 23.81 for arm A because that starfield is mostly black with lit dots, so median luminance is a
poor summary of it; the 2026-10-12 report records the same property. The secondary black static
baseline remains unrun, for the reason that report gives: setting a static wallpaper writes the home
wallpaper image, and this production build has no root (`adb root` is refused), so the change could
be neither backed up nor restored and would destroy the stock baseline this comparison is measured
against.

**B5's failure is unchanged by this pass.** 2026-10-12 measured 3.4552% of one core on USB with the
discharge simulated; this pass measures 3.7479% on the device's own battery. Both are above the 3%
budget, and every individual window in both passes is above it.

## Interruptions during the pass

Three were recorded rather than smoothed over:

1. **A wireless-debugging drop.** The first pilot attempt died 35 s into window 1: `adb` reported the
   device offline, the in-window restore could not run, and the device was left off the network with
   the harness's pin still applied (brightness, a one-hour timeout, stay-awake on every power
   source). It was recovered by hand, the leftover pin cleared, and the run restarted. Every later
   run went out with a device-side watchdog armed so a lost transport un-pins the device instead of
   leaving it draining. The per-window record persistence added this round is what keeps the
   *completed* windows of such a run rather than discarding them.
2. **A brightness change inside window 1 of the restarted pilot.** The setting was found at 1
   (minimum) while the window was mid-flight, having been pinned at 128 at its start, so that window
   was not measured under one condition. It was discarded, the state file reset, brightness fixed at
   128 in manual mode, and the pilot re-run cleanly from window 1.
3. **A harness invocation cut short by its own time limit.** The A/B orchestrator was killed by the
   tool's two-hour background limit part-way through the fourth arm-B window, so that window wrote no
   record and the device was left pinned by the killed process's unfinished restore; the pin was
   cleared by hand and the final window re-run, which is why pair 4's arm B carries a start
   twenty-six minutes after pair 4's arm A. The eight windows above are the completion of one
   A/B/A/B alternation, which is what `report` requires and checks.

## What the pass does not establish

The pass is one device, one build, one screen state and one site. The discharge floor is this
device's, and with it the 12.0123% B4 threshold; another device requires its own pilot, which the
budget requires anyway. The 15-minute windows are the issue's minimum, so the interval is as wide as
the mandated design permits; longer windows would narrow it, and this report does not claim they
would move the verdict. Nothing here transfers to another device, another window length, the lock
screen, or a hidden engine.
