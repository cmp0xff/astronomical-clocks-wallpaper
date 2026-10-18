# SPDX-License-Identifier: Apache-2.0
"""
Run observable live-wallpaper checks on one physical Android device via ADB.

This harness verifies lifecycle transitions, dial rendering accuracy across time shifts,
a bounded screen-off CPU observation, civil-hand accuracy across site-aware midnight, memory
growth stability, and renderer-log diagnostics.

The shared device layer owns the ADB primitives, the dial/frame analysis, and the target device
identity; this module keeps the qualification phases, its restore policy, and its report.
"""

import subprocess
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from host import contract
from host.device import frame, probes
from host.qualification import checks


def restore_device(
    device: probes.AdbDevice,
    size_override: str | None,
    *,
    initial_screen_was_on: bool | None,
    initial_night_mode: str | None,
) -> bool:
    """Restore every mutated setting and report whether the result could be verified."""
    restored = True
    if initial_night_mode is None:
        restored = False
        print("WARNING: initial night mode was unknown; it cannot be restored", file=sys.stderr)
    if initial_screen_was_on is None:
        restored = False
        print("WARNING: initial screen state was unknown; it cannot be restored", file=sys.stderr)

    for command in checks.restore_commands(size_override, initial_night_mode):
        if not device.run_restore_command(command):
            restored = False
    if not device.confirm_virtual_clock_reset():
        restored = False
    if initial_screen_was_on is False and not device.sleep_screen():
        restored = False
    if not checks.verify_restored_state(
        device,
        size_override,
        initial_screen_was_on=initial_screen_was_on,
        initial_night_mode=initial_night_mode,
    ):
        restored = False
    return restored


def phase_environment_setup(device: probes.AdbDevice, failures: list[str]) -> bool:
    """Wake the device, dismiss the keyguard, show home, and confirm the debug-clock reset."""
    print("\n--- Phase 0: Environment Wake & Unlocking ---")
    if not device.wake_screen():
        failures.append("Environment wake was not confirmed by the screen-state readback")
        return False
    device.dismiss_keyguard()
    device.show_home()
    time.sleep(1.0)
    confirmed = device.send_debug_clock_broadcast(contract.DEBUG_CLOCK_RESET_EXTRAS, contract.DEBUG_CLOCK_RESET_MESSAGE)
    if not confirmed:
        failures.append("Environment reset broadcast was not confirmed by the service log")
    time.sleep(0.5)
    return confirmed


def phase_baseline_capture(device: probes.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Capture one frame and locate the civil hand before any mutating phase."""
    w0, h0, px0 = device.capture_frame()
    palette_mode = "Dark" if frame.is_dark_palette(w0, h0, px0) else "Light"
    angle0 = frame.detect_hand_angle(w0, h0, px0)
    print(f"Detected dial palette: {palette_mode}")
    if angle0 is not None:
        print(f"Baseline hand angle t0: {angle0:.3f}°")
        results.append(("visible dial baseline", f"Dial rendered ({palette_mode} palette), hand at {angle0:.3f}°"))
    else:
        failures.append("Hand not found at baseline t0")


def phase_screen_off_wake(device: probes.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Sleep the screen, bound the wallpaper's CPU use while it is off, wake it, and confirm the hand."""
    print("\n--- Phase 1: Screen-Off / Wake Navigation ---")
    pid_before = checks.read_safe_pid(device)

    if not device.sleep_screen():
        failures.append("Screen sleep request failed; the screen-off / wake phase was skipped")
        return
    time.sleep(checks.SCREEN_OFF_SETTLE_SECONDS)
    screen_went_off = device.read_screen_on() is False
    wallpaper_visible = device.read_wallpaper_visible()
    if not screen_went_off:
        failures.append("Display was not confirmed off after sleep request")
        return

    if wallpaper_visible is True:
        failures.append("Wallpaper reported visible (mVisible=true) while the screen was confirmed off")

    # A rebound process would make the tick reading describe a different process than the one under
    # test, so re-read the pid after the screen settles rather than sampling against the stale one.
    pid = checks.confirm_pid_unchanged(device, pid_before, failures, description="screen-off transition")

    # The sleep below is uninterrupted: nothing reads the display state or mVisible inside the window.
    # Only the display-off state is bracketed by the read_screen_on reads on either side of it;
    # mVisible was read once, before the window, and is not observed throughout.
    ticks_start = checks.read_safe_cpu_ticks(device, pid)
    window_start = time.monotonic()
    time.sleep(checks.SCREEN_OFF_SAMPLE_SECONDS)
    window_seconds = time.monotonic() - window_start
    ticks_end = checks.read_safe_cpu_ticks(device, pid)
    # A rebound inside the sample window would attribute the closing ticks to a different process
    # than the one whose ticks opened it, so the identity is re-confirmed at the window's end.
    checks.confirm_pid_unchanged(device, pid, failures, description="CPU sample window")
    if device.read_screen_on() is not False:
        failures.append("Display was not confirmed off at the end of the CPU sample window")
    clock_ticks = device.read_process_cpu_clock_ticks()

    ticks_delta, cpu_failure = checks.evaluate_screen_off_cpu(
        ticks_start=ticks_start,
        ticks_end=ticks_end,
        clock_ticks=clock_ticks,
        window_seconds=window_seconds,
    )
    if cpu_failure is not None:
        failures.append(cpu_failure)

    # Wake device back up
    if not device.wake_screen():
        failures.append("Device wake was not confirmed after the screen-off interval")
        return
    device.dismiss_keyguard()
    device.show_home()
    time.sleep(1.0)

    screen_is_on = device.read_screen_on() is True
    w_wake, h_wake, px_wake = device.capture_frame()
    angle_wake = frame.detect_hand_angle(w_wake, h_wake, px_wake)

    if not (screen_is_on and angle_wake is not None):
        failures.append(
            f"Screen-off / wake navigation failed "
            f"(screen_off={screen_went_off}, screen_on_after_wake={screen_is_on}, hand={angle_wake})"
        )
        return

    print(f"Screen-off state observed; hand detected after wake at {angle_wake:.3f}°.")
    if ticks_delta is not None and ticks_delta > 0:
        print(f"Wallpaper process consumed {ticks_delta} CPU ticks while screen off.")

    results.append(
        (
            "screen-off / wake navigation",
            checks.format_screen_off_observation(
                wallpaper_visible=wallpaper_visible,
                ticks_delta=ticks_delta,
                clock_ticks=clock_ticks,
                window_seconds=window_seconds if ticks_delta is not None else None,
                angle_wake=angle_wake,
            ),
        )
    )


def ensure_screen_on(device: probes.AdbDevice, failures: list[str]) -> bool:
    """Re-confirm a visible screen before a phase, waking it if the display timeout switched it off."""
    if device.read_screen_on() is True:
        return True
    if not device.wake_screen():
        failures.append("Screen wake was not confirmed before a phase that needs the screen on")
        return False
    device.dismiss_keyguard()
    device.show_home()
    time.sleep(1.0)
    return True


def phase_preview_navigation(device: probes.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Open the live-wallpaper preview, back out, and confirm the home hand renders."""
    print("\n--- Phase 2: Preview Navigation ---")
    if not ensure_screen_on(device, failures):
        return
    preview_start = device.run_adb(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.service.wallpaper.CHANGE_LIVE_WALLPAPER",
            "-W",
            "--ecn",
            "android.service.wallpaper.extra.LIVE_WALLPAPER_COMPONENT",
            contract.SERVICE_NAME,
        ]
    ).decode("utf-8")
    preview_started = "Status: ok" in preview_start
    if not preview_started:
        failures.append("Wallpaper preview did not report a successful launch")
    time.sleep(2.0)

    # Back out of preview to home
    device.run_adb(["shell", "input", "keyevent", "KEYCODE_BACK"])
    time.sleep(0.8)
    device.show_home()
    time.sleep(1.0)

    w_preview_back, h_preview_back, px_preview_back = device.capture_frame()
    angle_post_preview = frame.detect_hand_angle(w_preview_back, h_preview_back, px_preview_back)

    if preview_started and angle_post_preview is not None:
        print(f"Returned from preview; hand visible on home screen at {angle_post_preview:.3f}°.")
        results.append(
            (
                "preview navigation",
                (
                    f"returned to home and detected the active wallpaper hand at {angle_post_preview:.3f}°; "
                    "preview-engine cleanup was not inspected"
                ),
            )
        )
    elif preview_started:
        failures.append("Hand missing after preview close")


def phase_surface_recreation(
    device: probes.AdbDevice,
    size_override: str | None,
    recreate_size: str,
    results: list[tuple[str, str]],
    failures: list[str],
) -> None:
    """Override the display size and restore it, confirming the dial is redrawn."""
    print("\n--- Phase 3: Surface Recreation ---")
    if not ensure_screen_on(device, failures):
        return
    device.run_adb(["shell", "wm", "size", recreate_size])
    time.sleep(0.5)
    _, active_override = device.read_display_size()
    device.run_adb(frame.size_restore_command(size_override))
    time.sleep(0.8)
    physical, restored_override = device.read_display_size()
    if physical is None or restored_override != size_override:
        failures.append("Display size was not verified after surface recreation restore")
        return

    w_recreate, h_recreate, px_recreate = device.capture_frame()
    angle_recreate = frame.detect_hand_angle(w_recreate, h_recreate, px_recreate)
    if active_override != recreate_size:
        failures.append(f"Display-size override was not active as requested ({active_override})")
    elif angle_recreate is not None:
        print(f"Surface recreated via {recreate_size}; hand rendered at {angle_recreate:.3f}°")
        results.append(
            (
                "surface recreation",
                (
                    f"Override to {recreate_size} and verified restore to {size_override or 'physical size'} "
                    f"redrew dial; hand at {angle_recreate:.3f}°"
                ),
            )
        )
    else:
        failures.append("Hand not drawn after surface recreation")


def phase_process_rebind(device: probes.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Kill the wallpaper process as its own uid and observe a rebound PID with a drawn hand."""
    print("\n--- Phase 4: Process Recreation (kill -9 simulation) ---")
    if not ensure_screen_on(device, failures):
        return
    pid_before = device.get_wallpaper_pid()
    pid_after: int | None = None
    angle_post_kill: float | None = None
    if pid_before is None:
        failures.append("Wallpaper process was not running before the process-recreation check")
    else:
        device.run_adb(["shell", "run-as", contract.PACKAGE_NAME, "kill", "-9", str(pid_before)])
        for _ in range(checks.REBIND_POLL_COUNT):
            time.sleep(checks.REBIND_POLL_SECONDS)
            curr_pid = device.get_wallpaper_pid()
            if curr_pid is None or curr_pid == pid_before:
                continue
            pid_after = curr_pid
            try:
                w_kill, h_kill, px_kill = device.capture_frame()
            except frame.ScreencapError:
                # A fresh PID exists before its engine is necessarily visible with a surface, so the
                # first layout reports after rebind can be absent or unusable. Retry within the poll
                # budget; the budget expiring still fails below rather than guessing a layout.
                continue
            angle_post_kill = frame.detect_hand_angle(w_kill, h_kill, px_kill)
            if angle_post_kill is not None:
                break
        print(f"PID transition: {pid_before} -> {pid_after}")

        if pid_after is not None and pid_after != pid_before and angle_post_kill is not None:
            results.append(
                (
                    "process rebind",
                    (
                        f"new PID observed within {checks.REBIND_POLL_COUNT} polls at "
                        f"{checks.REBIND_POLL_SECONDS}s intervals; "
                        f"hand visible at {angle_post_kill:.3f}°; saved preference values were not inspected"
                    ),
                )
            )
        else:
            failures.append(f"Process recreation failed (PIDs: {pid_before} -> {pid_after}, angle={angle_post_kill})")


def measure_hand_advance(
    device: probes.AdbDevice, offset_args: list[str], expected_message: str
) -> tuple[bool, float | None]:
    """Advance the virtual clock by one debug offset, returning (confirmed, hand angle after)."""
    if not device.send_debug_clock_broadcast(offset_args, expected_message):
        return False, None
    time.sleep(0.3)
    w_after, h_after, px_after = device.capture_frame()
    return True, frame.detect_hand_angle(w_after, h_after, px_after)


def phase_time_travel(device: probes.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Advance the virtual clock +30m then +12h and compare the measured hand advance."""
    print("\n--- Phase 5: Virtual Time Travel (+30m, +12h) ---")
    if not ensure_screen_on(device, failures):
        return
    if not device.send_debug_clock_broadcast(contract.DEBUG_CLOCK_RESET_EXTRAS, contract.DEBUG_CLOCK_RESET_MESSAGE):
        failures.append("Time-travel baseline reset was not confirmed by the service log")
        return
    time.sleep(0.8)

    w_base, h_base, px_base = device.capture_frame()
    a_base = frame.detect_hand_angle(w_base, h_base, px_base)

    # Advance +30 minutes
    confirmed_30m, a_30m = measure_hand_advance(
        device, ["--el", "offset_minutes", "30"], "Debug clock offset set to 1800000ms"
    )
    if not confirmed_30m:
        failures.append("+30m time offset was not confirmed by the service log")

    # Advance +12 hours
    confirmed_12h, a_12h = measure_hand_advance(
        device, ["--el", "offset_hours", "12"], "Debug clock offset set to 43200000ms"
    )
    if not confirmed_12h:
        failures.append("+12h time offset was not confirmed by the service log")

    if a_base is not None and a_30m is not None and a_12h is not None:
        delta_30m = (a_30m - a_base) % contract.FULL_TURN_DEG
        res_30m = delta_30m - contract.EXPECTED_ADVANCE_30M_DEG
        delta_12h = (a_12h - a_base) % contract.FULL_TURN_DEG
        res_12h = delta_12h - contract.EXPECTED_ADVANCE_12H_DEG

        print(
            f"+30m advance: {delta_30m:.3f}° "
            f"(expected: {contract.EXPECTED_ADVANCE_30M_DEG:.3f}°, residual: {res_30m:+.3f}°)"
        )
        print(
            f"+12h advance: {delta_12h:.3f}° "
            f"(expected: {contract.EXPECTED_ADVANCE_12H_DEG:.3f}°, residual: {res_12h:+.3f}°)"
        )

        # Compare the raw residuals, never a rounded copy: ANGLE_REPORT_DECIMALS is display-only,
        # and rounding first would accept a residual just above the boundary. The comparison is
        # inclusive, so a residual of exactly ANGLE_TOLERANCE_DEG passes.
        if abs(res_30m) <= contract.ANGLE_TOLERANCE_DEG and abs(res_12h) <= contract.ANGLE_TOLERANCE_DEG:
            results.append(
                (
                    "time travel (+30m, +12h)",
                    (
                        f"+30m moved hand {delta_30m:.3f}° (residual {res_30m:+.3f}°); "
                        f"+12h moved {delta_12h:.3f}° (residual {res_12h:+.3f}°)"
                    ),
                )
            )
        else:
            failures.append(f"Angle residuals exceeded tolerance: 30m={res_30m:+.3f}°, 12h={res_12h:+.3f}°")
    else:
        failures.append("Hand angle missing during time travel")


def set_debug_instant(device: probes.AdbDevice, instant_str: str) -> tuple[bool, float | None]:
    """Set the virtual clock to an ISO instant, returning (confirmed, hand angle after)."""
    expected_message = f"Debug clock fixed to instant: {instant_str}"
    if not device.send_debug_clock_broadcast(["--es", "instant", instant_str], expected_message):
        return False, None
    time.sleep(0.3)
    w_after, h_after, px_after = device.capture_frame()
    return True, frame.detect_hand_angle(w_after, h_after, px_after)


def phase_midnight_rollover(device: probes.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Step the virtual clock across the rendered zone's civil midnight and confirm the civil hand."""
    print("\n--- Phase 6: Midnight Date Rollover ---")
    if not ensure_screen_on(device, failures):
        return
    try:
        resolved = checks.resolve_rollover_zone(device)
    except probes.ProbeError as error:
        failures.append(
            "Midnight rollover could not be verified: the saved-site preference probe failed, "
            f"so the rendered zone is unknown ({probes.error_detail(error)})"
        )
        return
    if resolved is None:
        failures.append(
            "Midnight rollover could not be verified: neither the saved site zone nor the device timezone was readable"
        )
        return
    zone_id, source = resolved
    # Java's ZoneId.of accepts ids Python's ZoneInfo cannot build (fixed offsets, "Z", "UT"), so an
    # id that does not resolve here is not proof that the app fell back to the device zone: fail
    # loudly rather than sample a zone the wallpaper may not be rendering.
    try:
        zone = ZoneInfo(zone_id)
    except (ZoneInfoNotFoundError, ValueError):
        failures.append(
            f"Midnight rollover could not be verified: the rendered zone {zone_id!r} ({source}) "
            "does not resolve to a timezone this harness can construct"
        )
        return

    instants = checks.rollover_instants(zone)
    angles: list[float] = []
    for instant in instants:
        confirmed, angle = set_debug_instant(device, instant)
        if not confirmed or angle is None:
            failures.append(f"Midnight sample instant ({instant}) was not confirmed or hand not detected")
            return
        angles.append(angle)

    expected, residuals, problems = checks.evaluate_rollover(zone, instants, angles, zone_id)
    print(
        f"Midnight rollover in {zone_id} ({source}): expected absolute angles "
        f"{[round(value, checks.ANGLE_REPORT_DECIMALS) for value in expected]}, measured "
        f"{[round(angle, checks.ANGLE_REPORT_DECIMALS) for angle in angles]}, residuals "
        f"{[round(residual, checks.ANGLE_REPORT_DECIMALS) for residual in residuals]}"
    )
    if problems:
        failures.extend(problems)
    else:
        expected_text = ", ".join(f"{value:.3f}°" for value in expected)
        residual_text = ", ".join(f"{residual:+.3f}°" for residual in residuals)
        results.append(
            (
                "midnight date rollover",
                (
                    f"zone {zone_id} ({source}); samples {', '.join(instants)} each matched the zone's "
                    f"absolute civil-hand angle (expected {expected_text}; residuals {residual_text}); "
                    "the boundary is bracketed within 10s, not resolved"
                ),
            )
        )

    if not device.send_debug_clock_broadcast(contract.DEBUG_CLOCK_RESET_EXTRAS, contract.DEBUG_CLOCK_RESET_MESSAGE):
        failures.append("Post-midnight clock reset was not confirmed by the service log")


def phase_total_pss_growth(
    device: probes.AdbDevice, max_pss_growth_kb: int, results: list[tuple[str, str]], failures: list[str]
) -> None:
    """Sample total PSS ten seconds apart and compare the growth against the supplied budget."""
    print("\n--- Phase 7: Total PSS Growth ---")
    if not ensure_screen_on(device, failures):
        return
    if not device.send_debug_clock_broadcast(contract.DEBUG_CLOCK_RESET_EXTRAS, contract.DEBUG_CLOCK_RESET_MESSAGE):
        failures.append("PSS baseline reset was not confirmed by the service log")
        return
    time.sleep(0.5)

    mem_before = device.run_adb(["shell", "dumpsys", "meminfo", contract.PACKAGE_NAME]).decode("utf-8")
    time.sleep(checks.PSS_SAMPLE_SECONDS)
    mem_after = device.run_adb(["shell", "dumpsys", "meminfo", contract.PACKAGE_NAME]).decode("utf-8")

    pss_before = checks.extract_total_pss_kb(mem_before)
    pss_after = checks.extract_total_pss_kb(mem_after)
    pss_result = checks.evaluate_pss_growth(pss_before, pss_after, max_pss_growth_kb)
    if pss_result is None:
        failures.append("Could not parse total PSS from both memory samples")
        print(f"PSS sample before:\n{mem_before}", file=sys.stderr)
        print(f"PSS sample after:\n{mem_after}", file=sys.stderr)
    else:
        pss_growth, within_budget = pss_result
        print(
            f"Total PSS: {pss_before} kB -> {pss_after} kB (growth {pss_growth:+d} kB; budget {max_pss_growth_kb} kB)"
        )
        if not within_budget:
            failures.append(f"Total PSS growth {pss_growth} kB exceeded the {max_pss_growth_kb} kB budget")
        else:
            results.append(
                (
                    "total PSS sample",
                    (
                        f"{pss_before} -> {pss_after} kB over {checks.PSS_SAMPLE_SECONDS:.0f}s "
                        f"(growth {pss_growth:+d} kB; budget {max_pss_growth_kb} kB); not battery or CPU evidence"
                    ),
                )
            )


def phase_renderer_log_scan(
    device: probes.AdbDevice, run_start_marker: str, results: list[tuple[str, str]], failures: list[str]
) -> bool:
    """Scan the run's renderer log lines and append the outcome to the results."""
    print("\n--- Phase 9: Renderer Log Scan ---")
    log_scan_inconclusive = False
    try:
        log_check = device.run_adb(
            [
                "logcat",
                "-d",
                "-T",
                run_start_marker,
                "-s",
                f"{contract.SERVICE_LOG_TAG}:W",
                "DialRenderer:W",
            ]
        ).decode("utf-8")
        warnings = checks.matching_renderer_warnings(log_check)
        if not warnings:
            log_scan_inconclusive = True
            log_result = "Inconclusive: no matching warning records; rendering was not verified"
            print("Renderer log scan inconclusive: no matching warning records.")
        else:
            log_result = f"{len(warnings)} matching warning/error record(s)"
            print(f"Renderer warning/error scan: {len(warnings)} record(s)")
            failures.append(f"{len(warnings)} unexpected warning(s) in logcat")
        results.append(("renderer log scan", log_result))
    except (subprocess.SubprocessError, OSError) as error:
        failures.append(f"Renderer log scan could not be collected: {probes.error_detail(error)}")
        results.append(("renderer log scan", f"Failed to collect: {probes.error_detail(error)}"))
        print(f"Renderer log scan failed: {probes.error_detail(error)}", file=sys.stderr)
    return log_scan_inconclusive


def read_device_baseline(requested_serial: str | None) -> checks.DeviceBaseline:
    """Inspect the device before any mutation, exiting when a precondition cannot be read."""
    serial = probes.select_target_serial(requested_serial)
    print(f"=== Starting Device Checks on target: {serial} ===")
    device = probes.AdbDevice(serial)
    try:
        run_start_marker = device.run_adb(["shell", "date +'%m-%d %H:%M:%S.000'"]).decode().strip()
        print(f"Run start timestamp: {run_start_marker}")
        physical_size, size_override = device.read_display_size()
        screen_was_on = device.read_screen_on()
        night_mode = device.read_night_mode()
        keyguard_locked = device.read_keyguard_locked()
        pid_start = device.get_wallpaper_pid()
    except (subprocess.SubprocessError, OSError) as error:
        print(
            f"ERROR: Could not inspect the device before making changes: {probes.error_detail(error)}",
            file=sys.stderr,
        )
        sys.exit(1)

    if physical_size is None:
        print("ERROR: could not read the physical display size; refusing to change device state.", file=sys.stderr)
        sys.exit(1)
    if screen_was_on is None:
        print("ERROR: could not read the initial screen state; refusing to wake the device.", file=sys.stderr)
        sys.exit(1)
    if night_mode is None:
        print("ERROR: could not read the initial night mode; refusing to change device state.", file=sys.stderr)
        sys.exit(1)

    if keyguard_locked is None:
        print(
            "ERROR: could not read the keyguard state; refusing to run on an unconfirmed lock state.",
            file=sys.stderr,
        )
        sys.exit(1)
    if keyguard_locked:
        print("ERROR: device is locked; unlock it before running device checks.", file=sys.stderr)
        sys.exit(1)

    print(f"Initial wallpaper PID: {pid_start}")
    if pid_start is None:
        print("ERROR: Wallpaper service is not running. Apply wallpaper before qualification.", file=sys.stderr)
        sys.exit(1)

    return checks.DeviceBaseline(
        serial=serial,
        run_start_marker=run_start_marker,
        physical_size=physical_size,
        size_override=size_override,
        screen_was_on=screen_was_on,
        night_mode=night_mode,
    )


def phase_hidden_frames(device: probes.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Evidence zero rendering while the engine is hidden and the display is off (#125 criterion 1)."""
    print("\n--- Phase 8: Hidden Producer Window ---")
    pid_before = checks.read_safe_pid(device)
    control = checks.measure_producer_window(
        device, pid_before, checks.PRODUCER_CONTROL_SECONDS, results, failures, description="visible control"
    )
    if not checks.evaluate_producer_control(control, failures, description="visible control window"):
        return

    if not device.sleep_screen():
        failures.append("Screen sleep request failed; the hidden producer window was skipped")
        return
    time.sleep(checks.SCREEN_OFF_SETTLE_SECONDS)
    if device.read_screen_on() is not False:
        failures.append("Display was not confirmed off before the hidden producer window")
        return
    visible = device.read_wallpaper_visible()
    if visible is not False:
        failures.append(
            f"The wallpaper engine was not confirmed hidden before the producer window (mVisible={visible}); "
            "an unreadable probe is not a hidden engine, so the window was not recorded"
        )
        if not device.wake_screen():
            failures.append("Screen wake request failed after the hidden window was abandoned")
        return

    pid = checks.confirm_bracketed_pid(device, pid_before, failures)
    if pid is None:
        return

    recorded_from = len(results)
    hidden = checks.measure_producer_window(
        device,
        pid,
        checks.PRODUCER_HIDDEN_SECONDS,
        results,
        failures,
        description=checks.PRODUCER_HIDDEN_DESCRIPTION,
        announce=False,
    )
    # The trace is filtered to one pid, so a process that died or restarted inside the window leaves
    # its replacement's frames outside the trace. That zero is the wrong process's, so the result is
    # removed rather than printed beside the failure as if it were a measurement, and the evaluation
    # that would have validated it is skipped.
    pid_after = checks.confirm_pid_unchanged(device, pid, failures, description="hidden producer window")
    if pid_after != pid:
        del results[recorded_from:]
    else:
        checks.accept_hidden_window(hidden, failures)

    if not device.wake_screen():
        failures.append("Screen wake request failed; the closing control trace was skipped")
        return
    # Waking only confirms the display is on, which on a locked device is the keyguard rather than
    # the home screen the opening control ran on. The closing control has to bracket the hidden
    # window in the same context to be its counterpart, so the keyguard is dismissed and Home shown
    # before it is traced, as `phase_screen_off_wake` does after its own wake.
    device.dismiss_keyguard()
    device.show_home()
    time.sleep(checks.SCREEN_OFF_SETTLE_SECONDS)
    # The closing control asks whether the tracer still works, not whether the process survived the
    # wake. The pid that held across the hidden window may have been recreated during the wake and
    # Home restoration, and tracing a dead pid finds no frames, so the tracer would be failed for a
    # process change that says nothing about it. The current pid is read and traced instead; when it
    # cannot be read, `measure_producer_window` reports that and no tracer verdict is drawn.
    closing_pid = checks.read_safe_pid(device)
    closing = checks.measure_producer_window(
        device, closing_pid, checks.PRODUCER_CONTROL_SECONDS, results, failures, description="visible control after"
    )
    checks.evaluate_closing_control(closing, failures)


def main() -> None:
    """Inspect the device, run every check phase, restore the device, and report."""
    args = checks.build_argument_parser().parse_args()
    baseline = read_device_baseline(args.serial)
    device = probes.AdbDevice(baseline.serial)
    recreate_size = frame.choose_recreate_size(baseline.physical_size, baseline.size_override)

    results: list[tuple[str, str]] = []
    failures: list[str] = []
    restored = True
    current_phase = "environment setup"

    try:
        current_phase = "environment setup"
        reset_confirmed = phase_environment_setup(device, failures)

        if reset_confirmed:
            current_phase = "baseline capture"
            phase_baseline_capture(device, results, failures)

            current_phase = "screen-off / wake navigation"
            phase_screen_off_wake(device, results, failures)

            current_phase = "preview navigation"
            phase_preview_navigation(device, results, failures)

            current_phase = "surface recreation"
            phase_surface_recreation(device, baseline.size_override, recreate_size, results, failures)

            current_phase = "process rebind"
            phase_process_rebind(device, results, failures)

            current_phase = "virtual time travel"
            phase_time_travel(device, results, failures)

            current_phase = "midnight date rollover"
            phase_midnight_rollover(device, results, failures)

            current_phase = "total PSS growth"
            phase_total_pss_growth(device, args.max_pss_growth_kb, results, failures)

            current_phase = "hidden producer window"
            phase_hidden_frames(device, results, failures)
        else:
            print("Skipping phases 1-8: environment setup was not confirmed.", file=sys.stderr)

    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        failures.append(f"Run aborted during {current_phase}: {probes.error_detail(error)}")
    finally:
        print("\n--- Restoring device state in finally block ---")
        restored = restore_device(
            device,
            baseline.size_override,
            initial_screen_was_on=baseline.screen_was_on,
            initial_night_mode=baseline.night_mode,
        )
    if not restored:
        failures.append("Device restoration failed or could not be verified")

    current_phase = "renderer log scan"
    log_scan_inconclusive = phase_renderer_log_scan(device, baseline.run_start_marker, results, failures)

    checks.finish_run(results, failures, log_scan_inconclusive=log_scan_inconclusive)
