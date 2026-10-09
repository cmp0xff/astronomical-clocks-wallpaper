#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Run observable live-wallpaper checks on one physical Android device via ADB.

This harness verifies lifecycle transitions, dial rendering accuracy across time shifts,
screen-off CPU quiescence, midnight date rollover, memory growth stability, and renderer-log
diagnostics.

The shared device layer owns the ADB primitives, the dial/frame analysis, and the target device
identity; this module keeps the qualification phases, its restore policy, and its report.
"""

import argparse
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import device_layer

REBIND_POLL_COUNT: Final = 10
REBIND_POLL_SECONDS: Final = 0.5
PSS_SAMPLE_SECONDS: Final = 10.0
ANGLE_REPORT_DECIMALS: Final = 3

# Screen-off CPU evidence: sample the wallpaper process's tick counter across a measured,
# confirmed-off interval and bound its CPU time against a budget. One tick on the device clock
# is the smallest observable unit, so a zero-tick sample bounds CPU time below one tick rather
# than at zero.
SCREEN_OFF_SETTLE_SECONDS: Final = 1.0
SCREEN_OFF_SAMPLE_SECONDS: Final = 4.0
SCREEN_OFF_CPU_BUDGET_SECONDS: Final = 0.05

# Rollover instants are built in the zone the service actually renders with (the saved site's,
# falling back to the device's). The reference date fixes a summer instant whose zone offset is
# stable across the 90-minute window, and the four samples bracket local civil midnight by 10
# seconds (23:15:00, 23:59:50, 00:00:10, 00:45:00) so a frozen hand cannot sit within tolerance.
ROLLOVER_FIRST_LOCAL: Final = (2026, 6, 20, 23, 15, 0)
ROLLOVER_SAMPLE_OFFSETS_SECONDS: Final = (0.0, 2690.0, 2710.0, 5400.0)


@dataclass(frozen=True)
class DeviceBaseline:
    """The device state the run found, used to size and later restore every mutation."""

    serial: str
    run_start_marker: str
    physical_size: str
    size_override: str | None
    screen_was_on: bool
    night_mode: str
    wallpaper_pid: int


def non_negative_int(value: str) -> int:
    """Parse a command-line integer, rejecting anything below zero."""
    parsed = int(value)
    if parsed < 0:
        message = "must be zero or greater"
        raise argparse.ArgumentTypeError(message)
    return parsed


def extract_total_pss_kb(mem_output: str) -> int | None:
    """Return the TOTAL row's kB figure from `dumpsys meminfo`, or None when it is absent."""
    for line in mem_output.splitlines():
        parts = line.split()
        if len(parts) >= device_layer.MIN_SPLIT_FIELDS and parts[0] == "TOTAL" and parts[1].isdigit():
            return int(parts[1])
    return None


def evaluate_pss_growth(pss_before: int | None, pss_after: int | None, max_growth_kb: int) -> tuple[int, bool] | None:
    """Return (growth, within budget) for two PSS samples, or None when either is missing."""
    if pss_before is None or pss_after is None:
        return None
    growth_kb = pss_after - pss_before
    return growth_kb, growth_kb <= max_growth_kb


def matching_renderer_warnings(output: str) -> list[str]:
    """Keep the logcat lines that are records, dropping blanks and the `---------` separators."""
    return [line for line in output.splitlines() if line.strip() and not line.startswith("---------")]


def restore_commands(size_override: str | None, initial_night_mode: str | None) -> list[list[str]]:
    """Build the ordered commands that put every mutated setting back."""
    commands = [
        ["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
        device_layer.size_restore_command(size_override),
    ]
    if initial_night_mode is not None:
        commands.insert(2, ["shell", "cmd", "uimode", "night", initial_night_mode])
    commands.append(["shell", "input", "keyevent", "KEYCODE_HOME"])
    return commands


def verify_restored_state(
    device: device_layer.AdbDevice,
    size_override: str | None,
    *,
    initial_screen_was_on: bool | None,
    initial_night_mode: str | None,
) -> bool:
    """Re-read the mutated settings and report whether each returned to what the run found."""
    try:
        restored_physical_size, restored_size_override = device.read_display_size()
        restored_night_mode = device.read_night_mode() if initial_night_mode is not None else None
        restored_screen_on = device.read_screen_on() if initial_screen_was_on is not None else None
        restored_keyguard_locked = device.read_keyguard_locked()
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: could not verify restored device state: {device_layer.error_detail(error)}", file=sys.stderr)
        return False

    restored = True
    if restored_physical_size is None:
        restored = False
        print("WARNING: could not read the display size after restore", file=sys.stderr)
    elif restored_size_override != size_override:
        restored = False
        print("WARNING: display size did not return to its initial state", file=sys.stderr)
    if restored_night_mode != initial_night_mode:
        restored = False
        print("WARNING: night mode did not return to its initial state", file=sys.stderr)
    if restored_screen_on != initial_screen_was_on:
        restored = False
        print("WARNING: screen state did not return to its initial state", file=sys.stderr)
    if restored_keyguard_locked is not False:
        restored = False
        print("WARNING: device was not confirmed unlocked after the run", file=sys.stderr)
    return restored


def restore_device(
    device: device_layer.AdbDevice,
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

    for command in restore_commands(size_override, initial_night_mode):
        if not device.run_restore_command(command):
            restored = False
    if not device.confirm_virtual_clock_reset():
        restored = False
    if initial_screen_was_on is False and not device.sleep_screen():
        restored = False
    if not verify_restored_state(
        device,
        size_override,
        initial_screen_was_on=initial_screen_was_on,
        initial_night_mode=initial_night_mode,
    ):
        restored = False
    return restored


def phase_environment_setup(device: device_layer.AdbDevice, failures: list[str]) -> bool:
    """Wake the device, dismiss the keyguard, show home, and confirm the debug-clock reset."""
    print("\n--- Phase 0: Environment Wake & Unlocking ---")
    if not device.wake_screen():
        failures.append("Environment wake was not confirmed by the screen-state readback")
        return False
    device.dismiss_keyguard()
    device.show_home()
    time.sleep(1.0)
    confirmed = device.send_debug_clock_broadcast(
        device_layer.DEBUG_CLOCK_RESET_EXTRAS, device_layer.DEBUG_CLOCK_RESET_MESSAGE
    )
    if not confirmed:
        failures.append("Environment reset broadcast was not confirmed by the service log")
    time.sleep(0.5)
    return confirmed


def phase_baseline_capture(device: device_layer.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Capture one frame and locate the civil hand before any mutating phase."""
    w0, h0, px0 = device.capture_frame()
    palette_mode = "Dark" if device_layer.is_dark_palette(w0, h0, px0) else "Light"
    angle0 = device_layer.detect_hand_angle(w0, h0, px0)
    print(f"Detected dial palette: {palette_mode}")
    if angle0 is not None:
        print(f"Baseline hand angle t0: {angle0:.3f}°")
        results.append(("visible dial baseline", f"Dial rendered ({palette_mode} palette), hand at {angle0:.3f}°"))
    else:
        failures.append("Hand not found at baseline t0")


def read_safe_pid(device: device_layer.AdbDevice) -> int | None:
    """Return the wallpaper PID, returning None on probe failure."""
    try:
        return device.get_wallpaper_pid()
    except (subprocess.SubprocessError, OSError):
        return None


def confirm_pid_unchanged(
    device: device_layer.AdbDevice, expected_pid: int | None, failures: list[str], *, description: str
) -> int | None:
    """Re-read the wallpaper PID and fail unless it still matches, returning the fresh reading."""
    pid = read_safe_pid(device)
    if pid is None or pid != expected_pid:
        failures.append(
            f"Wallpaper PID was not confirmed unchanged across the {description} "
            f"(before={expected_pid}, after={pid}); the CPU sample is not attributable"
        )
    return pid


def read_safe_cpu_ticks(device: device_layer.AdbDevice, pid: int | None) -> int | None:
    """Return process CPU ticks, returning None on missing pid or probe failure."""
    if pid is None:
        return None
    try:
        return device.read_process_cpu_ticks(pid)
    except (subprocess.SubprocessError, OSError):
        return None


def format_screen_off_observation(
    *,
    wallpaper_visible: bool | None,
    ticks_delta: int | None,
    clock_ticks: int | None,
    window_seconds: float | None,
    angle_wake: float,
) -> str:
    """Build the descriptive observation record for the screen-off / wake phase."""
    if wallpaper_visible is False:
        visibility_text = "wallpaper reported hidden (mVisible=false)"
    elif wallpaper_visible is True:
        visibility_text = "wallpaper reported visible (mVisible=true)"
    else:
        visibility_text = "wallpaper visibility was unreadable"

    if ticks_delta is not None and clock_ticks is not None and window_seconds:
        if ticks_delta == 0:
            # A zero-whole-tick sample bounds CPU time below one tick, never at exactly zero.
            bound_seconds = 1 / clock_ticks
            cpu_text = (
                f"{ticks_delta} CPU ticks over the measured {window_seconds:.2f}s screen-off window at "
                f"{clock_ticks} ticks/s (<{bound_seconds:.3f}s CPU, <{bound_seconds / window_seconds * 100:.2f}% "
                f"of the window; budget {SCREEN_OFF_CPU_BUDGET_SECONDS:.2f}s)"
            )
        else:
            cpu_seconds = ticks_delta / clock_ticks
            cpu_text = (
                f"{ticks_delta} CPU ticks over the measured {window_seconds:.2f}s screen-off window at "
                f"{clock_ticks} ticks/s ({cpu_seconds:.3f}s CPU, {cpu_seconds / window_seconds * 100:.2f}% "
                f"of the window; budget {SCREEN_OFF_CPU_BUDGET_SECONDS:.2f}s)"
            )
    else:
        cpu_text = "rendering while asleep was not measured"

    return f"{visibility_text}; {cpu_text}; hand detected after wake at {angle_wake:.3f}°"


def evaluate_screen_off_cpu(
    *,
    ticks_start: int | None,
    ticks_end: int | None,
    clock_ticks: int | None,
    window_seconds: float,
) -> tuple[int | None, str | None]:
    """Return (tick delta, failure message) for one confirmed-off sample window."""
    if ticks_start is None or ticks_end is None:
        return None, "Failed to sample wallpaper CPU ticks during confirmed screen-off state"
    if ticks_end < ticks_start:
        return None, f"CPU ticks went backward during screen-off: {ticks_start} -> {ticks_end}"
    if clock_ticks is None:
        return None, "Could not read the device clock-tick rate (getconf CLK_TCK); CPU time is unbounded"
    ticks_delta = ticks_end - ticks_start
    cpu_seconds = ticks_delta / clock_ticks
    if cpu_seconds > SCREEN_OFF_CPU_BUDGET_SECONDS:
        return ticks_delta, (
            f"Wallpaper process used {cpu_seconds:.3f}s CPU while screen off "
            f"({ticks_delta} ticks over the measured {window_seconds:.2f}s window "
            f"at {clock_ticks} ticks/s); budget {SCREEN_OFF_CPU_BUDGET_SECONDS:.2f}s"
        )
    return ticks_delta, None


def phase_screen_off_wake(device: device_layer.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Sleep the screen, bound the wallpaper's CPU use while it is off, wake it, and confirm the hand."""
    print("\n--- Phase 1: Screen-Off / Wake Navigation ---")
    pid_before = read_safe_pid(device)

    if not device.sleep_screen():
        failures.append("Screen sleep request failed; the screen-off / wake phase was skipped")
        return
    time.sleep(SCREEN_OFF_SETTLE_SECONDS)
    screen_went_off = device.read_screen_on() is False
    wallpaper_visible = device.read_wallpaper_visible()
    if not screen_went_off:
        failures.append("Display was not confirmed off after sleep request")
        return

    if wallpaper_visible is True:
        failures.append("Wallpaper reported visible (mVisible=true) while the screen was confirmed off")

    # A rebound process would make the tick reading describe a different process than the one under
    # test, so re-read the pid after the screen settles rather than sampling against the stale one.
    pid = confirm_pid_unchanged(device, pid_before, failures, description="screen-off transition")

    ticks_start = read_safe_cpu_ticks(device, pid)
    window_start = time.monotonic()
    time.sleep(SCREEN_OFF_SAMPLE_SECONDS)
    window_seconds = time.monotonic() - window_start
    ticks_end = read_safe_cpu_ticks(device, pid)
    # A rebound inside the sample window would attribute the closing ticks to a different process
    # than the one whose ticks opened it, so the identity is re-confirmed at the window's end.
    confirm_pid_unchanged(device, pid, failures, description="CPU sample window")
    if device.read_screen_on() is not False:
        failures.append("Display was not confirmed off at the end of the CPU sample window")
    clock_ticks = device.read_process_cpu_clock_ticks()

    ticks_delta, cpu_failure = evaluate_screen_off_cpu(
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
    angle_wake = device_layer.detect_hand_angle(w_wake, h_wake, px_wake)

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
            format_screen_off_observation(
                wallpaper_visible=wallpaper_visible,
                ticks_delta=ticks_delta,
                clock_ticks=clock_ticks,
                window_seconds=window_seconds if ticks_delta is not None else None,
                angle_wake=angle_wake,
            ),
        )
    )


def ensure_screen_on(device: device_layer.AdbDevice, failures: list[str]) -> bool:
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


def phase_preview_navigation(
    device: device_layer.AdbDevice, results: list[tuple[str, str]], failures: list[str]
) -> None:
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
            device_layer.SERVICE_NAME,
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
    angle_post_preview = device_layer.detect_hand_angle(w_preview_back, h_preview_back, px_preview_back)

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
    device: device_layer.AdbDevice,
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
    device.run_adb(device_layer.size_restore_command(size_override))
    time.sleep(0.8)
    physical, restored_override = device.read_display_size()
    if physical is None or restored_override != size_override:
        failures.append("Display size was not verified after surface recreation restore")
        return

    w_recreate, h_recreate, px_recreate = device.capture_frame()
    angle_recreate = device_layer.detect_hand_angle(w_recreate, h_recreate, px_recreate)
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


def phase_process_rebind(device: device_layer.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
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
        device.run_adb(["shell", "run-as", device_layer.PACKAGE_NAME, "kill", "-9", str(pid_before)])
        for _ in range(REBIND_POLL_COUNT):
            time.sleep(REBIND_POLL_SECONDS)
            curr_pid = device.get_wallpaper_pid()
            if curr_pid is None or curr_pid == pid_before:
                continue
            pid_after = curr_pid
            try:
                w_kill, h_kill, px_kill = device.capture_frame()
            except device_layer.ScreencapError:
                # A fresh PID exists before its engine is necessarily visible with a surface, so the
                # first layout reports after rebind can be absent or unusable. Retry within the poll
                # budget; the budget expiring still fails below rather than guessing a layout.
                continue
            angle_post_kill = device_layer.detect_hand_angle(w_kill, h_kill, px_kill)
            if angle_post_kill is not None:
                break
        print(f"PID transition: {pid_before} -> {pid_after}")

        if pid_after is not None and pid_after != pid_before and angle_post_kill is not None:
            results.append(
                (
                    "process rebind",
                    (
                        f"new PID observed within {REBIND_POLL_COUNT} polls at {REBIND_POLL_SECONDS}s intervals; "
                        f"hand visible at {angle_post_kill:.3f}°; saved preference values were not inspected"
                    ),
                )
            )
        else:
            failures.append(f"Process recreation failed (PIDs: {pid_before} -> {pid_after}, angle={angle_post_kill})")


def measure_hand_advance(
    device: device_layer.AdbDevice, offset_args: Sequence[str], expected_message: str
) -> tuple[bool, float | None]:
    """Advance the virtual clock by one debug offset, returning (confirmed, hand angle after)."""
    if not device.send_debug_clock_broadcast(offset_args, expected_message):
        return False, None
    time.sleep(0.3)
    w_after, h_after, px_after = device.capture_frame()
    return True, device_layer.detect_hand_angle(w_after, h_after, px_after)


def phase_time_travel(device: device_layer.AdbDevice, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Advance the virtual clock +30m then +12h and compare the measured hand advance."""
    print("\n--- Phase 5: Virtual Time Travel (+30m, +12h) ---")
    if not ensure_screen_on(device, failures):
        return
    if not device.send_debug_clock_broadcast(
        device_layer.DEBUG_CLOCK_RESET_EXTRAS, device_layer.DEBUG_CLOCK_RESET_MESSAGE
    ):
        failures.append("Time-travel baseline reset was not confirmed by the service log")
        return
    time.sleep(0.8)

    w_base, h_base, px_base = device.capture_frame()
    a_base = device_layer.detect_hand_angle(w_base, h_base, px_base)

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
        delta_30m = (a_30m - a_base) % device_layer.FULL_TURN_DEG
        res_30m = delta_30m - device_layer.EXPECTED_ADVANCE_30M_DEG
        delta_12h = (a_12h - a_base) % device_layer.FULL_TURN_DEG
        res_12h = delta_12h - device_layer.EXPECTED_ADVANCE_12H_DEG

        print(
            f"+30m advance: {delta_30m:.3f}° "
            f"(expected: {device_layer.EXPECTED_ADVANCE_30M_DEG:.3f}°, residual: {res_30m:+.3f}°)"
        )
        print(
            f"+12h advance: {delta_12h:.3f}° "
            f"(expected: {device_layer.EXPECTED_ADVANCE_12H_DEG:.3f}°, residual: {res_12h:+.3f}°)"
        )

        if (
            round(abs(res_30m), ANGLE_REPORT_DECIMALS) <= device_layer.ANGLE_TOLERANCE_DEG
            and round(abs(res_12h), ANGLE_REPORT_DECIMALS) <= device_layer.ANGLE_TOLERANCE_DEG
        ):
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


def set_debug_instant(device: device_layer.AdbDevice, instant_str: str) -> tuple[bool, float | None]:
    """Set the virtual clock to an ISO instant, returning (confirmed, hand angle after)."""
    expected_message = f"Debug clock fixed to instant: {instant_str}"
    if not device.send_debug_clock_broadcast(["--es", "instant", instant_str], expected_message):
        return False, None
    time.sleep(0.3)
    w_after, h_after, px_after = device.capture_frame()
    return True, device_layer.detect_hand_angle(w_after, h_after, px_after)


def resolve_rollover_zone(device: device_layer.AdbDevice) -> tuple[str, str] | None:
    """
    Resolve the zone the service renders with: the saved site's, else the device's.

    A device_layer.ProbeError from the saved-site read propagates: a None saved zone means a
    successful read confirmed no saved record, so the device timezone is the correct fallback, but a
    failed read must not be read as absence.
    """
    saved_site_zone = device.read_saved_site_zone_id()
    if saved_site_zone:
        return saved_site_zone, "saved site"
    device_timezone = device.read_device_timezone()
    if device_timezone:
        return device_timezone, "device timezone"
    return None


def rollover_instants(zone: ZoneInfo) -> list[str]:
    """Return ISO UTC instants bracketing the zone's local civil midnight on the reference date."""
    first = datetime(*ROLLOVER_FIRST_LOCAL, tzinfo=zone)
    return [
        (first + timedelta(seconds=offset)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for offset in ROLLOVER_SAMPLE_OFFSETS_SECONDS
    ]


def expected_rollover_angles_deg(zone: ZoneInfo, instants: list[str]) -> list[float]:
    """Return each UTC instant's absolute civil-hand angle in the zone, clockwise from the top."""
    angles: list[float] = []
    for instant in instants:
        local = datetime.fromisoformat(instant).astimezone(zone)
        angles.append(device_layer.civil_hand_angle_deg(local.hour * 3600 + local.minute * 60 + local.second))
    return angles


def evaluate_rollover(
    zone: ZoneInfo, instants: list[str], angles: list[float], zone_id: str
) -> tuple[list[float], list[float], list[str]]:
    """Return (expected absolute angles, circular residuals, problems) for one civil-midnight sweep."""
    expected = expected_rollover_angles_deg(zone, instants)
    residuals = [
        device_layer.signed_circular_difference_deg(angle, want) for angle, want in zip(angles, expected, strict=True)
    ]
    problems: list[str] = []
    for instant, residual in zip(instants, residuals, strict=True):
        if round(abs(residual), ANGLE_REPORT_DECIMALS) > device_layer.ANGLE_TOLERANCE_DEG:
            problems.append(
                f"Midnight rollover at {instant} (zone {zone_id}) hand angle residual {residual:+.3f}° "
                f"exceeds the {device_layer.ANGLE_TOLERANCE_DEG}° tolerance"
            )
    for index in range(1, len(angles)):
        # Only gaps wider than the probe jitter can order adjacent samples: the 20-second pair sits
        # 0.083° apart, below the tolerance, so it is checked as a bracket, not ordered against itself.
        discriminable = expected[index] - expected[index - 1] > device_layer.ANGLE_TOLERANCE_DEG
        if discriminable and angles[index] < angles[index - 1]:
            problems.append(
                f"Midnight rollover did not advance monotonically at {instants[index]} "
                f"({angles[index - 1]:.3f}° -> {angles[index]:.3f}°) in zone {zone_id}"
            )
    return expected, residuals, problems


def phase_midnight_rollover(
    device: device_layer.AdbDevice, results: list[tuple[str, str]], failures: list[str]
) -> None:
    """Step the virtual clock across the rendered zone's civil midnight and confirm the civil hand."""
    print("\n--- Phase 6: Midnight Date Rollover ---")
    if not ensure_screen_on(device, failures):
        return
    try:
        resolved = resolve_rollover_zone(device)
    except device_layer.ProbeError as error:
        failures.append(
            "Midnight rollover could not be verified: the saved-site preference probe failed, "
            f"so the rendered zone is unknown ({device_layer.error_detail(error)})"
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

    instants = rollover_instants(zone)
    angles: list[float] = []
    for instant in instants:
        confirmed, angle = set_debug_instant(device, instant)
        if not confirmed or angle is None:
            failures.append(f"Midnight sample instant ({instant}) was not confirmed or hand not detected")
            return
        angles.append(angle)

    expected, residuals, problems = evaluate_rollover(zone, instants, angles, zone_id)
    print(
        f"Midnight rollover in {zone_id} ({source}): expected absolute angles "
        f"{[round(value, ANGLE_REPORT_DECIMALS) for value in expected]}, measured "
        f"{[round(angle, ANGLE_REPORT_DECIMALS) for angle in angles]}, residuals "
        f"{[round(residual, ANGLE_REPORT_DECIMALS) for residual in residuals]}"
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

    if not device.send_debug_clock_broadcast(
        device_layer.DEBUG_CLOCK_RESET_EXTRAS, device_layer.DEBUG_CLOCK_RESET_MESSAGE
    ):
        failures.append("Post-midnight clock reset was not confirmed by the service log")


def phase_total_pss_growth(
    device: device_layer.AdbDevice, max_pss_growth_kb: int, results: list[tuple[str, str]], failures: list[str]
) -> None:
    """Sample total PSS ten seconds apart and compare the growth against the supplied budget."""
    print("\n--- Phase 7: Total PSS Growth ---")
    if not ensure_screen_on(device, failures):
        return
    if not device.send_debug_clock_broadcast(
        device_layer.DEBUG_CLOCK_RESET_EXTRAS, device_layer.DEBUG_CLOCK_RESET_MESSAGE
    ):
        failures.append("PSS baseline reset was not confirmed by the service log")
        return
    time.sleep(0.5)

    mem_before = device.run_adb(["shell", "dumpsys", "meminfo", device_layer.PACKAGE_NAME]).decode("utf-8")
    time.sleep(PSS_SAMPLE_SECONDS)
    mem_after = device.run_adb(["shell", "dumpsys", "meminfo", device_layer.PACKAGE_NAME]).decode("utf-8")

    pss_before = extract_total_pss_kb(mem_before)
    pss_after = extract_total_pss_kb(mem_after)
    pss_result = evaluate_pss_growth(pss_before, pss_after, max_pss_growth_kb)
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
                        f"{pss_before} -> {pss_after} kB over {PSS_SAMPLE_SECONDS:.0f}s "
                        f"(growth {pss_growth:+d} kB; budget {max_pss_growth_kb} kB); not battery or CPU evidence"
                    ),
                )
            )


def phase_renderer_log_scan(
    device: device_layer.AdbDevice, run_start_marker: str, results: list[tuple[str, str]], failures: list[str]
) -> bool:
    """Scan the run's renderer log lines and append the outcome to the results."""
    print("\n--- Phase 8: Renderer Log Scan ---")
    log_scan_inconclusive = False
    try:
        log_check = device.run_adb(
            [
                "logcat",
                "-d",
                "-T",
                run_start_marker,
                "-s",
                f"{device_layer.SERVICE_LOG_TAG}:W",
                "DialRenderer:W",
            ]
        ).decode("utf-8")
        warnings = matching_renderer_warnings(log_check)
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
        failures.append(f"Renderer log scan could not be collected: {device_layer.error_detail(error)}")
        results.append(("renderer log scan", f"Failed to collect: {device_layer.error_detail(error)}"))
        print(f"Renderer log scan failed: {device_layer.error_detail(error)}", file=sys.stderr)
    return log_scan_inconclusive


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the harness's command-line interface."""
    parser = argparse.ArgumentParser(description="Astronomical Clock Wallpaper Device Checks (#6)")
    parser.add_argument("-s", "--serial", help="Target ADB serial", default=None)
    parser.add_argument(
        "--max-pss-growth-kb",
        type=non_negative_int,
        required=True,
        help="Agreed maximum total-PSS growth over the 10-second sample, in kB",
    )
    return parser


def read_device_baseline(requested_serial: str | None) -> DeviceBaseline:
    """Inspect the device before any mutation, exiting when a precondition cannot be read."""
    serial = device_layer.select_target_serial(requested_serial)
    print(f"=== Starting Device Checks on target: {serial} ===")
    device = device_layer.AdbDevice(serial)
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
            f"ERROR: Could not inspect the device before making changes: {device_layer.error_detail(error)}",
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

    return DeviceBaseline(
        serial=serial,
        run_start_marker=run_start_marker,
        physical_size=physical_size,
        size_override=size_override,
        screen_was_on=screen_was_on,
        night_mode=night_mode,
        wallpaper_pid=pid_start,
    )


def print_check_report(results: list[tuple[str, str]]) -> None:
    """Print the results table a device report is assembled from."""
    print("\n=======================================================")
    print("              PHYSICAL-DEVICE CHECK REPORT             ")
    print("=======================================================")
    print("| Date | Check | Observed |")
    print("| --- | --- | --- |")
    today = time.strftime("%Y-%m-%d")
    for check, observed in results:
        print(f"| {today} | {check} | {observed} |")
    print("=======================================================\n")


def finish_run(results: list[tuple[str, str]], failures: list[str], *, log_scan_inconclusive: bool) -> None:
    """Print the report and exit non-zero when any check failed."""
    print_check_report(results)
    if failures:
        print("DEVICE CHECK FAILURES:", file=sys.stderr)
        for failure in failures:
            print(f"  - FAIL: {failure}", file=sys.stderr)
        sys.exit(1)
    if log_scan_inconclusive:
        print("Configured checks passed; renderer log scan was inconclusive.")
    else:
        print("ALL CONFIGURED DEVICE CHECKS PASSED.")


def main() -> None:
    """Inspect the device, run every check phase, restore the device, and report."""
    args = build_argument_parser().parse_args()
    baseline = read_device_baseline(args.serial)
    device = device_layer.AdbDevice(baseline.serial)
    recreate_size = device_layer.choose_recreate_size(baseline.physical_size, baseline.size_override)

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
        else:
            print("Skipping phases 1-7: environment setup was not confirmed.", file=sys.stderr)

    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        failures.append(f"Run aborted during {current_phase}: {device_layer.error_detail(error)}")
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

    finish_run(results, failures, log_scan_inconclusive=log_scan_inconclusive)


if __name__ == "__main__":
    main()
