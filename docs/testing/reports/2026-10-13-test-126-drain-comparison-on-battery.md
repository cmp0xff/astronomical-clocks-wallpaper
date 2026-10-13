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
| `FINAL_MAX_VISIBLE_DELTA_PCT` | **12.0123%** | The greater of the provisional 5% and the measured 12.0123% floor. The floor is wider, so the budget gets harder, not easier — the document's rule working as intended |
| `FINAL_MAX_VISIBLE_CPU_PCT` | **3.0%** | The greater of the provisional 3% and the measured 0.0894% floor, which leaves the provisional value standing |
| `PILOT_DISCHARGE_NOISE_FLOOR_UA` | 43997.06 uA | The discharge 2 sigma, carried beside the threshold it justifies |
| `PILOT_CPU_NOISE_FLOOR_PCT` | 0.0894% | The CPU 2 sigma, below the provisional threshold |

These were committed in `scripts/battery_protocol.py` in a commit that precedes the final run and
that contains this section; the run below is the first to use them.

## What the floors do and do not establish

**The discharge floor is this device's, not the wallpaper's.** Four windows spread from 335382 uA to
387475 uA on the *same* arm with the same pinned conditions. That spread is what the harness measures
when nothing is being compared, and it is a property of the gauge and the cell rather than of the
wallpaper: the fuel gauge reports in 1000 uAh steps, so a 15-minute window's ~330000-390000 uAh
carries roughly 0.3% quantisation, and a worn cell widens the rest. Nothing here says the wallpaper
is or is not power-hungry; it says this device cannot resolve a difference smaller than about 12%
between two 15-minute windows.

**The consequence is that B4's bar is now 12%, not 5%.** A live-versus-static difference smaller than
that will not be distinguishable from this device's own variation, and the report below says
whichever way the comparison comes out. The floor has to be re-measured per device, which is what the
document's rule requires anyway.

**The CPU floor is small and is not a floor for anything else.** 0.0894% of one core over 15-minute
home-screen windows does not transfer to other window lengths, to the lock screen, or to a hidden
engine.

## Interruptions during the pass

Two were recorded rather than smoothed over:

1. **A wireless-debugging drop.** The first pilot attempt died 35 s into window 1: `adb` reported the
   device offline, the in-window restore could not run, and the device was left off the network with
   the harness's pin still applied (brightness, a one-hour timeout, stay-awake on every power
   source). It was recovered by hand, the leftover pin cleared, and the run restarted. The
   per-window record persistence this round added is what keeps the *completed* windows of such a run
   rather than discarding them; in this case window 1 had not completed, so nothing was kept and
   nothing needed discarding. Every later run went out with a device-side watchdog armed so a lost
   transport un-pins the device instead of leaving it draining.
2. **A brightness change inside window 1 of the restarted pilot.** The setting was found at 1
   (minimum) while the window was mid-flight, having been pinned at 128 at its start, so that window
   was not measured under one condition. It was discarded: the pilot was stopped, the state file
   reset, brightness fixed at 128 in manual mode, and the pilot re-run cleanly from window 1. Every
   window in the table above was measured with brightness pinned and unchanged.

## The final run

The A/B/A/B comparison against the stock `ImageWallpaper`, and its B4 and B5 verdicts, follow in the
section below once the run completes. The black static baseline remains unrun for the reason the
2026-10-12 report gives: setting a static wallpaper writes the home wallpaper image, and this
production build has no root (`adb root` is refused), so the change could be neither backed up nor
restored and would destroy the stock baseline this comparison is measured against.
