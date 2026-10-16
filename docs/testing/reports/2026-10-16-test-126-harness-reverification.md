# Battery-protocol harness re-verification on device (#126, [#132](https://github.com/godaniya/astronomical-clocks-wallpaper/pull/132))

Harness revision under test: `c48698fce5d20d2aed51124379306c9cf93a70d4`
(`scripts/battery_protocol.py`, `scripts/battery_report.py` and `scripts/device_layer.py` at that
revision). Android 16 (API 36). The APK **as installed on the device**: SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`, read from the device with
`pm path` → `sha256sum` rather than from a host build artifact, so the digest names the build the
windows actually ran against. The pass installed nothing and built nothing; it verifies the harness
against whatever build is already on the device. Device vendor, model, serial and firmware build are
withheld per the [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy);
the harness's own salted `device_id` for this device is `2ec5a940ef5f`, carried by every record below.
Run date: 2026-10-16.

## What was measured and why

The review loop on #132 closed clean at `c48698f` with 84 threads resolved and 506 host tests green,
but eight review rounds had changed **device-facing** code, and the recorded B2 `resources` evidence
in the slice predates all of them. `docs/device-testing.md` and `docs/battery-budget.md` attribute
every device result to the harness revision that produced it, and the repository's own precedent is
that a scripts-only change still needs its own device pass — the
[2026-10-07 final #107 pass](2026-10-07-refactor-107-final-device-pass.md) records exactly that for a
change whose `app/` diff was empty — its test APK is built from a different revision, and it states
that `git diff 058567a..48751a8 -- app/` is empty, so no install was performed. The recorded
`resources` pass therefore no longer describes
this head, and this turn re-establishes it.

The device-facing changes this pass had to exercise, all landed after that recorded pass:

- **Tightened parsers**, each of which could now *refuse a real dump*: `parse_battery_output` and the
  three census parsers bound their digit runs (`\d{1,18}(?!\d)`); `parse_total_pss_kb` requires
  horizontal whitespace and a value boundary; `parse_proc_stat_cpu_seconds` and `read_process_uid`
  read unsigned; `read_int_setting` parses instead of testing digits.
- **New window validity rules**: a screen-off window reads the display back before it starts, and a
  visible window samples the surface in front *through* the window rather than trusting one reading
  taken before it.
- **A split power mode**: the mode a window runs in is decided and recorded before the mutating
  `cmd battery unplug`, so a failed override still owes a `cmd battery reset`.
- **Guarded restores**: `_restore_display`'s reads are guarded so a read that fails cannot discard the
  problems the settings steps already collected.
- `median_luma` refuses a garbled screencap as a `ProbeError`; `phase_memory` records the measured
  span; the `hidden` record no longer carries `wall_clock_seconds`.

**Scope: the short harness pass only.** No pilot and no drain comparison. B4, B5 and B6 are not
claimed by this pass, for the reasons given under [What this pass does and does not
establish](#what-this-pass-does-and-does-not-establish).

## Method

One bounded invocation per phase, each writing its own record to a fresh state directory and printing
its own evidence. The state file is working data and stays out of git; this report quotes the records
rather than attaching them.

```sh
STATE=build/reports/battery-pass-2026-10-16   # created empty for this pass
python3.11 scripts/battery_protocol.py --state "$STATE" --serial <SERIAL> resources
python3.11 scripts/battery_protocol.py --state "$STATE" --serial <SERIAL> hidden  --minutes 10
python3.11 scripts/battery_protocol.py --state "$STATE" --serial <SERIAL> visible --arm A --minutes 15
python3.11 scripts/battery_protocol.py --state "$STATE" --serial <SERIAL> report \
  --black-unavailable "setting a static wallpaper rewrites the home image and this build has no root"
```

No Gradle and no `ANDROID_HOME` are involved: the pass installs nothing and builds nothing.
`python3.11` is the interpreter verified in this environment; `python3` resolves to 3.12 on the
runner and both are at or above the 3.11 the harness needs.

**The harness must not be run with `-I`.** `-I` implies `-P`, so `scripts/` is not prepended to
`sys.path` and the run dies at `import device_layer` with `ModuleNotFoundError: No module named
'device_layer'`. It is run as `python3.11 scripts/battery_protocol.py …` from the worktree root. The
host *tests* keep `-I`, because `unittest discover -s scripts` inserts the start directory itself.
`--state` and `--serial` are top-level options and appear **before** the subcommand.

### The arm the pass ran on

The home wallpaper slot is bound to this app's own service, so the pass ran the live arm. Read
directly before any window: `dumpsys wallpaper` reports
`ComponentInfo{io.github.godaniya.astronomicalclockswallpaper.debug/io.github.godaniya.astronomicalclockswallpaper.AstronomicalClocksWallpaperService}`,
and both the `visible` arm guard and `hidden`'s live-arm check admitted it. The harness never swaps
the wallpaper it measures, so no swap was needed. The device has no keyguard
(`locksettings get-disabled` is `true`), so no lock surface could shadow the home screen.

## The parser check against the device's real dumps

Every parser this branch tightened could now *refuse* a real dump, and a refusal on the deferred drain
pass would cost the owner an hour. Each dump below was captured through `device_layer.run_adb` — the
same call a phase makes, so the bytes are identical to what a phase would parse — into a scratch
directory, and the captured text was then fed to the worktree's parsers from a throwaway script. **25
of 25 checks passed; no parser refused a real dump.** No dump carried a single CRLF line ending.

| Parser | Dump | Captured size | Observed |
| --- | --- | --- | --- |
| `parse_battery_output` | `dumpsys battery` | 10 975 B | charge counter 3555000 uAh, level 79%, voltage 4323 mV |
| `read_process_uid` | `stat -c %u /proc/<pid>` | — | `10026` |
| `require_clk_tck` | `getconf CLK_TCK` | — | `100` |
| `parse_proc_stat_cpu_seconds` | `cat /proc/<pid>/stat` | 201 B | 8.72 s (utime 749 + stime 123 ticks ÷ 100) |
| `parse_total_pss_kb` | `dumpsys meminfo <pkg>` | 3 218 B | 21 181 kB |
| `parse_wakelock_count` | `dumpsys power` | 126 866 B | census `Wake Locks: size=1`, block reconciles to 1 entry, **0** attributed to uid 10026 |
| `parse_alarm_count` | `dumpsys alarm` | 295 294 B | census `161 pending alarms:`, block reconciles to **161** entries, **0** for the app |
| `parse_job_count` | `dumpsys jobscheduler` | 2 764 652 B | census `Registered 572 jobs:`, block reconciles to **572** entries, **0** for the app |
| `read_int_setting` | `settings get` | — | brightness 128, brightness mode 0, timeout 300000, airplane mode 0, `stay_on_while_plugged_in` unset, `screen_off_pocket` 1 |
| `read_wallpaper_component` | `dumpsys wallpaper` | — | the live service, as above |
| `read_installed_apk_sha256` | `pm path` + `sha256sum` | — | the digest in the header |
| `read_screen_on`, `read_keyguard_locked`, `read_home_in_front` | `dumpsys power` / `dumpsys window` | 254 911 B | `False`, `False`, `None` (the display was off, so no surface was in front) |
| `parse_api_level` | `getprop ro.build.version.sdk` | 3 B | `36` |
| `median_luma` | `exec-out screencap` | — | `0.0` with the display off — the screencap decoded rather than being refused as garbled |
| `read_power_source_lines`, `power_mode_for` | `dumpsys battery` | — | all four `powered:` fields present; `power_mode_for` → `simulated` |
| `device_identity` | — | — | `2ec5a940ef5f` |

The census accounting is the part worth stressing, because that is where the branch's changes turned
a presence check into an accounting one. The device's alarm census read **161** this pass where the
recorded pass saw 164, and its job census **572** where the recorded pass saw 521 — the counts move
between moments, and the parser is what reconciles whatever the dump declares against the block
below it rather than holding a remembered figure. The wake-lock census was `size=1` and the single
entry belonged to another UID; the all-type predicate counted it, reconciled it, and attributed
**zero** to the app, which a `PARTIAL_WAKE_LOCK`-only scan could never have done.

## Observed results

### `resources` (B2) — seconds

| Date | Check | Observed | Budget | Verdict |
| --- | --- | --- | --- | --- |
| 2026-10-16 | wake locks, every type | 0 | 0 | PASS |
| 2026-10-16 | alarms | 0 | 0 | PASS |
| 2026-10-16 | jobs | 0 | 0 | PASS |
| 2026-10-16 | `batterystats` corroboration | `Total cpu time: u=44s 597ms s=34s 303ms, Fg Service for: 1m 53s 98ms, Total running: 13m 46s 774ms, Total cpu time: u=3s 330ms s=475ms` | — | recorded, not gated |
| 2026-10-16 | installed-digest read | `3ef8bf79…3dc4d`, read from the device | — | PASS |

### `hidden --minutes 10` (B3)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-16 | measured span | 605.8 s (a 10-minute window) |
| 2026-10-16 | hidden CPU | **0.0182%** of one core against the 0.1% budget — PASS |
| 2026-10-16 | `screen_on_samples` | 20/20 `False` — the display was off for the whole window, and the new pre-window readback confirmed it went dark before the first sample |
| 2026-10-16 | `on_battery_samples` | 20/20 `True` |
| 2026-10-16 | `power_source` | `simulated` — the device was on USB, so the override was applied and the reset owed |
| 2026-10-16 | `clk_tck` | 100 |
| 2026-10-16 | `restore_problems` | `[]` |
| 2026-10-16 | `wall_clock_seconds` | absent from the record, as the branch changed it |

### `visible --arm A --minutes 15` (one arm-A window, not a B4/B5 measurement)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-16 | arm guard | admitted the live service; `wallpaper_component` in the record names it in full |
| 2026-10-16 | `median_luma` | 23.81 for the live arm — the same reading the 2026-10-13 pass recorded for arm A |
| 2026-10-16 | measured span | 911.3 s (a 15-minute window) |
| 2026-10-16 | CPU | 3.4348% of one core |
| 2026-10-16 | `screen_on_samples` | 30/30 `True` |
| 2026-10-16 | `on_battery_samples` | 30/30 `True` |
| 2026-10-16 | **`home_in_front_samples`** | **30/30 `True`** — the new through-the-window sampling working on hardware, which is the one observable a host test with faked dumps cannot establish |
| 2026-10-16 | charge counter | 3600000 → 3600000 uAh; discharge 0 uAh, rate 0 uA |
| 2026-10-16 | `power_source`, `clk_tck` | `simulated`, 100 |
| 2026-10-16 | `restore_problems` | `[]` |

**The zero discharge is the known frozen-gauge artefact, not a harness defect.** The device was on
USB, so `power_mode_for` chose the simulated override, and `cmd battery unplug` freezes the charge
counter — the same limitation the [2026-10-12 drain
comparison](2026-10-12-test-126-drain-comparison.md) records and the reason B4 needs the device on
its own battery. Every guard the `visible` phase owns still ran and passed on this window: the arm
guard admitted the component, the display and the surface in front were sampled 30 times each and
were correct every time, the CPU channel read, and the restore reported nothing. That is what this
window was for. It is **not** a discharge measurement, and no B4 or B5 value is claimed from it.

## Restoration evidence

Every phase restored every setting it pins, and each restore verifies its own readback. The settings
were captured before the pass and read again after each phase; all three phases left them identical:

| Setting | Before the pass | After `resources` | After `hidden` | After `visible` |
| --- | --- | --- | --- | --- |
| `system/screen_brightness` | 128 | 128 | 128 | 128 |
| `system/screen_brightness_mode` | 0 | 0 | 0 | 0 |
| `system/screen_off_timeout` | 300000 | 300000 | 300000 | 300000 |
| `global/airplane_mode_on` | 0 | 0 | 0 | 0 |
| `global/stay_on_while_plugged_in` | unset | unset | unset | unset |
| display state | off (`mWakefulness=Dozing`) | off | off | off |
| `dumpsys battery` power sources | USB powered | USB powered | USB powered | USB powered |
| home wallpaper component | the live service | unchanged | unchanged | unchanged |

The stay-awake setting is the one the phase *deletes* rather than writes — it was unset before the
pass, the harness pins it to 7 for a window and removes it afterwards, and it read back as unset both
times. The two `simulated` records each owed a `cmd battery reset`; the power sources read back to
`USB powered: true` after both, which is the pre-phase state, so the override was cleared. No record
in the pass carried a non-empty `restore_problems`, and no `*_aborted` record was written.

## The `report` command's refusal

`report` was run against the state file as the plan requires, and it refused — which is the designed
behaviour here, not a failure. It read all three records, named their shared identity (one revision,
one API level, one `device_id`, one `apk_sha256`), and printed:

```
B4 pairing: NOT MEASURED - the visible windows are not an A/B alternation: recorded arms ['A']
B5 visible CPU (mean): FAIL — 3.4348% against 3.0%
```

It exits 1. Both rows are the harness behaving correctly on a state file that is not an acceptance
run:

- The **B4 pairing** rule refuses a sequence that is not an A/B alternation rather than pairing
  windows that were not taken under the same conditions. One arm-A window is exactly that case, and
  the message names what is missing. The `report` does not print a top-level `ERROR:` line for it —
  it realizes the refusal as a `NOT MEASURED` verdict row, which is the same rule with a different
  presentation.
- The **B5** row is the live-arm CPU channel, which the report evaluates from the live windows
  independently of the pairing, so it prints a value even here. **This pass does not claim B5 from
  it.** The pass ran one arm-A window to verify the harness, not to measure the criterion; and the
  reading is in any case consistent with every recorded pass — 3.4552% on 2026-10-12, 3.7479% on
  2026-10-13 and 4.3391% in the six-pair run — all above the 3% budget at their own revisions.

`B6 PSS growth` is `NOT MEASURED` because no `memory` phase was run, and `B1 hidden frames` is
`OUT_OF_SCOPE`: the frame counter is #125 criterion 1, which records its own producer-trace evidence.
The black baseline is `NOT MEASURED (DEVICE-LIMITED)`, declared to the report rather than inferred
from its absence: setting a static wallpaper rewrites the home image and this build has no root, so
that baseline can be neither backed up nor restored.

## What this pass does and does not establish

**It establishes** that the harness at `c48698f` runs against real hardware and real dumps: every
phase exited 0, no phase wrote an aborted record, the censused dumps reconcile under the accounting
the branch introduced, no tightened parser refuses a dump this device actually produces, the new
through-the-window sampling of the surface in front works on hardware (30/30 `True`), the new
pre-window display readback works (20/20 screen-off samples after a confirmed transition), the split
power mode recorded `simulated` and still cleared the override, and every restore returned the device
to the state it was found in with nothing reported.

**It does not establish B4, B5 or B6.** A single arm-A window is not an A/B alternation, so the
paired comparison the B4 criterion is gated on was not run, and the pass claims no B4 verdict. The
B5 number above is one window's CPU, offered as an observation that agrees with the recorded passes,
not as this pass's B5 verdict. No `memory` phase was run, so B6 has no value at all. And none of the
three would be meaningful from this window in any case: the charge counter was frozen at 3600000 uAh
throughout, because the device was on USB under the simulated override.

**The recorded drain evidence still stands at its own older harness revision.** The 2026-10-13 and
six-pair reports were produced by an earlier `battery_protocol.py`; nothing here re-runs them or
supersedes their numbers. What this pass does add is that the revision which *would* produce the next
such run is known to work on this device — which matters, because a visible window is now stricter
than the windows those reports recorded, and a screen-off window now confirms its own transition. A
future run can be **refused** where an earlier one was accepted; that is the intended tightening, and
it is the reason it was worth verifying before asking the owner to spend an hour at the device.

**One obvious consequence is worth stating plainly.** A harness that now refuses more can refuse the
deferred drain pass on its first attempt, and each refusal costs a window. The pass was run to find
that out cheaply.

One device, one build, one screen state, one site. Nothing here transfers to another device, another
window length, the lock screen, or a hidden engine. The device was left unlocked, on the home screen,
with the display in the off state it was found in — the snapshot restores the display state, not the
lock state or the previously foregrounded app — and with the wallpaper binding it started with.
