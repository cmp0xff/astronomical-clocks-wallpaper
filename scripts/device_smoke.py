#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Automated physical-device smoke test harness for Astronomical Clock Wallpaper.

Exercises live wallpaper on an attached Android device via ADB:
- Virtual time travel (+30 minutes) via DEBUG_SET_TIME broadcast
- 24-hour hand angular advance verification (7.5 degrees per 30 minutes)
- Surface recreation (wm size), confirmed by reading the override back
- A verified debug-clock reset and a renderer log scan, which reports an empty scan as inconclusive
  rather than as a clean log

The shared device layer owns the ADB primitives, the dial/frame analysis, and the target device
identity; this module keeps the smoke scenario, its restore policy, and its report.
"""

import argparse
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

import device_layer


@dataclass(frozen=True)
class SmokeBaseline:
    """The device state the run found, used to size and later restore every mutation."""

    serial: str
    log_start: str
    physical_size: str | None
    size_override: str | None
    screen_was_on: bool | None


@dataclass(frozen=True)
class SurfaceRecreation:
    """The applied override, phase restore verification, and hand angle after a verified restore."""

    override_was_active: bool
    restore_was_verified: bool
    hand_angle: float | None


@dataclass(frozen=True)
class AdvanceMeasurement:
    """The civil hand's measured advance across one debug-clock broadcast."""

    delta_deg: float
    residual_deg: float


@dataclass(frozen=True)
class SmokeOutcome:
    """The measured results of one run, assembled into both the failure list and the report."""

    initial_reset_confirmed: bool
    measurement: AdvanceMeasurement | None
    recreation: SurfaceRecreation
    warnings: Sequence[str]
    log_collection_error: str | None = None


def verify_restored_state(
    device: device_layer.AdbDevice, size_override: str | None, *, screen_was_on: bool | None
) -> bool:
    """Re-read the mutated settings and report whether each returned to what the run found."""
    try:
        restored_physical_size, restored_size_override = device.read_display_size()
        restored_screen_on = device.read_screen_on() if screen_was_on is not None else None
        keyguard_locked = device.read_keyguard_locked()
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
    if restored_screen_on != screen_was_on:
        restored = False
        print("WARNING: screen state did not return to its initial state", file=sys.stderr)
    if keyguard_locked is not False:
        restored = False
        print("WARNING: device was not confirmed unlocked after the run", file=sys.stderr)
    return restored


def restore_device(device: device_layer.AdbDevice, size_override: str | None, *, screen_was_on: bool | None) -> bool:
    """Best-effort reset of every setting the run changes, never raising."""
    restored = True
    if not device.run_restore_command(device_layer.size_restore_command(size_override)):
        restored = False
    if not device.confirm_virtual_clock_reset():
        restored = False
    # Last, after the clock reset and display restore, so a failure among the earlier commands still attempts it.
    if screen_was_on is False and not device.sleep_screen():
        restored = False
    if screen_was_on is None:
        restored = False
        print(
            "WARNING: initial screen state was unreadable; the screen cannot be reported as restored",
            file=sys.stderr,
        )
    if not verify_restored_state(device, size_override, screen_was_on=screen_was_on):
        restored = False
    return restored


def step_reset_clock_and_show_home(device: device_layer.AdbDevice) -> bool:
    """Wake the screen, dismiss the keyguard, show home, and confirm the debug clock reset."""
    print("Waking the screen and showing the home screen...")
    if not device.wake_screen():
        print("ERROR: screen wake was not confirmed; skipping the virtual-clock reset.", file=sys.stderr)
        return False
    device.dismiss_keyguard()
    device.show_home()
    time.sleep(1.0)
    print("Resetting virtual clock...")
    confirmed = device.confirm_virtual_clock_reset()
    time.sleep(0.5)
    return confirmed


def step_time_travel(device: device_layer.AdbDevice) -> AdvanceMeasurement | None:
    """Capture the baseline hand, advance the debug clock by a confirmed 30 minutes, and measure it."""
    # Capture baseline frame t0
    print("Capturing baseline frame t0...")
    w0, h0, px0 = device.capture_frame()
    angle0 = device_layer.detect_hand_angle(w0, h0, px0)
    if angle0 is not None:
        print(f"Baseline hand angle at t0: {angle0:.3f}°")
    else:
        print("Warning: Could not isolate hand pixels at t0 (wallpaper might be obstructed)")

    # Advance virtual time by +30 minutes
    print("Advancing virtual time +30 minutes via debug broadcast...")
    offset_message = "Debug clock offset set to 1800000ms"
    if not device.send_debug_clock_broadcast(["--el", "offset_minutes", "30"], offset_message):
        print("ERROR: +30m time offset was not confirmed by the service log", file=sys.stderr)
        return None

    # Capture frame t1 at +30 minutes
    print("Capturing frame t1 at +30m...")
    w1, h1, px1 = device.capture_frame()
    angle1 = device_layer.detect_hand_angle(w1, h1, px1)
    if angle1 is not None:
        print(f"Hand angle at t1 (+30m): {angle1:.3f}°")
    if angle0 is None or angle1 is None:
        return None

    delta = (angle1 - angle0) % device_layer.FULL_TURN_DEG
    residual = delta - device_layer.EXPECTED_ADVANCE_30M_DEG
    print(
        f"Observed angular advance: {delta:.3f}° "
        f"(expected: {device_layer.EXPECTED_ADVANCE_30M_DEG:.3f}°, residual: {residual:+.3f}°)"
    )
    return AdvanceMeasurement(delta_deg=delta, residual_deg=residual)


def step_recreate_surface(
    device: device_layer.AdbDevice, size_override: str | None, recreate_size: str
) -> SurfaceRecreation:
    """Override the display size, restore it, and report whether it took effect and what was drawn."""
    print("Testing surface recreation...")
    device.run_adb(["shell", "wm", "size", recreate_size])
    time.sleep(0.5)
    _, active_override = device.read_display_size()
    device.run_adb(device_layer.size_restore_command(size_override))
    time.sleep(0.5)
    physical, restored_override = device.read_display_size()
    if physical is None or restored_override != size_override:
        return SurfaceRecreation(
            override_was_active=active_override == recreate_size,
            restore_was_verified=False,
            hand_angle=None,
        )
    w2, h2, px2 = device.capture_frame()
    return SurfaceRecreation(
        override_was_active=active_override == recreate_size,
        restore_was_verified=True,
        hand_angle=device_layer.detect_hand_angle(w2, h2, px2),
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the harness's command-line interface."""
    parser = argparse.ArgumentParser(description="Automated live wallpaper device smoke test")
    parser.add_argument("-s", "--serial", help="ADB device serial", default=None)
    return parser


def read_baseline(requested_serial: str | None) -> SmokeBaseline:
    """Resolve the target and record the device state the run must put back afterwards."""
    target_serial = device_layer.select_target_serial(requested_serial)
    print(f"Targeting ADB device: {target_serial}")
    device = device_layer.AdbDevice(target_serial)

    log_start = device.run_adb(["shell", "date +'%m-%d %H:%M:%S.000'"]).decode().strip()
    print(f"Logcat start marker: {log_start}")

    physical_size, size_override = device.read_display_size()
    if physical_size is None:
        print(
            "ERROR: could not read the physical display size; refusing to change device state.",
            file=sys.stderr,
        )
        sys.exit(1)
    if size_override is not None:
        print(
            f"WARNING: display-size override {size_override} is already active; "
            "it will be restored as found, not reset",
            file=sys.stderr,
        )

    screen_was_on = device.read_screen_on()
    if screen_was_on is None:
        print(
            "ERROR: could not read the initial screen state; refusing to wake the device.",
            file=sys.stderr,
        )
        sys.exit(1)

    keyguard_locked = device.read_keyguard_locked()
    if keyguard_locked is None:
        print(
            "ERROR: could not read the keyguard state; refusing to run on an unconfirmed lock state.",
            file=sys.stderr,
        )
        sys.exit(1)
    if keyguard_locked:
        print("ERROR: device is locked; unlock it before running this smoke test.", file=sys.stderr)
        sys.exit(1)

    return SmokeBaseline(
        serial=target_serial,
        log_start=log_start,
        physical_size=physical_size,
        size_override=size_override,
        screen_was_on=screen_was_on,
    )


def scan_renderer_log(device: device_layer.AdbDevice, log_start: str) -> list[str]:
    """Return this run's renderer warning records, isolated by the device-time marker."""
    logs = device.run_adb(
        ["logcat", "-d", "-T", log_start, "-s", f"{device_layer.SERVICE_LOG_TAG}:W", "DialRenderer:W"]
    ).decode("utf-8")
    return [line for line in logs.splitlines() if line.strip() and not line.startswith("---------")]


def collect_failures(outcome: SmokeOutcome, *, restored: bool) -> list[str]:
    """Build the failure list from the measured outcome of the run."""
    failures = []
    if not outcome.initial_reset_confirmed:
        failures.append(
            "initial device setup (wake and virtual-clock reset) was not confirmed; "
            "time travel and surface recreation were skipped"
        )
    else:
        measurement = outcome.measurement
        recreation = outcome.recreation
        if measurement is None:
            failures.append("hand not found; wallpaper must be visible and unobstructed")
        elif abs(measurement.residual_deg) > device_layer.ANGLE_TOLERANCE_DEG:
            failures.append(
                f"hand advance residual {measurement.residual_deg:+.3f}° exceeds ±{device_layer.ANGLE_TOLERANCE_DEG}°"
            )
        if not recreation.override_was_active:
            failures.append("display-size override was not active as requested")
        if not recreation.restore_was_verified:
            failures.append("display size was not verified after surface recreation restore")
        elif recreation.hand_angle is None:
            failures.append("hand not drawn after surface recreation")
    if outcome.log_collection_error is not None:
        failures.append(f"renderer log collection failed: {outcome.log_collection_error}")
    if outcome.warnings:
        failures.append(f"{len(outcome.warnings)} renderer warning(s) in logcat")
    if not restored:
        failures.append("device state was not fully restored; see the restore warnings above")
    return failures


def print_results(baseline: SmokeBaseline, recreate_size: str, outcome: SmokeOutcome) -> None:
    """Print the measured-results table a device report is assembled from."""
    print("\n--- Measured results (record in docs/testing/reports/ with APK/source attribution) ---")
    today = time.strftime("%Y-%m-%d")
    print("| Date | Check | Observed |")
    print("| --- | --- | --- |")
    if not outcome.initial_reset_confirmed:
        skip_note = "SKIPPED: initial device setup (wake and virtual-clock reset) was not confirmed"
        print(f"| {today} | virtual time travel (+30m) | {skip_note} |")
        print(f"| {today} | surface recreation | {skip_note} |")
    else:
        measurement = outcome.measurement
        recreation = outcome.recreation
        if measurement is not None:
            print(
                f"| {today} | virtual time travel (+30m) | "
                f"Hand advanced {measurement.delta_deg:.3f}° against {device_layer.EXPECTED_ADVANCE_30M_DEG:.3f}° "
                f"expected, residual {measurement.residual_deg:+.3f}°; "
                "broadcast confirmed by the service log |"
            )
        recreated = "drawn" if recreation.hand_angle is not None else "NOT found"
        applied = "active as requested" if recreation.override_was_active else "NOT active as requested"
        verified = "verified" if recreation.restore_was_verified else "NOT verified"
        restore_desc = f"`wm size {baseline.size_override}`" if baseline.size_override else "`wm size reset`"
        effective_size = baseline.size_override or baseline.physical_size or "unknown"
        print(
            f"| {today} | surface recreation | effective {effective_size}, `wm size {recreate_size}` "
            f"({applied}) then {restore_desc} ({verified}); hand {recreated} afterwards |"
        )
    if outcome.log_collection_error is not None:
        log_row = f"FAILED: renderer log collection failed: {outcome.log_collection_error}"
    elif outcome.warnings:
        log_row = f"{len(outcome.warnings)} warning(s) or error(s) from either renderer tag in logcat"
    else:
        log_row = "Inconclusive: no matching warning records; rendering was not verified"
    print(f"| {today} | renderer log | {log_row} |")
    print("---------------------------------------------------------------------------\n")


def finish_run(failures: Sequence[str], *, log_scan_inconclusive: bool) -> None:
    """Exit non-zero when any check failed, or report the pass and what it did not establish."""
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        sys.exit(1)
    if log_scan_inconclusive:
        print("Smoke test passed; renderer log scan was inconclusive.")
    else:
        print("Smoke test passed.")


def main() -> None:
    """Reset the clock, advance it, recreate the surface, restore the device, and report."""
    args = build_argument_parser().parse_args()
    baseline = read_baseline(args.serial)
    device = device_layer.AdbDevice(baseline.serial)
    recreate_size = device_layer.choose_recreate_size(baseline.physical_size, baseline.size_override)

    measurement: AdvanceMeasurement | None = None
    recreation = SurfaceRecreation(override_was_active=False, restore_was_verified=False, hand_angle=None)
    restored = True
    initial_reset_confirmed = False

    try:
        initial_reset_confirmed = step_reset_clock_and_show_home(device)
        if initial_reset_confirmed:
            measurement = step_time_travel(device)
            recreation = step_recreate_surface(device, baseline.size_override, recreate_size)
        else:
            print(
                "ERROR: initial device setup (wake and virtual-clock reset) was not confirmed; "
                "skipping time travel and surface recreation checks.",
                file=sys.stderr,
            )
    finally:
        print("Restoring virtual clock, display size, and screen state...")
        restored = restore_device(device, baseline.size_override, screen_was_on=baseline.screen_was_on)

    log_collection_error = None
    try:
        warnings = scan_renderer_log(device, baseline.log_start)
    except (subprocess.SubprocessError, OSError) as error:
        warnings = []
        log_collection_error = device_layer.error_detail(error)
    if log_collection_error is not None:
        print(f"Renderer log collection failed: {log_collection_error}", file=sys.stderr)
    elif warnings:
        print(f"Renderer warnings check: {len(warnings)} unexpected warning(s)")
    else:
        print("Renderer log scan inconclusive: no matching warning records.")

    outcome = SmokeOutcome(
        initial_reset_confirmed=initial_reset_confirmed,
        measurement=measurement,
        recreation=recreation,
        warnings=warnings,
        log_collection_error=log_collection_error,
    )
    failures = collect_failures(outcome, restored=restored)
    print_results(baseline, recreate_size, outcome)
    finish_run(failures, log_scan_inconclusive=log_collection_error is None and not warnings)


if __name__ == "__main__":
    main()
