# Lifecycle, accuracy, and battery qualification (#6)

Historical evidence migrated from [archived #103](https://github.com/cmp0xff/astronomical-clocks-wallpaper/blob/2932354f67d578f337d50cc6d83e5e355db9545a/docs/device-testing.md#lifecycle-accuracy-and-battery-qualification-6).
No hardware run was performed during this split. The rows retain their original source and APK attribution.

Test build: local debug `app-debug.apk` from this branch at
`39360abf7410deb177e6eecaf87fca73479673ce` (APK SHA-256
`a6379b408df1a834d2b1135a9aeaafeaa9bc280a64ef8b8b654044526d3097c4`), built from a clean tree at that
revision and installed in place with `adb install -r` over the existing wallpaper binding.

That SHA-256 is the same value the [#36 report](2026-10-06-docs-36-force-stop-lifecycle.md) cites for `ab883937`, and the coincidence is mechanical
rather than a transcription error: `git diff ab883937 3de59d47 -- app/src/main` and
`git diff ab883937 39360abf -- app/src/main` are both empty, so those three revisions carry a
byte-identical `app/src/main` tree. The commits between them change only host tooling, documentation,
and `app/src/test` sources, and the debug APK is compiled from `app/src/main` plus the build scripts,
so all three produce the same artifact. Rebuilding each of them from a clean tree reproduced
`a6379b40…` three times. The hash therefore identifies the application sources under test, and the
revision identifies the harness that drove them.

The device is a physical unit running Android 16 (API 36), locale `de-DE`, timezone `Europe/Prague`,
with the firmware build withheld per the device-privacy policy. The saved site was manual Prague
`50.08, 14.42`, `Europe/Prague`, with the default Zodiac, Sun, and Moon layers, and the home screen
already carried the live wallpaper binding, so the baseline phase did not need the preview flow to
re-apply it. Before mutating anything the harness read the display size, screen state, night mode, and
wallpaper PID.

### Revised harness scope

Run `python3 scripts/device_qualification.py --max-pss-growth-kb <agreed-limit>` on one attached
physical device. The required PSS limit must be agreed before qualification; the short PSS sample is
not a battery or CPU measurement. The run begins with a Phase 0 environment setup that wakes the
device, dismisses the keyguard, shows home, and resets the debug clock; it is not a check and
contributes no report row. After the baseline the script runs seven check phases:

- **Baseline dial render**: Detects the visible dial and civil 24-hour hand,
   adapting to both Light (`#4E341B`) and Dark (`#F4E5B8`) appearance palettes.
- **Phase 1 — Screen-off / wake navigation**: Requests screen-off for four seconds, confirms the device reports
   off, then requests wake and detects the hand within one second. This does not count successful ticks
   or frames while asleep and cannot establish the zero-rendering requirement.
- **Phase 2 — Preview navigation**: Opens the live-wallpaper preview, returns home, and detects the active
   wallpaper hand. It does not inspect preview-engine destruction, listener cleanup, or duplicate timers.
- **Phase 3 — Surface recreation**: Reads back the applied override and requires readable physical size
   and the original override after restoring, before detecting the hand. A failed phase restore remains
   a failure even if final cleanup succeeds. It does not inspect lifecycle callbacks or resource leaks.
- **Phase 4 — Process rebind**: Kills the app process and looks for a different PID and visible hand during ten
   polls at 0.5-second intervals. The poll window is not a rebound-latency measurement, and saved
   preference values are not read back.
- **Phase 5 — Virtual time travel**: Confirms the debug service logged the requested offsets and checks
   hand-angle changes for +30 minutes and +12 hours only. Date rollover
   is not exercised.
- **Phase 6 — Total PSS sample**: Requires its own confirmed debug-clock reset, then compares total PSS
   before and after ten seconds against the supplied limit. An unconfirmed reset skips sampling and
   records a failure without a passing row. Missing samples and growth beyond the limit fail the run. This is neither a frame counter nor
   battery/CPU evidence.
- **Phase 7 — Renderer log scan**: Reports warnings/errors emitted by the renderer/service tags; an empty
   scan is inconclusive and does not prove successful rendering or that no frames were drawn. A scan
   that cannot be collected at all is a failed check and exits non-zero, which is a different state
   from a completed scan that matched nothing.

Before making device changes the harness requires readable display size, screen state, night mode,
and an explicit PSS limit. It checks the restored display size, night mode, and screen state after the
`finally` cleanup; any failed restore command, a display size that cannot be re-read, or a mismatch
fails the run. The virtual-clock reset is requested but its resulting app state is not independently
inspected.

### Palette classification

The palette verdict chooses the hand ink, so the harness classifies it from the civil scale's own rim
tone rather than from a single point. It samples twelve bearings every 30 degrees from 15 degrees at
`0.40 * min(w, h)` from the dial centre, takes the modal colour, and selects whichever of the two
pinned literals — `DialStyle.RIM` `#1C2C39` or `DialStyle.LIGHT_RIM` `#E8E2D2` — is nearer.

On the qualification frame at 1080x2408 all twelve reads returned `#1C2C39`, the dark palette's rim.
A radial scan upward from the dial centre measured gold at normalized radius 1.180 and again at 1.236 —
the ink of the numeral at the top of the dial, which is `XII`, since `MIDNIGHT_ANGLE_DEG` puts the
twelfth numeral at the top — with rim between the two strokes, then flat rim out to the muted-gold
inset stroke at `OUTER_RADIUS - RIM_INSET = 1.344` (measured 1.345), and the gold rim at
`OUTER_RADIUS = 1.370` (measured 1.369). The twelve probes sit at normalized 1.274, inside the flat
band between 1.239 and 1.344.

The harness's earlier single top-centre probe sat at normalized 1.211, inside the numeral's ink band,
where the sampled pixel depends on how the glyph lies at that exact column. On this frame the sample
fell in the gap between the glyph's two strokes and read rim by luck rather than by construction; the
same radius a few pixels away reads the `#D8B66A` gold.

### Observed qualification results

Run on 2026-10-06 with `--max-pss-growth-kb 4096`. The process exited 0 with
`Configured checks passed; renderer log scan was inconclusive.`

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-06 | visible dial baseline | Dial rendered, dark palette detected from the civil-scale rim; hand at 111.270° |
| 2026-10-06 | screen-off / wake navigation | Device reported screen off for 4s; hand visible within 1s of wake at 111.278° |
| 2026-10-06 | preview navigation | Returned to home with the active wallpaper hand at 111.324° |
| 2026-10-06 | surface recreation | Override to 1080x2000 and reset cleanly redrew the dial; hand at 111.324° |
| 2026-10-06 | process rebind | PID 15552 -> 16708 within ten polls at 0.5s; hand at 111.330° |
| 2026-10-06 | time travel (+30m, +12h) | +30m moved the hand 7.658° (residual +0.158°); +12h moved it 180.381° (residual +0.381°); both within the ±0.5° tolerance |
| 2026-10-06 | total PSS sample | 29361 -> 32280 kB over 10s, growth +2919 kB against the 4096 kB budget |
| 2026-10-06 | renderer log scan | Inconclusive: the tag filter matched no record, so the scan neither passed nor reported a warning |

A second invocation three minutes later repeated every check, with the measured values differing: the
same dark palette, a baseline of 112.040°, wake at 112.065°, preview at 112.091°, surface at 112.092°,
a rebind from PID 16708 to 18090, +30m at 7.627° (residual +0.127°), +12h at 180.350°
(residual +0.350°), and a PSS sample of 29137 to 32103 kB, growth +2966 kB. Both time-travel residuals
carry the same sign in both runs, which is a
small systematic offset rather than noise; at 0.381° the +12h residual uses three quarters of the
tolerance, so the tolerance is the binding constraint on that check.

**The PSS sample is a settling step, not per-tick growth, but the harness cannot prove that.** Both runs
measured roughly 2.9 MB of growth across their ten-second window, which does not support a first-run
warm-up explanation, since the second run was already warm and grew by the same amount. A separate
45-second steady-state sample taken afterwards, with the wallpaper visible and ticking, showed total PSS
flat at 32.3 MB (32339, 32187, 32337, 32329 kB) and Java Heap flat near 8.8 MB, so no sustained growth was
observable once the process had settled. What the harness's ten-second window cannot distinguish is a
one-off settling allocation from a slow leak; separating them is part of the battery and resource
protocol that #6 still owes, and the 4096 kB budget records the observation without asserting that
2.9 MB is acceptable.

### Historical rows from the original harness

The table below records the values the original harness reported at source revision
`3de59d47c1b1871d0a999a00e8bee8f3294c9957`. The measured values are as published; only the check
labels were aligned to the phase names the revised harness uses, and three rows the original harness
could not support — a `~200 ms` rebind latency, a midnight rollover, and "0 renderer warnings" — were
already withdrawn before this section was written. None of these rows was produced by the revised
harness, and none is evidence for the revised checks.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-06 | visible dial baseline | Dial rendered (Light palette), hand detected at 313.454° |
| 2026-10-06 | screen-off / wake navigation | Hand detected after wake at 313.752°; sleep state and rendering while asleep were not measured |
| 2026-10-06 | preview navigation | Hand detected after returning home at 313.767°; preview-engine cleanup was not inspected |
| 2026-10-06 | surface recreation | Override to 1080x2000 and reset cleanly redrew dial; hand at 313.789° |
| 2026-10-06 | process rebind | PID 24477 -> 25075; hand detected at 313.808°; saved preferences and exact latency were not measured |
| 2026-10-06 | time travel (+30m, +12h) | +30m moved hand 7.479° (residual -0.021°); +12h moved 180.006° (residual +0.006°), both within ±0.5° tolerance |
| 2026-10-06 | total PSS sample | PSS changed from 29.8 MB to 33.3 MB over 10 seconds; no acceptance budget was applied, so this is not a pass |
| 2026-10-06 | renderer log scan | No warnings were reported; the original harness did not treat an empty scan as inconclusive |

### Device state restored

The harness restored the virtual clock, display size, night mode, and screen state in its `finally`
block; it re-read the display size, night mode, and screen state to confirm each, and confirmed the
virtual clock only from its reset log line rather than from the device's app state, and its own run
reported no restoration failure. Nothing else was changed for the run: the device was woken by hand
before it, and `settings put system screen_off_pocket 0` and
`svc power stayon usb` were applied only to keep the accidental-touch overlay away from the injected
input and were put back afterwards (`screen_off_pocket` 1, `svc power stayon false`, verified). The
device was left with the wallpaper still bound to the home screen under PID 18090, display size
physical 1080x2408 with no override, night mode `auto`, and the screen dozing, which is the state it
was found in.

### Smoke-test harness run

`scripts/device_smoke_test.py` was run on the same device, in the same session, after the changes that
tightened it:

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-06 | virtual time travel (+30m) | Hand advanced 7.497° against 7.500° expected, residual -0.003°; broadcast took 113 ms |
| 2026-10-06 | surface recreation | Effective 1080x2408, `wm size 1080x2000` read back as active as requested, then `wm size reset`; hand drawn afterwards |
| 2026-10-06 | renderer log | Inconclusive: no matching warning records; rendering was not verified |

The run exited 0 and its closing line said the log scan was inconclusive. The service log was read
back afterwards and carried both `Debug clock offset set to 1800000ms` and
`Debug clock reset to system UTC`, which is the confirmation the reset path now requires.

### Not run

The date-rollover check is not exercised by either harness. Also not run, and still owed to #6:
evidence for zero rendering while hidden or asleep (Phase 1 observes the reported screen state and
post-wake rendering only), saved-state restoration after process recreation (Phase 4 reads a new PID
and a visible hand, not the persisted preferences), and the documented physical battery and CPU
protocol. Issue #6 remains incomplete until that matrix is run and recorded.

### Keyguard precondition and prerequisite-gated phases (2026-10-07, no device pass)

Copilot's review of PR #103 noted that both harnesses dismissed the keyguard unconditionally with
`wm dismiss-keyguard` before mutating the device, and that a few of their broadcasts and phases
proceeded even when a prerequisite step's confirmation had already failed. Neither harness was run on
a physical device for this change — no device was available this round — so every change below was
validated by the host test suite (`python3 -I -m unittest discover -s scripts -p 'test_*.py'`) plus
Ruff and `ty`, and is called out here as unverified on real hardware rather than folded silently into
the existing device-pass rows above.

- **Unlocked precondition.** `read_baseline`/`read_device_baseline` now also require a confirmed
   `isKeyguardShowing=false` before any mutation: an unreadable `dumpsys window` dump or a device
   reporting locked both refuse the run (`sys.exit(1)`) rather than letting `wm dismiss-keyguard`
   silently bypass a lock the harness never confirmed was already open. `wm dismiss-keyguard` is kept
   in the Phase 0 / `step_reset_clock_and_show_home` setup as a defensive no-op — it can only act on a
   transient, non-secure keyguard, and this precondition means it is expected to have nothing left to
   dismiss.
- **Keyguard re-check on restore.** `verify_restored_state` now also re-reads the keyguard state and
   reports the restore as unverified if the device is not confirmed unlocked afterwards, alongside the
   existing display-size, night-mode, and screen-state checks.
- **Prerequisite gating.** `device_qualification.py`'s `phase_environment_setup` and
   `device_smoke_test.py`'s `step_reset_clock_and_show_home` now return whether the service log
   confirmed the initial debug-clock reset. `main()` in both scripts skips every phase/step that
   depends on a known clock state (qualification Phases 1–6; the smoke test's time-travel and
   surface-recreation steps) when that reset was not confirmed, instead of measuring against an
   unknown clock and reporting a misleading pass. The `finally` restore and the renderer log scan still
   run unconditionally.
- **+30m offset confirmation.** `device_smoke_test.py`'s time-travel step now confirms the +30 minute
   broadcast against the service log before measuring the hand advance, mirroring the confirmation the
   qualification harness already applied to its own offset broadcasts; an unconfirmed offset is
   reported as unmeasurable rather than as a zero-delta advance.
- **Source for the keyguard pattern.** The `isKeyguardShowing=<bool>` line matched by
   `KEYGUARD_SHOWING_PATTERN` is printed by AOSP's `DisplayPolicy.dump()` as part of `dumpsys window`,
   confirmed from `frameworks/base`'s `DisplayPolicy.java` and present since at least API 23. It has not
   been confirmed against this project's own target device.

**Still owed, next device pass:** confirm `dumpsys window` actually reports `isKeyguardShowing=` in the
expected form on the target device and locale; confirm a locked device is correctly refused by both
harnesses without any mutation; confirm an unlocked run still completes end-to-end with the new
keyguard re-check in `restore_device`/`verify_restored_state`; re-run the full smoke and qualification
suites and record a new dated report without changing the historical rows above.

### Phase restore and parser hardening (2026-10-07, host-only verification)

Both harnesses reject malformed, nonpositive, or conflicting display-size fields as unreadable;
an unreadable override cannot be treated as absent and reset away. Keyguard parsing reads complete
assignment values across the dump: any valid `true` means locked, only all-valid `false` readings
establish unlocked, and missing or malformed values otherwise remain unknown. The pre-mutation
refusal and post-cleanup keyguard re-check continue to use these parsers.

Surface recreation now verifies its own restore before capturing the restored frame; a later
successful `finally` cleanup cannot erase a failed phase restore. Qualification's PSS phase also
stops before memory sampling when its own reset is unconfirmed. These changes retain unconditional
cleanup and do not alter the CLI, dependencies, or the historical device observations above.

Verification on 2026-10-07: isolated unittest discovery passed 99 tests; Ruff 0.16.10 lint and
format checks, ty 0.0.84, and `git diff --check` passed. Each of the five new regression tests
failed by assertion against temporary copies of the previous harness behavior; the smoke copy kept
the new result schema with restore success assumed, so schema errors could not masquerade as proof.
Confirmed PSS sampling also retains budget-boundary coverage.

Device checks were not run for this round. End-to-end smoke and qualification runs, existing-override
restoration, and the keyguard checks listed above remain for the next physical-device pass.
