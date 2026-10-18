# SPDX-License-Identifier: Apache-2.0
"""Evaluation rules, baseline state, rollover geometry, and producer-window checks for qualification."""

import argparse
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Final
from zoneinfo import ZoneInfo

from host import contract
from host.device import frame, probes, trace

REBIND_POLL_COUNT: Final = 10

REBIND_POLL_SECONDS: Final = 0.5

PSS_SAMPLE_SECONDS: Final = 10.0

# Decimal places for printed angles and residuals. This is a presentation setting: the tolerance
# comparison uses the raw residual, since rounding first would widen the stated boundary.
ANGLE_REPORT_DECIMALS: Final = 3

# Screen-off CPU evidence: sample the wallpaper process's tick counter across a measured window and
# bound its CPU time against a budget. The display is confirmed off only at the window's endpoints:
# nothing polls it inside the window, so a display that wakes and sleeps again mid-window would pass
# both endpoint checks. That continuous in-window evidence is deferred to
# https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125. One tick on the device clock
# is the smallest observable unit, so a delta of D ticks cannot exceed D+1 ticks of CPU time: the
# budget decision compares that upper bound, (D+1)/CLK_TCK, rather than the exact-looking D/CLK_TCK.
# A clock whose single tick is itself wider than the budget can never show the sample within budget,
# so the sample is refused instead of passed.
SCREEN_OFF_SETTLE_SECONDS: Final = 1.0

SCREEN_OFF_SAMPLE_SECONDS: Final = 4.0

# The producer windows below bracket the hidden one on both sides: a control taken before and after,
# each on the visible home screen, is what makes a zero inside the hidden window a measurement
# rather than an empty trace. Durations are bounded so no single gather holds the shared device for
# long, and the 60 s hidden window is sampled every 2 s across its whole width.
PRODUCER_CONTROL_SECONDS: Final = 20.0

PRODUCER_HIDDEN_SECONDS: Final = 60.0

# Named once because the hidden window is described in three places: the trace failure, the recorded
# row and the summary line, and they have to name the same window.
PRODUCER_HIDDEN_DESCRIPTION: Final = "hidden, screen off"

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
        if len(parts) >= probes.MIN_SPLIT_FIELDS and parts[0] == "TOTAL" and parts[1].isdigit():
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
        frame.size_restore_command(size_override),
    ]
    if initial_night_mode is not None:
        commands.insert(2, ["shell", "cmd", "uimode", "night", initial_night_mode])
    commands.append(["shell", "input", "keyevent", "KEYCODE_HOME"])
    return commands


def verify_restored_state(
    device: probes.AdbDevice,
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
        print(f"WARNING: could not verify restored device state: {probes.error_detail(error)}", file=sys.stderr)
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


def read_safe_pid(device: probes.AdbDevice) -> int | None:
    """Return the wallpaper PID, returning None on probe failure."""
    try:
        return device.get_wallpaper_pid()
    except (subprocess.SubprocessError, OSError):
        return None


def confirm_pid_unchanged(
    device: probes.AdbDevice, expected_pid: int | None, failures: list[str], *, description: str
) -> int | None:
    """Re-read the wallpaper PID and fail unless it still matches, returning the fresh reading."""
    pid = read_safe_pid(device)
    if pid is None or pid != expected_pid:
        failures.append(
            f"Wallpaper PID was not confirmed unchanged across the {description} "
            f"(before={expected_pid}, after={pid}); the measurement is not attributable to one process"
        )
    return pid


def confirm_bracketed_pid(device: probes.AdbDevice, pid_before: int | None, failures: list[str]) -> int | None:
    """
    Confirm the wallpaper is still the process the opening control bracketed, or abandon the window.

    Returns the pid to trace, or None when the window must be abandoned. `confirm_pid_unchanged`
    returns the fresh reading even when it does not match, and the trace is filtered to whatever pid
    it is handed. Adopting a replacement would therefore trace the new process and then compare the
    post-window reading against that same replacement, so the mismatch would satisfy its own check
    and a zero for a process the opening control never bracketed would be recorded. The window is
    abandoned — waking the device — rather than traced against a process that is not that one.

    The failure is worded here rather than by `confirm_pid_unchanged`: that message ends "the
    measurement is not attributable to one process", and at this point there is no measurement to be
    attributable, because the window is abandoned before it is traced. Its shared description would
    also be the same literal the post-window check reports, so two failures about different spans
    would read as one.
    """
    pid = read_safe_pid(device)
    if pid is None or pid != pid_before:
        failures.append(
            f"The wallpaper process is not confirmed to be the one the opening control bracketed "
            f"(before={pid_before}, after={pid}); the hidden producer window was abandoned rather "
            "than traced against a process it never bracketed"
        )
        if not device.wake_screen():
            failures.append("Screen wake request failed after the hidden window was abandoned")
        return None
    return pid


def read_safe_cpu_ticks(device: probes.AdbDevice, pid: int | None) -> int | None:
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
    """
    Build the descriptive observation record for the screen-off / wake phase.

    The CPU figure is the same one-tick upper bound the budget decision compares, so the number a
    reader sees is the one the pass or failure turned on.
    """
    if wallpaper_visible is False:
        visibility_text = "wallpaper reported hidden (mVisible=false)"
    elif wallpaper_visible is True:
        visibility_text = "wallpaper reported visible (mVisible=true)"
    else:
        visibility_text = "wallpaper visibility was unreadable"

    if ticks_delta is not None and clock_ticks is not None and window_seconds:
        # A delta of D whole ticks cannot exceed D+1 ticks of CPU time, so the reported figure is
        # that upper bound, marked with "<". This mirrors evaluate_screen_off_cpu's comparison.
        bound_seconds = (ticks_delta + 1) / clock_ticks
        cpu_text = (
            f"{ticks_delta} CPU ticks over the measured {window_seconds:.2f}s screen-off window at "
            f"{clock_ticks} ticks/s (<{bound_seconds:.3f}s CPU, <{bound_seconds / window_seconds * 100:.2f}% "
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
    """
    Return (tick delta, failure message) for one endpoint-bracketed screen-off sample window.

    A delta of D ticks cannot exceed D+1 ticks of CPU, so the decision compares that bound. A clock
    whose single tick is wider than the budget can never establish it, and both comparisons are
    strict: one tick equal to the budget still bounds the true CPU below it.
    """
    if ticks_start is None or ticks_end is None:
        return None, "Failed to sample wallpaper CPU ticks during confirmed screen-off state"
    if ticks_end < ticks_start:
        return None, f"CPU ticks went backward during screen-off: {ticks_start} -> {ticks_end}"
    if clock_ticks is None:
        return None, "Could not read the device clock-tick rate (getconf CLK_TCK); CPU time is unbounded"
    ticks_delta = ticks_end - ticks_start
    tick_seconds = 1 / clock_ticks
    if tick_seconds > SCREEN_OFF_CPU_BUDGET_SECONDS:
        return None, (
            f"The device clock ticks at {clock_ticks}/s, so one {tick_seconds:.3f}s tick is wider than the "
            f"{SCREEN_OFF_CPU_BUDGET_SECONDS:.2f}s budget and this sample cannot establish it"
        )
    cpu_bound_seconds = (ticks_delta + 1) / clock_ticks
    if cpu_bound_seconds > SCREEN_OFF_CPU_BUDGET_SECONDS:
        return ticks_delta, (
            f"Wallpaper process used up to {cpu_bound_seconds:.3f}s CPU while screen off "
            f"({ticks_delta} ticks over the measured {window_seconds:.2f}s window "
            f"at {clock_ticks} ticks/s); budget {SCREEN_OFF_CPU_BUDGET_SECONDS:.2f}s"
        )
    return ticks_delta, None


def resolve_rollover_zone(device: probes.AdbDevice) -> tuple[str, str] | None:
    """
    Resolve the zone the service renders with: the saved site's, else the device's.

    A `ProbeError` from the saved-site read propagates: only a successful read returning none means
    "no saved record", where the device timezone is the fallback.
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
    return [
        frame.civil_hand_angle_deg(local.hour * 3600 + local.minute * 60 + local.second)
        for local in (datetime.fromisoformat(instant).astimezone(zone) for instant in instants)
    ]


def evaluate_rollover(
    zone: ZoneInfo, instants: list[str], angles: Sequence[float], zone_id: str
) -> tuple[list[float], list[float], list[str]]:
    """Return (expected absolute angles, circular residuals, problems) for one civil-midnight sweep."""
    expected = expected_rollover_angles_deg(zone, instants)
    residuals = [
        frame.signed_circular_difference_deg(angle, want) for angle, want in zip(angles, expected, strict=True)
    ]
    problems = [
        # Compare the raw residual: rounding to ANGLE_REPORT_DECIMALS first would accept a residual
        # just above the boundary (0.5004° rounds to 0.500°), widening the stated 0.5° tolerance.
        f"Midnight rollover at {instant} (zone {zone_id}) hand angle residual {residual:+.3f}° "
        f"exceeds the {contract.ANGLE_TOLERANCE_DEG}° tolerance"
        for instant, residual in zip(instants, residuals, strict=True)
        if abs(residual) > contract.ANGLE_TOLERANCE_DEG
    ]
    for instant, (previous_expected, expected_angle), (previous_angle, angle) in zip(
        instants[1:], pairwise(expected), pairwise(angles), strict=True
    ):
        # Only gaps wider than the probe jitter can order adjacent samples; the 20-second pair is a bracket.
        if expected_angle - previous_expected > contract.ANGLE_TOLERANCE_DEG and angle < previous_angle:
            problems.append(
                f"Midnight rollover did not advance monotonically at {instant} "
                f"({previous_angle:.3f}° -> {angle:.3f}°) in zone {zone_id}"
            )
    return expected, residuals, problems


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


def print_check_report(results: Sequence[tuple[str, str]]) -> None:
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


def finish_run(results: Sequence[tuple[str, str]], failures: list[str], *, log_scan_inconclusive: bool) -> None:
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


def evaluate_producer_control(window: trace.ProducerWindow | None, failures: list[str], *, description: str) -> bool:
    """Fail unless a visible control window produced frames with the display on throughout."""
    if window is None:
        return False
    if window.frame_count == 0:
        failures.append(
            f"The {description} produced no producer frames, so the tracer is not evidence and a zero "
            "recorded beside it would be an unread counter rather than a measured zero"
        )
        return False
    # The control validates the display reader too: it is the only check in the direction the hidden
    # claim rests on before that window. The sleep gate only confirms an off reading, which a reader
    # stuck on False would also satisfy, so invalid display evidence refuses the window.
    if not window.display_on_at_every_sample:
        failures.append(f"The {description} display was not on for every sample taken inside it")
        return False
    return True


def producer_window_summary(window: trace.ProducerWindow) -> str:
    """
    Format the one line of evidence recorded for, and printed about, a producer window.

    The label is read off the samples: "mixed" is used only when both an on and an off sample were
    seen, so an all-unreadable window is never called mixed.
    """
    states = window.display_states
    on_samples = sum(1 for state in states if state is True)
    off_samples = sum(1 for state in states if state is False)
    if on_samples and off_samples:
        display = "mixed"
    elif on_samples:
        display = "on at every readable sample"
    elif off_samples:
        display = "off at every readable sample"
    else:
        display = "unreadable at every sample"
    return (
        f"{window.frame_count} producer frames over {window.window_seconds:.0f} s "
        f"({window.trace_lines} trace lines; display sampled {len(states)} times: {display}, "
        f"{window.display_unknown_samples} unreadable)"
    )


# The six inputs all reach the one call site that records and fails the check; a carrier type would
# not make it clearer, so the argument count is the honest shape.
def measure_producer_window(  # noqa: PLR0913
    device: probes.AdbDevice,
    pid: int | None,
    seconds: float,
    results: list[tuple[str, str]],
    failures: list[str],
    *,
    description: str,
    announce: bool = True,
) -> trace.ProducerWindow | None:
    """
    Trace one bounded producer window, recording its evidence or failing the check.

    `announce` is False for a window whose validity is not known until a later check: the evidence is
    recorded either way, but printing it is left to the caller so a window that is then abandoned is
    not put on the console as a measurement.
    """
    if pid is None:
        failures.append(f"No wallpaper process to trace for the {description}")
        return None
    try:
        window = device.capture_producer_window(pid, seconds=seconds)
    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        failures.append(f"The {description} producer trace failed: {probes.error_detail(error)}")
        return None
    summary = producer_window_summary(window)
    results.append((f"producer trace, {description}", summary))
    if announce:
        print(f"{description}: {summary}")
    return window


def evaluate_closing_control(window: trace.ProducerWindow | None, failures: list[str]) -> None:
    """
    Fail unless the closing control both produced frames and was on screen throughout.

    The frames carry the tracer's claim and the display state makes it the visible control, so it is
    judged on both, as the opening control is.
    """
    if window is None:
        return
    if window.frame_count == 0:
        # The observation, not a cause: a recreated process yields the same empty window with a
        # working tracer.
        failures.append(
            "The visible control window after the hidden one produced no producer frames on the pid "
            "traced after the wake, so the tracer is not confirmed to have been working across the "
            "hidden window"
        )
    if not window.display_on_at_every_sample:
        failures.append(
            "The visible control window after the hidden one was not on for every sample taken inside "
            "it, so it does not confirm a visible control after the hidden window"
        )


def accept_hidden_window(window: trace.ProducerWindow | None, failures: list[str]) -> None:
    """
    Announce and judge a hidden window whose pid check held.

    Printing waits for this point, because a line about a window the pid check then abandons could
    not be retracted.
    """
    if window is None:
        return
    print(f"{PRODUCER_HIDDEN_DESCRIPTION}: {producer_window_summary(window)}")
    evaluate_hidden_window(window, failures)


def evaluate_hidden_window(window: trace.ProducerWindow | None, failures: list[str]) -> None:
    """Fail unless the hidden window was screen-off for its whole width and produced no frames."""
    if window is None:
        return
    if not window.display_off_at_every_sample:
        failures.append(
            "The display was not off for every sample inside the hidden producer window, so its "
            "frame count does not describe a screen-off window"
        )
        # The budget is not applied: the count is not attributable to a screen-off window, so the
        # failure above is the whole verdict.
        return
    if window.frame_count != 0:
        failures.append(
            f"{window.frame_count} producer frames were rendered while the engine was hidden and the "
            "display was off; the budget is zero"
        )
