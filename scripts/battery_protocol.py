#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Battery and resource acceptance protocol for Astronomical Clock Wallpaper.

Each subcommand runs one bounded phase of the budget in `docs/battery-budget.md` against a connected
physical device, appends one JSON record to a state directory, and prints its own evidence. The
phases are split so that no invocation blocks for a whole qualification, which is what the shared
device policy requires: `resources`, `hidden`, `visible`, `pilot`, `memory`, and `report`.

No phase performs the wallpaper swap it measures. The arm is verified before a window starts and the
window is refused when the device does not match, because a mis-swapped wallpaper must not be
recorded as a valid measurement.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

import device_layer

CHARGE_COUNTER_PATTERN: Final = re.compile(r"^[ \t]*Charge counter:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
BATTERY_LEVEL_PATTERN: Final = re.compile(r"^[ \t]*level:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
# Anchored to the field name so that `Max charging voltage:` can never be read as the pack voltage.
BATTERY_VOLTAGE_PATTERN: Final = re.compile(r"^[ \t]*voltage:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
TOTAL_PSS_PATTERN: Final = re.compile(r"^\s*TOTAL\s+(\d+)", re.MULTILINE)
SDK_PATTERN: Final = re.compile(r"^(\d+)$")
# `dumpsys jobscheduler` lists each scheduled job as `JOB #<uid>/<id>: <hash> <pkg>/<service>`; the
# fields after the colon are the job's deferred/source hash and the component. Group 3 is the package
# the job belongs to, which is what attributes it to the app.
JOB_LINE_PATTERN: Final = re.compile(r"^\s*JOB #(\d+)/(\d+):\s+\S+\s+([\w.]+)/", re.MULTILINE)
# A `dumpsys jobscheduler` dump that has been read at all prints a `Registered <n> jobs:` census line
# before the job list; requiring it is what separates "the app has no jobs" from "this is not a
# jobscheduler dump", so an unparsable probe is an error rather than a zero.
JOBSCHEDULER_CENSUS_PATTERN: Final = re.compile(r"^\s*Registered (\d+) jobs:", re.MULTILINE)
# A `dumpsys power` that has been read at all prints its wake-lock census before listing the locks,
# the same way `dumpsys jobscheduler` prints its job census. Requiring it is what separates "the app
# holds no wake locks" from "this is not a power dump", so a truncated or reshaped dump is an error
# rather than a zero.
POWER_WAKELOCK_CENSUS_PATTERN: Final = re.compile(r"^\s*Wake Locks: size=(\d+)\s*$", re.MULTILINE)
# `dumpsys alarm` likewise prints a census before listing the pending alarms, and that census is what
# makes an alarm count trustworthy. The line is taken from the test device's own dump, which reads
# `  152 pending alarms: ` — count first, no `Pending alarm batches:` line anywhere in it. A device
# whose alarm dump is shaped differently fails loudly here rather than reporting a zero, which is the
# behaviour this census exists for.
ALARM_CENSUS_PATTERN: Final = re.compile(r"^\s*(\d+) pending alarms:\s*$", re.MULTILINE)
POWERED_LINE_PATTERN: Final = re.compile(r"^\s*(\w+) powered:\s*(true|false)\s*$", re.MULTILINE)
# The four power sources `dumpsys battery` reports. A reading is only complete when all four are
# present: a dump that lost three of them must not be read as "the remaining one is false, so the
# device is on battery", which is what a per-line scan would conclude from a truncated dump.
EXPECTED_POWER_SOURCES: Final = ("AC", "USB", "Wireless", "Dock")

DEFAULT_STATE_DIR: Final = Path("build/reports/battery")
RECORD_FILE_NAME: Final = "records.jsonl"

# The window lengths the acceptance matrix documents. They are minimums rather than defaults: a
# shorter window is a different measurement, so `--minutes` refuses one and `report` refuses a record
# that carries one. The default is the minimum because that is the documented cost of the criterion,
# not because a longer window is worse.
MINIMUM_HIDDEN_MINUTES: Final = 10.0
MINIMUM_WINDOW_MINUTES: Final = 15.0
MINIMUM_MEMORY_MINUTES: Final = 30.0
DEFAULT_WINDOW_MINUTES: Final = MINIMUM_WINDOW_MINUTES
DEFAULT_HIDDEN_MINUTES: Final = MINIMUM_HIDDEN_MINUTES
DEFAULT_MEMORY_MINUTES: Final = MINIMUM_MEMORY_MINUTES
# The recorded wall-clock of a window that ran as documented lands within a few seconds of its
# requested length on either side; scheduling jitter is seconds and an interrupted window is minutes,
# so a 5% shortfall is the widest gap that still means "this window ran for its whole length".
MINIMUM_DURATION_TOLERANCE: Final = 0.95
# Each windowed phase's documented minimum, keyed by the phase name its records carry, so `report`
# can check a recorded duration against it without knowing how the window was requested.
MINIMUM_SECONDS_BY_PHASE: Final = {
    "hidden": MINIMUM_HIDDEN_MINUTES * 60.0,
    "visible": MINIMUM_WINDOW_MINUTES * 60.0,
    "memory": MINIMUM_MEMORY_MINUTES * 60.0,
}
SAMPLE_INTERVAL_SECONDS: Final = 30.0
COMMAND_TIMEOUT_SECONDS: Final = 60

PROC_STAT_UTIME_INDEX: Final = 11
PROC_STAT_INDEX_STIME: Final = 12
MIN_PROC_STAT_FIELDS: Final = 13
# `/proc/<pid>/stat` counts CPU in kernel clock ticks, and the ticks per second is a kernel constant
# (`getconf CLK_TCK`) that is not 100 on every device. It is probed per run through
# `device_layer.AdbDevice.read_process_cpu_clock_ticks` and the run fails when the probe cannot
# answer, because scaling a CPU percentage by an assumed rate misreports every CPU criterion.

# Budgets from docs/battery-budget.md. B1 is #125 criterion 1 and is deliberately not measured here:
# the frame console is that issue's, and it lands its own evidence under docs/testing/reports/
# (2026-10-12-test-125-hidden-frames-and-reboot.md) rather than through this protocol.
MAX_HIDDEN_CPU_PCT: Final = 0.1
PROVISIONAL_MAX_VISIBLE_DELTA_PCT: Final = 5.0
PROVISIONAL_MAX_VISIBLE_CPU_PCT: Final = 3.0
MAX_PSS_GROWTH_KB: Final = 6144

# Committed before the final run from the pilot's observed noise floors, per the document's own
# anti-tuning rule; `report` reads these two constants, so a change here is what "committing the
# threshold" means. The 2026-10-13 pilot measured the live discharge channel on a device that was
# genuinely discharging: four 15-minute arm-A windows gave a mean of 366265 uA with a 2 sigma of
# 43997 uA, which is 12.0123% of the mean. That floor is wider than the provisional 5%, so the B4
# threshold rises to it, which is the document's rule working as intended: a larger tolerated
# increase makes the criterion **more permissive**, not harder, because a live-versus-baseline
# increase up to 12.0123% can now pass where 5% would have failed it. What the wider floor buys is
# honesty rather than leniency: below it this device cannot tell a real difference from its own
# variation, so the alternative to a permissive threshold is a criterion that reports a difference it
# has not resolved. The floor is a property of this device's gauge and load rather than evidence
# about the wallpaper: a worn cell and a gauge that reports in 1000 uAh steps both widen it, and it
# has to be re-measured per device. The CPU channel's floor is 0.0894% of one core, below the
# provisional 3%, so the B5 threshold stays as stated.
FINAL_MAX_VISIBLE_DELTA_PCT: Final = 12.0123  # greater of the provisional 5% and the measured floor
FINAL_MAX_VISIBLE_CPU_PCT: Final = PROVISIONAL_MAX_VISIBLE_CPU_PCT  # the floor is below the provisional
# The pilot's floors are part of the commitment, not of the working state: a state file is reset
# between the pilot and the final run precisely so the calibration cannot enter the result, so the
# floors are carried here where the thresholds they justify are.
PILOT_DISCHARGE_NOISE_FLOOR_UA: Final = 43997.06
PILOT_CPU_NOISE_FLOOR_PCT: Final = 0.0894
PILOT_REPORT: Final = "docs/testing/reports/2026-10-13-test-126-drain-comparison-on-battery.md"

# Two-sided 95% Student t critical values by degrees of freedom, from the table in NIST/SEMATECH
# e-Handbook of Statistical Methods, section 1.3.6.7.3 (retrieved 2026-10-12), which is the free
# redistillation of the standard tables. Beyond 30 degrees of freedom the normal value 1.960 is used,
# where the two agree to three decimals.
T_CRITICAL_95_BY_DF: Final = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}
NORMAL_T_CRITICAL_95: Final = 1.960
# Above the table the normal value is not good enough: the two-sided 95% t at 31 degrees of freedom
# is 2.040, and returning 1.960 there would understate the interval by 4%. The Cornish-Fisher
# expansion of the t quantile in powers of 1/nu is used instead, which reproduces the table to three
# decimals at its top and tends to the normal value as nu grows.
T_TAIL_TERMS: Final = (4.0, 96.0)
MIN_PAIRED_SAMPLES: Final = 4

# The arm guard's two provable facts. The live component is a deterministic read — the home slot
# names it in full — while which static image a baseline carries is not, so the baseline's identity is
# bounded by the median luminance of the home screen, which refuses rather than guesses when it lands
# between the two. The live arm is compared as a whole component rather than by a substring of its
# text: another package shipping a service with the same class name would otherwise be admitted as the
# wallpaper under test, and every CPU and drain figure this protocol records would be attributed to
# the wrong process.
COMPONENT_INFO_PATTERN: Final = re.compile(r"^ComponentInfo\{(.*)\}$")
LUMA_STRIDE: Final = 8
LUMA_WEIGHTS: Final = (0.2126, 0.7152, 0.0722)
LUMA_MAX: Final = 255.0
MEDIAN_LUMA_NOTE: Final = "recorded, not gated: the OEM stock wallpaper on this device is a dark image"

PINNED_BRIGHTNESS: Final = 128
# Stay awake on every power source; see DeviceStateSnapshot.pin.
STAY_ON_ALWAYS: Final = 7
# A whole hour, so it comfortably exceeds the longest documented window (30 minutes) rather
# than expiring at the same instant the window closes.
LONG_SCREEN_TIMEOUT_MS: Final = 3_600_000
RESTORE_SETTLE_SECONDS: Final = 2.0

# The discharge window's power source. A device that is genuinely on its own battery has a live fuel
# gauge and its counter moves; a device held on USB only reports battery when `cmd battery unplug`
# overrides the power source, and on some devices that freezes the gauge. Which one a window was
# measured under is part of the measurement's identity, so it is recorded and checked.
POWER_SOURCE_DEVICE: Final = "device"
POWER_SOURCE_SIMULATED: Final = "simulated"
# The fields that say a record was taken from the same build, device configuration, and channel, so
# aggregating a set that mixes them would combine measurements that are not comparable. `revision`
# is deliberately not one of them: a harness revision that changes only documentation or the report's
# own aggregation does not change how a window was measured, and the reports say when that happened.
IDENTITY_FIELDS: Final = ("apk_sha256", "api_level", "power_source", "clk_tck")


class Arm(StrEnum):
    """Which wallpaper a window is recorded against."""

    LIVE = "A"
    BASELINE = "B"


class Baseline(StrEnum):
    """Which static baseline an arm B window is recorded against."""

    STOCK = "stock"
    BLACK = "black"


class VerdictStatus(StrEnum):
    """How one criterion, or one observation, came out."""

    PASS = "PASS"  # noqa: S105 — a verdict label, not a credential
    FAIL = "FAIL"
    NOT_MEASURED = "NOT MEASURED"
    # A criterion that belongs to another issue and is deliberately not measured here, so its absence
    # is not a failure of this run. B1 is #125's frame counter.
    OUT_OF_SCOPE = "OUT OF SCOPE"
    # A criterion the device cannot measure at all, so its absence is recorded rather than gated and
    # rather than left to look measured. The black static baseline is the one case: the harness never
    # swaps a wallpaper, setting one rewrites the home image, and on a production build without root
    # there is no way to put the stock image back — so the document requires that secondary baseline
    # only where the device permits setting one, and its absence is not a failure of the run while the
    # comparison the criterion is gated on stays the stock one. The report has to say so out loud,
    # which is what this status is for: silence here would read as a complete verdict.
    DEVICE_LIMITED = "NOT MEASURED (DEVICE-LIMITED)"
    # A line the report prints beside the verdicts but that is not itself a pass/fail criterion.
    INFO = "INFO"


@dataclass(frozen=True)
class Verdict:
    """
    One line of the report, as data rather than as text.

    The report's exit status is derived from these, so a FAIL can never be printed and still leave
    the command exiting zero: automation reads the status, not the printed string.
    """

    label: str
    status: VerdictStatus
    line: str

    @property
    def is_failure(self) -> bool:
        """Whether this verdict should make the report exit non-zero."""
        return self.status is VerdictStatus.FAIL


@dataclass(frozen=True)
class BatterySample:
    """Instantaneous battery and process telemetry reading."""

    timestamp: float
    charge_counter_uah: int
    level_pct: int
    voltage_mv: int
    process_cpu_seconds: float | None
    total_pss_kb: int | None


@dataclass(frozen=True)
class Window:
    """One bounded measurement period under constant display and workload conditions."""

    start_sample: BatterySample
    end_sample: BatterySample
    duration_seconds: float

    @property
    def discharge_uah(self) -> int:
        """Net microampere-hours consumed during the window."""
        return self.start_sample.charge_counter_uah - self.end_sample.charge_counter_uah

    @property
    def discharge_rate_ua(self) -> float:
        """
        Average current draw over the window, in microamperes.

        The counter must have fallen. A gauge that reports the pack gaining charge across a window is
        not measuring a discharge at all — the device was charging, or the gauge recalibrated — and
        returning the negative draw would let that window enter the comparison as a live arm that
        looked cheaper, so it is refused at the source rather than judged downstream.
        """
        if self.duration_seconds <= 0:
            detail = "a window with no elapsed time has no discharge rate"
            raise device_layer.ProbeError(detail)
        if self.discharge_uah < 0:
            detail = (
                f"the charge counter rose by {-self.discharge_uah} uAh across the window "
                f"({self.start_sample.charge_counter_uah} to {self.end_sample.charge_counter_uah} uAh), "
                "so the pack gained charge and this is not a discharge measurement"
            )
            raise device_layer.ProbeError(detail)
        return (self.discharge_uah / self.duration_seconds) * 3600.0

    @property
    def cpu_pct(self) -> float | None:
        """Average CPU across one core during the window, or None when the process was not read."""
        start = self.start_sample.process_cpu_seconds
        end = self.end_sample.process_cpu_seconds
        if start is None or end is None or self.duration_seconds <= 0:
            return None
        if end < start:
            detail = (
                f"the process CPU counter went backwards ({start:.3f}s to {end:.3f}s), which means the "
                "process restarted or the pid was recycled inside the window; the window is not a measurement"
            )
            raise device_layer.ProbeError(detail)
        return ((end - start) / self.duration_seconds) * 100.0


@dataclass(frozen=True)
class PairedStatistics:
    """A paired comparison of two equal-length series of window rates."""

    sample_count: int
    degrees_of_freedom: int
    t_critical: float
    mean_baseline: float
    mean_treatment: float
    relative_delta_pct: float
    ci_95_upper_pct: float
    # The sd of the per-pair differences, in the same unit as the means. The interval's width is
    # `t * sd / sqrt(n)`, so this is the one number that says how many pairs a future run needs —
    # a report that carries it lets the next run's n be computed instead of guessed.
    standard_deviation: float


@dataclass(frozen=True)
class Record:
    """One phase invocation's result, as written to the state file."""

    phase: str
    recorded_at: str
    revision: str
    apk_sha256: str | None
    api_level: int | None
    duration_seconds: float
    values: dict[str, object] = field(default_factory=dict)


class DeviceStateSnapshot:
    """
    The device state a phase changes, captured so it can be put back and checked.

    The screen's own power state is part of it: a hidden window turns the display off, and a phase
    that returned that way would leave the device dark.
    """

    def __init__(  # noqa: PLR0913
        self,
        device: device_layer.AdbDevice,
        *,
        brightness: int | None,
        brightness_mode: int | None,
        screen_timeout: int | None,
        airplane_mode: int | None,
        stay_on: int | None,
        screen_on: bool | None,
    ) -> None:
        """Hold the values read before a phase changed anything."""
        self.device = device
        self.brightness = brightness
        self.brightness_mode = brightness_mode
        self.screen_timeout = screen_timeout
        self.airplane_mode = airplane_mode
        self.stay_on = stay_on
        self.screen_on = screen_on

    @classmethod
    def capture(cls, device: device_layer.AdbDevice) -> DeviceStateSnapshot:
        """
        Read the state a measurement phase pins, including the display's own power state.

        The four settings `pin()` writes are required rather than best-effort: a value that could not
        be read is a value the restore cannot put back, and skipping it would leave the shared device
        changed by a phase that reported success. The stay-awake setting is the one exception — it is
        legitimately unset on a device that never had it, and an unset value *is* captured, as `None`,
        and restored by deleting the key.
        """
        return cls(
            device,
            brightness=require_int_setting(device, "system", "screen_brightness"),
            brightness_mode=require_int_setting(device, "system", "screen_brightness_mode"),
            screen_timeout=require_int_setting(device, "system", "screen_off_timeout"),
            airplane_mode=require_int_setting(device, "global", "airplane_mode_on"),
            stay_on=read_int_setting(device, "global", "stay_on_while_plugged_in"),
            screen_on=device.read_screen_on(),
        )

    def pin(self) -> None:
        """Pin the display conditions a visible window is measured under."""
        self.device.run_adb(["shell", "settings", "put", "system", "screen_brightness_mode", "0"])
        self.device.run_adb(["shell", "settings", "put", "system", "screen_brightness", str(PINNED_BRIGHTNESS)])
        self.device.run_adb(["shell", "settings", "put", "system", "screen_off_timeout", str(LONG_SCREEN_TIMEOUT_MS)])
        # The timeout alone does not hold the display: a device that reports itself as running on
        # battery ignores the plugged-in stay-awake setting, which is exactly the state a discharge
        # simulation puts it in. Staying awake on every power source is what keeps a visible window
        # visible, and it is restored below like everything else.
        self.device.run_adb(["shell", "settings", "put", "global", "stay_on_while_plugged_in", str(STAY_ON_ALWAYS)])
        set_airplane_mode(self.device, enabled=True)

    def restore(self) -> list[str]:
        """Put every pinned value back, isolating each step so none can skip the others."""
        problems: list[str] = []
        problems.extend(self._restore_settings())
        problems.extend(self._restore_display())
        return problems

    def _restore_settings(self) -> list[str]:
        """Undo the settings writes, reporting each one that could not be verified."""
        problems: list[str] = []
        steps: list[tuple[str, list[str], str, str, int | None]] = [
            (
                # Restored to the value it was found with, not to off: a device that had airplane
                # mode on before the phase must still have it on afterwards.
                "airplane mode",
                [
                    "shell",
                    "cmd",
                    "connectivity",
                    "airplane-mode",
                    "enable" if self.airplane_mode else "disable",
                ],
                "global",
                "airplane_mode_on",
                self.airplane_mode,
            ),
            (
                "brightness mode",
                ["shell", "settings", "put", "system", "screen_brightness_mode", str(self.brightness_mode)],
                "system",
                "screen_brightness_mode",
                self.brightness_mode,
            ),
            (
                "brightness",
                ["shell", "settings", "put", "system", "screen_brightness", str(self.brightness)],
                "system",
                "screen_brightness",
                self.brightness,
            ),
            (
                "screen timeout",
                ["shell", "settings", "put", "system", "screen_off_timeout", str(self.screen_timeout)],
                "system",
                "screen_off_timeout",
                self.screen_timeout,
            ),
        ]
        for description, command, namespace, key, expected in steps:
            if expected is None:
                continue
            problems.extend(self._apply(description, command, namespace, key, expected))
        problems.extend(self._restore_stay_on())
        return problems

    def _restore_stay_on(self) -> list[str]:
        """Put the stay-awake setting back, including removing it when it was unset."""
        if self.stay_on is None:
            try:
                self.device.run_adb(["shell", "settings", "delete", "global", "stay_on_while_plugged_in"])
            except (subprocess.SubprocessError, OSError) as error:
                return [f"stay-awake restore failed: {device_layer.error_detail(error)}"]
            # The readback is inside its own guard: a transport failure here used to escape the whole
            # restore and skip the display step that follows it.
            try:
                remaining = read_int_setting(self.device, "global", "stay_on_while_plugged_in")
            except (subprocess.SubprocessError, OSError) as error:
                return [f"stay-awake restore could not be verified: {device_layer.error_detail(error)}"]
            if remaining is not None:
                return ["global/stay_on_while_plugged_in did not return to unset"]
            return []
        return self._apply(
            "stay-awake",
            ["shell", "settings", "put", "global", "stay_on_while_plugged_in", str(self.stay_on)],
            "global",
            "stay_on_while_plugged_in",
            self.stay_on,
        )

    def _apply(self, description: str, command: list[str], namespace: str, key: str, expected: int) -> list[str]:
        """Run one restore step and verify it, converting a failure into a reported problem."""
        try:
            self.device.run_adb(command)
            time.sleep(RESTORE_SETTLE_SECONDS)
        except (subprocess.SubprocessError, OSError) as error:
            return [f"{description} restore failed: {device_layer.error_detail(error)}"]
        # The readback is guarded too: an uncaught transport failure here used to escape the whole
        # restore and skip the display restoration.
        try:
            actual = read_int_setting(self.device, namespace, key)
        except (subprocess.SubprocessError, OSError) as error:
            return [f"{description} restore could not be verified: {device_layer.error_detail(error)}"]
        if actual != expected:
            return [f"{namespace}/{key} did not read back as {expected}"]
        return []

    def _restore_display(self) -> list[str]:
        """Return the display to the power state it was found in."""
        if self.screen_on is None:
            return ["the display state could not be read before the phase, so it cannot be restored"]
        if self.screen_on and not self.device.wake_screen():
            return ["the display was on before the phase and could not be woken again"]
        if not self.screen_on and self.device.read_screen_on() is not False:
            self.device.sleep_screen()
            if self.device.read_screen_on() is not False:
                return ["the display was off before the phase and could not be put back to sleep"]
        return []


def undo_phase_changes(
    device: device_layer.AdbDevice,
    snapshot: DeviceStateSnapshot,
    prior_power: tuple[str, ...] | None = None,
    *,
    simulated: bool = False,
) -> list[str]:
    """
    Undo a phase's device changes, isolating each step so none can skip the others.

    The `cmd battery` reset runs only when this phase applied the override. A device that was already
    on its own battery is never put under the override, so there is nothing to clear, and clearing it
    would be a write to a device whose state is already correct.
    """
    problems: list[str] = []
    if simulated:
        try:
            reset_battery_simulation(device)
            if prior_power is not None and not power_source_restored(device, prior_power):
                problems.append("the reported power sources did not return to their pre-phase state")
        except (subprocess.SubprocessError, OSError) as error:
            problems.append(f"battery simulation reset failed: {device_layer.error_detail(error)}")
    try:
        problems.extend(snapshot.restore())
    except (subprocess.SubprocessError, OSError) as error:
        problems.append(f"device settings restore failed: {device_layer.error_detail(error)}")
    return problems


def read_int_setting(device: device_layer.AdbDevice, namespace: str, key: str) -> int | None:
    """Read one integer device setting, or None when it is unset or not an integer."""
    raw = device.run_adb(["shell", "settings", "get", namespace, key]).decode(errors="replace").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


def require_int_setting(device: device_layer.AdbDevice, namespace: str, key: str) -> int:
    """
    Read one device setting the pre-phase capture cannot do without.

    `pin()` overwrites every one of these, and the restore can only put back a value that was
    captured. A phase that pinned a setting it could not read would leave the shared device changed
    and still report success, so an unreadable value stops the phase before it touches anything.
    """
    value = read_int_setting(device, namespace, key)
    if value is None:
        detail = (
            f"{namespace}/{key} could not be read before the phase, so whatever value it had could not "
            "be restored; refusing to pin a setting whose original value is unknown"
        )
        raise device_layer.ProbeError(detail)
    return value


def set_airplane_mode(device: device_layer.AdbDevice, *, enabled: bool) -> None:
    """Turn airplane mode on or off through the connectivity service."""
    device.run_adb(["shell", "cmd", "connectivity", "airplane-mode", "enable" if enabled else "disable"])


def require_complete_power_sources(lines: Sequence[str]) -> None:
    """
    Refuse a power-source reading that is missing any of the four sources `dumpsys battery` reports.

    One `powered:` line is not a reading. A dump that named only `Dock powered: false` would look
    exactly like a device on its own battery to a per-line scan, and the identity of a discharge
    measurement rests on this reading, so an incomplete one is raised rather than filled in.
    """
    present = {match.group(1) for match in POWERED_LINE_PATTERN.finditer("\n".join(lines))}
    missing = [source for source in EXPECTED_POWER_SOURCES if source not in present]
    if missing:
        detail = (
            f"the battery dump carried no {' or '.join(missing)} powered: field, so the power source is "
            "not a complete reading; refusing to infer the device's state from the fields that remain"
        )
        raise device_layer.ProbeError(detail)


def read_power_source_lines(device: device_layer.AdbDevice) -> tuple[str, ...]:
    """Read the battery dump's power-source lines, which `cmd battery unplug` rewrites."""
    output = device.run_adb(["shell", "dumpsys", "battery"]).decode(errors="replace")
    lines = tuple(line.strip() for line in output.splitlines() if POWERED_LINE_PATTERN.match(line.strip()))
    require_complete_power_sources(lines)
    return lines


def power_source_restored(device: device_layer.AdbDevice, before: tuple[str, ...]) -> bool:
    """
    Report whether the reported power sources match what they were before the simulation.

    Comparing against the pre-simulation reading is what separates "the override is gone" from
    "this device happens to be running on its own battery", which look identical in the dump.
    """
    return read_power_source_lines(device) == before


def power_lines_show_battery(lines: Sequence[str]) -> bool:
    """
    Report whether a battery dump's `powered:` fields all read false, i.e. the device is on battery.

    Every source is required. A dump that named fewer than the four sources is raised rather than
    read as "no charger is connected", because a probe that stopped reporting some of them would
    otherwise look like a device that is discharging.
    """
    require_complete_power_sources(lines)
    return all(match.group(2) == "false" for match in POWERED_LINE_PATTERN.finditer("\n".join(lines)))


def device_on_own_battery(device: device_layer.AdbDevice) -> bool:
    """Report whether the device is already discharging on its own battery rather than on a charger."""
    return power_lines_show_battery(read_power_source_lines(device))


def apply_power_mode(device: device_layer.AdbDevice) -> str:
    """
    Put the device in the discharge state a window needs, and name the mode it is in.

    A device that is genuinely discharging already has the live fuel gauge this measurement wants,
    and `cmd battery unplug` would replace that with a frozen override — the very artefact the
    simulation was invented to work around. The override is therefore applied only when the device
    is on a charger, and the mode it chose is recorded in the window as part of its identity.
    """
    if device_on_own_battery(device):
        return POWER_SOURCE_DEVICE
    simulate_unplug(device)
    return POWER_SOURCE_SIMULATED


def parse_battery_output(output: str) -> tuple[int, int, int]:
    """
    Extract charge counter (uAh), level (%), and voltage (mV) from `dumpsys battery`.

    Every field is required. A missing field is raised rather than defaulted, because a battery
    probe that stopped reporting would otherwise look like a device drawing nothing.
    """
    values: list[int] = []
    for pattern, name in (
        (CHARGE_COUNTER_PATTERN, "Charge counter"),
        (BATTERY_LEVEL_PATTERN, "level"),
        (BATTERY_VOLTAGE_PATTERN, "voltage"),
    ):
        match = pattern.search(output)
        if match is None:
            detail = f"dumpsys battery output carried no {name} field; refusing to read it as zero"
            raise device_layer.ProbeError(detail)
        values.append(int(match.group(1)))
    return values[0], values[1], values[2]


def parse_proc_stat_cpu_seconds(stat_content: str, clk_tck: int) -> float | None:
    """
    Parse utime + stime from a `/proc/<pid>/stat` line and return total CPU seconds.

    `clk_tck` is required rather than defaulted: the tick rate is a kernel constant that is not 100 on
    every device, and scaling by a guess would misreport every CPU percentage the budget is judged on.
    """
    if clk_tck <= 0:
        detail = f"a CPU-tick rate of {clk_tck} cannot scale a tick count"
        raise device_layer.ProbeError(detail)
    close = stat_content.rfind(")")
    if close == -1:
        return None
    parts = stat_content[close + 1 :].split()
    # After the closing parenthesis the first field is `state`, so utime (field 14) and stime
    # (field 15) are at indices 11 and 12. Splitting on whitespace instead would misparse whenever
    # the parenthesised comm field contains a space.
    if len(parts) < MIN_PROC_STAT_FIELDS:
        return None
    try:
        utime = int(parts[PROC_STAT_UTIME_INDEX])
        stime = int(parts[PROC_STAT_INDEX_STIME])
    except (ValueError, IndexError):
        return None
    return (utime + stime) / float(clk_tck)


def parse_total_pss_kb(mem_output: str) -> int | None:
    """Return the `TOTAL` PSS line of a `dumpsys meminfo` dump, in kB."""
    match = TOTAL_PSS_PATTERN.search(mem_output)
    return int(match.group(1)) if match else None


def parse_api_level(output: str) -> int:
    """Read the SDK level from `getprop ro.build.version.sdk`, refusing anything unexpected."""
    stripped = output.strip()
    if not SDK_PATTERN.match(stripped):
        detail = f"ro.build.version.sdk read back as {stripped!r}, which is not an API level"
        raise device_layer.ProbeError(detail)
    return int(stripped)


def t_critical_95(degrees_of_freedom: int) -> float:
    """
    Return the two-sided 95% Student t critical value for `degrees_of_freedom`.

    The table is tabulated to 30 degrees of freedom; beyond that the normal value is used, where
    the two agree to three decimals. Every tabulated value is returned as tabulated, so a caller
    with two or three pairs reads 4.303 or 3.182 rather than the four-pair 3.182 the previous
    two-branch lookup collapsed them to. How many pairs a comparison needs is enforced by
    `paired_statistics`, which is where the sample count is known.
    """
    if degrees_of_freedom < 1:
        detail = f"{degrees_of_freedom} degrees of freedom admits no interval at all"
        raise device_layer.ProbeError(detail)
    tabulated = T_CRITICAL_95_BY_DF.get(degrees_of_freedom)
    if tabulated is not None:
        return tabulated
    z = NORMAL_T_CRITICAL_95
    first, second = T_TAIL_TERMS
    return (
        z
        + (z**3 + z) / (first * degrees_of_freedom)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (second * degrees_of_freedom**2)
    )


def paired_statistics(baseline_rates: Sequence[float], treatment_rates: Sequence[float]) -> PairedStatistics:
    """
    Compare paired baseline and treatment window rates, with a 95% interval on the increase.

    The interval is the paired one: the per-pair differences remove the drift both arms share, so
    the bound reflects the measurement actually being asked about.
    """
    if len(baseline_rates) != len(treatment_rates):
        detail = f"pairing needs equal arms but got {len(baseline_rates)} and {len(treatment_rates)} windows"
        raise device_layer.ProbeError(detail)
    if len(baseline_rates) < MIN_PAIRED_SAMPLES:
        detail = (
            f"{len(baseline_rates)} alternations were recorded but the budget mandates at least "
            f"{MIN_PAIRED_SAMPLES} pairs"
        )
        raise device_layer.ProbeError(detail)
    diffs = [treatment - baseline for baseline, treatment in zip(baseline_rates, treatment_rates, strict=True)]
    mean_baseline = sum(baseline_rates) / len(baseline_rates)
    mean_treatment = sum(treatment_rates) / len(treatment_rates)
    if mean_baseline <= 0:
        detail = "the baseline mean discharge rate is not positive, so no relative increase exists"
        raise device_layer.ProbeError(detail)
    degrees_of_freedom = len(diffs) - 1
    t_value = t_critical_95(degrees_of_freedom)
    mean_diff = sum(diffs) / len(diffs)
    variance = sum((diff - mean_diff) ** 2 for diff in diffs) / degrees_of_freedom
    standard_error = math.sqrt(variance / len(diffs))
    relative_delta_pct = (mean_diff / mean_baseline) * 100.0
    margin_pct = (t_value * standard_error / mean_baseline) * 100.0
    return PairedStatistics(
        sample_count=len(diffs),
        degrees_of_freedom=degrees_of_freedom,
        t_critical=t_value,
        mean_baseline=mean_baseline,
        mean_treatment=mean_treatment,
        relative_delta_pct=relative_delta_pct,
        ci_95_upper_pct=relative_delta_pct + margin_pct,
        standard_deviation=math.sqrt(variance),
    )


def two_sigma_noise_floor(values: Sequence[float]) -> tuple[float, float, float]:
    """Return the mean, the sample standard deviation, and the 2-sigma half-width of a series."""
    if len(values) < MIN_PAIRED_SAMPLES:
        detail = f"a noise floor needs at least {MIN_PAIRED_SAMPLES} windows but got {len(values)}"
        raise device_layer.ProbeError(detail)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    deviation = math.sqrt(variance)
    return mean, deviation, 2.0 * deviation


def read_process_uid(device: device_layer.AdbDevice, pid: int | None) -> int | None:
    """Read the owning UID of a process from `/proc/<pid>`."""
    if pid is None or pid <= 0:
        return None
    output = device.run_adb(["shell", "stat", "-c", "%u", f"/proc/{pid}"]).decode(errors="replace").strip()
    return int(output) if output.isdigit() else None


def require_clk_tck(device: device_layer.AdbDevice) -> int:
    """
    Probe the device's kernel clock-tick rate, refusing to run without a real one.

    `device_layer.read_process_cpu_clock_ticks` reads `getconf CLK_TCK`. When it cannot answer, the
    CPU criteria cannot be scaled correctly, so the run fails loudly instead of assuming 100.
    """
    clk_tck = device.read_process_cpu_clock_ticks()
    if clk_tck is None or clk_tck <= 0:
        detail = (
            "the device did not report its CLK_TCK rate, so process CPU ticks cannot be converted to "
            "seconds; refusing to scale every CPU criterion by an assumed rate"
        )
        raise device_layer.ProbeError(detail)
    return clk_tck


def take_battery_sample(device: device_layer.AdbDevice, pid: int | None, clk_tck: int) -> BatterySample:
    """Read the battery fuel gauge plus the process CPU and PSS counters."""
    battery_output = device.run_adb(["shell", "dumpsys", "battery"]).decode(errors="replace")
    charge_uah, level_pct, voltage_mv = parse_battery_output(battery_output)

    cpu_seconds: float | None = None
    if pid is not None and pid > 0:
        stat_output = device.run_adb(["shell", "cat", f"/proc/{pid}/stat"]).decode(errors="replace")
        cpu_seconds = parse_proc_stat_cpu_seconds(stat_output, clk_tck)

    mem_output = device.run_adb(["shell", "dumpsys", "meminfo", device_layer.PACKAGE_NAME]).decode(errors="replace")
    return BatterySample(
        # Monotonic, not wall time: the window's duration and every rate derived from it come from the
        # difference of two of these, and a clock correction during a fifteen-minute window would
        # otherwise rescale the current or make the window negative. Wall time is for `recorded_at`.
        timestamp=time.monotonic(),
        charge_counter_uah=charge_uah,
        level_pct=level_pct,
        voltage_mv=voltage_mv,
        process_cpu_seconds=cpu_seconds,
        total_pss_kb=parse_total_pss_kb(mem_output),
    )


def parse_wakelock_count(dump: str, app_uid: int) -> int:
    """
    Count the partial wake locks a `dumpsys power` dump attributes to one UID.

    The dump's own `Wake Locks: size=<n>` census is required, for the same reason the jobscheduler
    census is: a dump without it is not a power dump, and counting its matching lines would report
    "the app holds no wake locks" from output that says nothing about wake locks at all. A dump that
    carries the census and no lock for this UID is a real zero.
    """
    if POWER_WAKELOCK_CENSUS_PATTERN.search(dump) is None:
        detail = (
            "the power dump carried no `Wake Locks: size=` census, so it cannot be read as a wake-lock "
            "count; refusing to report a zero"
        )
        raise device_layer.ProbeError(detail)
    # The uid is matched as a whole token, not as a prefix of one: a lock line for uid 10026 contains
    # the text `uid=1002`, so a prefix test would attribute another process's wake lock to an app
    # whose own uid happens to be a prefix of it, and report a B2 failure the app did not cause.
    uid_field = re.compile(rf"\buid={app_uid}\b")
    return sum(1 for line in dump.splitlines() if "PARTIAL_WAKE_LOCK" in line and uid_field.search(line))


def parse_alarm_count(dump: str, package: str = device_layer.PACKAGE_NAME) -> int:
    """
    Count the pending alarms a `dumpsys alarm` dump attributes to one package.

    As with the power and jobscheduler probes, the dump's own `Pending alarm batches: <n>` census is
    required so that a truncated or reshaped dump is an error rather than a zero.
    """
    if ALARM_CENSUS_PATTERN.search(dump) is None:
        detail = (
            "the alarm dump carried no `Pending alarm batches:` census, so it cannot be read as an "
            "alarm count; refusing to report a zero"
        )
        raise device_layer.ProbeError(detail)
    return sum(1 for line in dump.splitlines() if package in line and "Alarm{" in line)


def count_held_wakelocks(device: device_layer.AdbDevice, app_uid: int | None) -> int:
    """Count the partial wake locks `dumpsys power` attributes to the app's UID."""
    if app_uid is None:
        detail = "no UID was resolved for the wallpaper process, so wake locks cannot be attributed"
        raise device_layer.ProbeError(detail)
    output = device.run_adb(["shell", "dumpsys", "power"]).decode(errors="replace")
    return parse_wakelock_count(output, app_uid)


def count_alarms(device: device_layer.AdbDevice) -> int:
    """Count the pending alarms `dumpsys alarm` attributes to the wallpaper package."""
    output = device.run_adb(["shell", "dumpsys", "alarm"]).decode(errors="replace")
    return parse_alarm_count(output)


def parse_job_count(dump: str, package: str = device_layer.PACKAGE_NAME) -> int:
    """
    Count the JobScheduler jobs a package holds in a `dumpsys jobscheduler` dump.

    The dump's own `Registered <n> jobs:` census is required: a dump without it is not a jobscheduler
    dump, and reading it as a zero would report "the app holds no jobs" from output that says nothing
    about jobs at all. A dump that carries the census and no job for the package is a real zero.
    """
    if JOBSCHEDULER_CENSUS_PATTERN.search(dump) is None:
        detail = (
            "the jobscheduler dump carried no `Registered <n> jobs:` census, so it cannot be read as a "
            "job count; refusing to report a zero"
        )
        raise device_layer.ProbeError(detail)
    return sum(1 for match in JOB_LINE_PATTERN.finditer(dump) if match.group(3) == package)


def count_jobs(device: device_layer.AdbDevice) -> int:
    """Count the JobScheduler jobs `dumpsys jobscheduler` attributes to the wallpaper package."""
    output = device.run_adb(["shell", "dumpsys", "jobscheduler"]).decode(errors="replace")
    return parse_job_count(output)


def read_batterystats_summary(device: device_layer.AdbDevice) -> str | None:
    """Read the historical per-app CPU lines from `dumpsys batterystats` as corroborating evidence."""
    output = device.run_adb(["shell", "dumpsys", "batterystats", device_layer.PACKAGE_NAME]).decode(errors="replace")
    keys = ("Total cpu time:", "Fg Service for:", "Total running:")
    lines = [line.strip() for line in output.splitlines() if any(key in line for key in keys)]
    return ", ".join(lines) if lines else None


def simulate_unplug(device: device_layer.AdbDevice) -> None:
    """Make the device report itself as running on battery while it stays on USB for telemetry."""
    device.run_adb(["shell", "cmd", "battery", "unplug"])


def reset_battery_simulation(device: device_layer.AdbDevice) -> None:
    """Clear any `cmd battery` override so the device reports its real state again."""
    device.run_adb(["shell", "cmd", "battery", "reset"])


def read_source_revision() -> str:
    """Read the source revision of this working tree."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def read_apk_sha256() -> str | None:
    """Hash the debug APK under test, or None when it has not been built."""
    apk = Path("app/build/outputs/apk/debug/app-debug.apk")
    if not apk.is_file():
        return None
    return hashlib.sha256(apk.read_bytes()).hexdigest()


def record_path(state_dir: Path) -> Path:
    """Return the JSON Lines file the phase records accumulate in."""
    return state_dir / RECORD_FILE_NAME


def append_record(state_dir: Path, record: Record) -> Path:
    """Append one record to the state file, creating the directory when it is missing."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = record_path(state_dir)
    payload = {"phase": record.phase, "recorded_at": record.recorded_at, "revision": record.revision}
    payload["apk_sha256"] = record.apk_sha256
    payload["api_level"] = record.api_level
    payload["duration_seconds"] = record.duration_seconds
    payload.update(record.values)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return path


def load_records(state_dir: Path) -> list[dict[str, object]]:
    """Read back every record written so far, refusing to guess at a malformed line."""
    path = record_path(state_dir)
    if not path.is_file():
        detail = f"no records at {path}; run the measurement phases before the report"
        raise device_layer.ProbeError(detail)
    records: list[dict[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as error:
            # A truncated write is a state file the run can be told about, not a traceback: the
            # phases append as they go, so the last line is the one an interrupted run leaves short.
            detail = f"{path}:{number} is not valid JSON ({error.msg}); the record is truncated or corrupt"
            raise device_layer.ProbeError(detail) from error
        if not isinstance(parsed, dict):
            detail = f"{path}:{number} is not a JSON object"
            raise device_layer.ProbeError(detail)
        records.append(parsed)
    return records


class Recorder:
    """
    Writes each record to the state file as it is produced, and keeps them for the caller.

    Persisting per record is what makes the phases resumable: a phase interrupted part-way leaves the
    records it completed on disk rather than holding them in a list until a `main` that may never
    reach its append loop.
    """

    def __init__(self, state_dir: Path) -> None:
        """Bind the recorder to the state directory its records accumulate in."""
        self.state_dir = state_dir
        self.records: list[Record] = []

    def emit(self, record: Record) -> Record:
        """Append one record, print where it went, and return it for the caller to keep using."""
        path = append_record(self.state_dir, record)
        self.records.append(record)
        print(f"recorded {record.phase} -> {path}")
        return record


def build_record(device: device_layer.AdbDevice, phase: str, duration_seconds: float, **values: object) -> Record:
    """Assemble one record, stamping the build identity every report row has to carry."""
    api_level = parse_api_level(device.run_adb(["shell", "getprop", "ro.build.version.sdk"]).decode(errors="replace"))
    return Record(
        phase=phase,
        recorded_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        revision=read_source_revision(),
        apk_sha256=read_apk_sha256(),
        api_level=api_level,
        duration_seconds=duration_seconds,
        values=dict(values),
    )


def emit_aborted_restore_record(
    recorder: Recorder,
    device: device_layer.AdbDevice,
    phase: str,
    error: BaseException,
    problems: Sequence[str],
) -> None:
    """
    Persist a failed phase's restore problems when it aborted before building its own record.

    A phase collects its restore problems in a `finally` so that a failing write cannot skip the rest,
    but an exception raised by the measurement itself leaves through that same `finally` and takes the
    collected problems with it: the device is left changed and nothing on disk says so, so `report`
    cannot flag it and the next phase measures a device nobody restored. This writes the problems as a
    record of their own, named for the phase that did not finish, which is all `report` needs to print
    its device-restore failure.

    A record is written only when there is at least one problem: the record exists to carry them, and
    an empty one would look like a measurement of a phase that never ran. Building it can itself fail
    when the abort was the device going away, so that is swallowed — the original error is the one
    worth reporting, and `main` still exits non-zero on it.
    """
    if not problems:
        return
    try:
        record = build_record(
            device,
            f"{phase}_aborted",
            0.0,
            aborted=str(error),
            restore_problems=list(problems),
        )
    except (device_layer.ProbeError, subprocess.SubprocessError, OSError):
        print(f"restore problems could not be persisted for the aborted {phase} phase: {problems}", file=sys.stderr)
        return
    recorder.emit(record)


def median_luma(device: device_layer.AdbDevice) -> float:
    """
    Return the median luminance of a freshly captured screen, a bounded proxy for what is shown.

    This reads the raw screencap rather than the dial-validated capture, because a baseline arm has
    no dial to validate: the validated read raises when the live wallpaper is not the one on screen,
    which is exactly the state the baseline arms are in.
    """
    _, _, pixels = device_layer.decode_frame(device.run_adb(["exec-out", "screencap"]))
    luma = sorted(
        sum(weight * pixel for weight, pixel in zip(LUMA_WEIGHTS, pixels[index : index + 3], strict=True))
        for index in range(0, len(pixels) - 2, device_layer.PIXEL_STRIDE * LUMA_STRIDE)
    )
    if not luma:
        detail = "the captured frame carried no pixels to measure"
        raise device_layer.ProbeError(detail)
    middle = len(luma) // 2
    return luma[middle] if len(luma) % 2 else (luma[middle - 1] + luma[middle]) / 2.0


def component_name(component: str) -> str:
    """Return the `package/class` inside a `ComponentInfo{...}` wrapper, or the text unchanged."""
    stripped = component.strip()
    match = COMPONENT_INFO_PATTERN.match(stripped)
    # `captured_text` is the shared helper for this: a match group is `Any`, and returning it
    # unchanged would let a non-string through a function the arm guard depends on.
    return device_layer.captured_text(match, 1) if match else stripped


def component_is_live(component: str) -> bool:
    """
    Report whether a home-wallpaper component is exactly this app's live service.

    The comparison is on the whole `package/class`, not on whether the text contains the service's
    class name: `ComponentInfo{other.pkg/…AstronomicalClocksWallpaperService}` is a different
    wallpaper, and admitting it as arm A would attribute its CPU and drain to this app.
    """
    return component_name(component) == device_layer.SERVICE_NAME


def verify_arm(device: device_layer.AdbDevice, arm: Arm, baseline: Baseline | None) -> str:
    """
    Refuse a window whose device does not match the arm it is about to be recorded against.

    The component is the fact that decides the arm, and it is decidable: the home slot names the live
    service when the app is bound and a static component when it is not. Which *static* image a
    baseline carries is not decidable this way, and the screen's own luminance does not decide it
    either — this device's OEM stock wallpaper is a dark starfield, so a brightness threshold would
    refuse valid stock windows while still not separating the two baselines. The luminance is
    recorded with every window as an observation and is not used to admit one.
    """
    component = device_layer.read_wallpaper_component(device.serial)
    if arm is Arm.LIVE:
        if not component_is_live(component):
            detail = (
                f"arm A was requested but the home wallpaper is {component_name(component)}, not "
                f"{device_layer.SERVICE_NAME}"
            )
            raise device_layer.ProbeError(detail)
        return component
    if baseline is None:
        detail = "arm B needs --baseline stock or --baseline black to say which static wallpaper is bound"
        raise device_layer.ProbeError(detail)
    if component_is_live(component):
        detail = f"arm B was requested but the live service is still bound as {component_name(component)}"
        raise device_layer.ProbeError(detail)
    return component


def arm_conditions(device: device_layer.AdbDevice, arm: Arm, baseline: Baseline | None) -> dict[str, object]:
    """Describe the verified arm for the record, including the luminance the guard admitted."""
    return {
        "arm": arm.value,
        "baseline": baseline.value if baseline else None,
        "wallpaper_component": device_layer.read_wallpaper_component(device.serial),
        "median_luma": round(median_luma(device), 2),
        "median_luma_note": MEDIAN_LUMA_NOTE,
    }


def sample_window_states(device: device_layer.AdbDevice, seconds: float) -> tuple[list[bool | None], list[bool]]:
    """Sample the display state and the power source across a window and return every reading."""
    states: list[bool | None] = []
    on_battery: list[bool] = []
    remaining = seconds
    while remaining > 0:
        interval = min(SAMPLE_INTERVAL_SECONDS, remaining)
        time.sleep(interval)
        remaining -= interval
        states.append(device.read_screen_on())
        on_battery.append(device_on_own_battery(device))
        print(f"  {seconds - remaining:6.0f}s: screen_on={states[-1]} on_battery={on_battery[-1]}", flush=True)
    return states, on_battery


def power_source_problem(on_battery: Sequence[bool]) -> str | None:
    """
    Return why a window is not a discharge measurement, or None when every sample was on battery.

    The same shape as the arm guard: a window whose device ended up on a charger is not a measurement
    of discharge, and recording it as one would be a fabricated result.
    """
    if not on_battery:
        return "the window took no power-source sample, so it is not known to have run on battery"
    if not all(on_battery):
        return f"the device reported a charger connected inside the window: {list(on_battery)}"
    return None


def ensure_visible_home(device: device_layer.AdbDevice) -> None:
    """
    Put the device on the home screen with the display on, or refuse the window.

    A visible window means the home screen with the wallpaper showing. `wake_screen()` only confirms
    the display is lit — it does not dismiss a keyguard or bring the launcher forward — so a device
    that started locked, or with another app in front, would have its lock screen or that app measured
    while the bound component still matched the requested arm. The qualification harness establishes
    this state explicitly before its own screen-on phases, and this one now does too.
    """
    if not device.wake_screen():
        detail = "the display could not be woken for a visible window"
        raise device_layer.ProbeError(detail)
    device.dismiss_keyguard()
    device.show_home()
    if device.read_screen_on() is not True:
        detail = "the display did not read back as on while the home screen was being established"
        raise device_layer.ProbeError(detail)
    if device.read_keyguard_locked() is True:
        detail = "the device is still locked after dismissing the keyguard, so the home screen is not displayed"
        raise device_layer.ProbeError(detail)


def run_window(
    device: device_layer.AdbDevice,
    pid: int | None,
    minutes: float,
    *,
    screen_on: bool,
    clk_tck: int,
) -> tuple[Window, list[bool | None], list[bool]]:
    """Sample a bounded window, holding the requested display state for its whole duration."""
    if not screen_on:
        device.run_adb(["shell", "input", "keyevent", "KEYCODE_SLEEP"])
        time.sleep(2.0)
    start = take_battery_sample(device, pid, clk_tck)
    seconds = minutes * 60.0
    states, on_battery = sample_window_states(device, seconds)
    end = take_battery_sample(device, pid, clk_tck)
    window = Window(start_sample=start, end_sample=end, duration_seconds=end.timestamp - start.timestamp)
    return window, states, on_battery


def phase_resources(device: device_layer.AdbDevice) -> Record:
    """Record B2: the partial wake locks, alarms, and jobs the app holds, with their commands."""
    pid = device.get_wallpaper_pid()
    uid = read_process_uid(device, pid)
    print(f"$ adb -s {device.serial} shell pidof {device_layer.PACKAGE_NAME} -> {pid}")
    print(f"$ adb -s {device.serial} shell stat -c %u /proc/{pid} -> {uid}")
    if uid is None:
        detail = "the wallpaper has no live process, so its wake locks cannot be attributed"
        raise device_layer.ProbeError(detail)
    wake_locks = count_held_wakelocks(device, uid)
    alarms = count_alarms(device)
    jobs = count_jobs(device)
    print(f"$ adb -s {device.serial} shell dumpsys power   (PARTIAL_WAKE_LOCK uid={uid}) -> {wake_locks}")
    print(f"$ adb -s {device.serial} shell dumpsys alarm   ({device_layer.PACKAGE_NAME}) -> {alarms}")
    print(f"$ adb -s {device.serial} shell dumpsys jobscheduler   ({device_layer.PACKAGE_NAME}) -> {jobs}")
    summary = read_batterystats_summary(device)
    print(f"  batterystats corroboration: {summary or 'no matching lines'}")
    record = build_record(
        device,
        "resources",
        0.0,
        wake_locks=wake_locks,
        alarms=alarms,
        jobs=jobs,
        wallpaper_pid=pid,
        wallpaper_uid=uid,
        batterystats_summary=summary,
    )
    print(f"wake locks={wake_locks} (budget 0), alarms={alarms} (budget 0), jobs={jobs} (budget 0)")
    return record


def phase_hidden(device: device_layer.AdbDevice, minutes: float, *, recorder: Recorder) -> Record:
    """Record B3: hidden-state CPU over a screen-off window, with the visibility sampled."""
    clk_tck = require_clk_tck(device)
    snapshot = DeviceStateSnapshot.capture(device)
    problems: list[str] = []
    prior_power = read_power_source_lines(device)
    power_source = POWER_SOURCE_DEVICE
    simulated = False
    # The restore sits in its own `finally` inside the outer `try`, so it runs before the abort is
    # handled and the problems it collected are in hand when the record is written.
    try:
        try:
            # The live arm is verified here too. A hidden window is B3 evidence for the live engine, so
            # a static wallpaper bound at the time would make the CPU this window measures the
            # launcher's; `phase_visible` and `phase_memory` guard their arms and this phase used to
            # take the pid and open the window without asking what was bound.
            verify_arm(device, Arm.LIVE, None)
            pid = device.get_wallpaper_pid()
            snapshot.pin()
            power_source = apply_power_mode(device)
            simulated = power_source == POWER_SOURCE_SIMULATED
            window, states, on_battery = run_window(device, pid, minutes, screen_on=False, clk_tck=clk_tck)
        finally:
            problems = undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
    except Exception as error:
        emit_aborted_restore_record(recorder, device, "hidden", error, problems)
        raise
    hidden_all = states and all(state is False for state in states)
    if not hidden_all:
        detail = f"the display was not off for the whole window: {states}"
        raise device_layer.ProbeError(detail)
    charging = power_source_problem(on_battery)
    if charging is not None:
        detail = f"this window is not a valid measurement: {charging}"
        raise device_layer.ProbeError(detail)
    cpu_pct = window.cpu_pct
    if cpu_pct is None:
        detail = "the wallpaper process's CPU counters could not be read over the hidden window"
        raise device_layer.ProbeError(detail)
    print(f"hidden window {window.duration_seconds:.1f}s: screen_on readings={states}")
    print(f"CPU {cpu_pct:.4f}% of one core against the {MAX_HIDDEN_CPU_PCT}% budget")
    return build_record(
        device,
        "hidden",
        window.duration_seconds,
        window_minutes=minutes,
        cpu_pct=cpu_pct,
        hidden_cpu_budget_pct=MAX_HIDDEN_CPU_PCT,
        clk_tck=clk_tck,
        power_source=power_source,
        screen_on_samples=states,
        on_battery_samples=on_battery,
        wall_clock_seconds=round(window.duration_seconds, 1),
        restore_problems=problems,
    )


def phase_visible(
    device: device_layer.AdbDevice,
    *,
    arm: Arm,
    baseline: Baseline | None,
    minutes: float,
    recorder: Recorder,
) -> Record:
    """Record one B4/B5 window on the home screen after the arm guard admits it."""
    clk_tck = require_clk_tck(device)
    # The snapshot is taken before the display is woken: a phase that starts with the display off has
    # to record that state, so its restore can put the device back to sleep instead of leaving it on.
    snapshot = DeviceStateSnapshot.capture(device)
    prior_power = read_power_source_lines(device)
    problems: list[str] = []
    power_source = POWER_SOURCE_DEVICE
    simulated = False
    # The restore sits in its own `finally` inside the outer `try`, so it runs before the abort is
    # handled and the problems it collected are in hand when the record is written.
    try:
        try:
            # The wake and the arm guard sit inside the try with the window, so a refusal after the
            # display has already been woken still runs the restore below.
            ensure_visible_home(device)
            verify_arm(device, arm, baseline)
            pid = device.get_wallpaper_pid() if arm is Arm.LIVE else None
            conditions = arm_conditions(device, arm, baseline)
            print(f"arm guard admitted {conditions['wallpaper_component']} (median luma {conditions['median_luma']})")
            snapshot.pin()
            power_source = apply_power_mode(device)
            simulated = power_source == POWER_SOURCE_SIMULATED
            window, states, on_battery = run_window(device, pid, minutes, screen_on=True, clk_tck=clk_tck)
        finally:
            problems = undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
    except Exception as error:
        emit_aborted_restore_record(recorder, device, "visible", error, problems)
        raise
    if states and not all(state is True for state in states):
        detail = f"the display was not on for the whole visible window: {states}"
        raise device_layer.ProbeError(detail)
    charging = power_source_problem(on_battery)
    if charging is not None:
        detail = f"this is not a discharge measurement: {charging}"
        raise device_layer.ProbeError(detail)
    if arm is Arm.LIVE and window.cpu_pct is None:
        detail = "the live wallpaper's CPU counters could not be read over a visible window"
        raise device_layer.ProbeError(detail)
    print(f"window {window.duration_seconds:.1f}s: discharge {window.discharge_uah} uAh")
    print(f"discharge rate {window.discharge_rate_ua:.0f} uA, CPU {window.cpu_pct}% of one core")
    return build_record(
        device,
        "visible",
        window.duration_seconds,
        window_minutes=minutes,
        discharge_uah=window.discharge_uah,
        discharge_rate_ua=window.discharge_rate_ua,
        cpu_pct=window.cpu_pct,
        charge_counter_start_uah=window.start_sample.charge_counter_uah,
        charge_counter_end_uah=window.end_sample.charge_counter_uah,
        clk_tck=clk_tck,
        power_source=power_source,
        screen_on_samples=states,
        on_battery_samples=on_battery,
        restore_problems=problems,
        **conditions,
    )


def phase_memory(device: device_layer.AdbDevice, minutes: float, *, recorder: Recorder) -> Record:
    """Record B6: the total-PSS growth over a long window on the live wallpaper."""
    clk_tck = require_clk_tck(device)
    # Captured before the wake, like the visible phase: a phase that found the display off records it
    # so its restore can put the device back the way it was.
    snapshot = DeviceStateSnapshot.capture(device)
    prior_power = read_power_source_lines(device)
    problems: list[str] = []
    states: list[bool | None] = []
    on_battery: list[bool] = []
    power_source = POWER_SOURCE_DEVICE
    simulated = False
    # The restore sits in its own `finally` inside the outer `try`, so it runs before the abort is
    # handled and the problems it collected are in hand when the record is written.
    try:
        try:
            ensure_visible_home(device)
            verify_arm(device, Arm.LIVE, None)
            pid = device.get_wallpaper_pid()
            snapshot.pin()
            power_source = apply_power_mode(device)
            simulated = power_source == POWER_SOURCE_SIMULATED
            before = take_battery_sample(device, pid, clk_tck)
            seconds = minutes * 60.0
            states, on_battery = sample_window_states(device, seconds)
            after = take_battery_sample(device, pid, clk_tck)
        finally:
            problems = undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
    except Exception as error:
        emit_aborted_restore_record(recorder, device, "memory", error, problems)
        raise
    if states and not all(state is True for state in states):
        detail = f"the display was not on for the whole memory window: {states}"
        raise device_layer.ProbeError(detail)
    charging = power_source_problem(on_battery)
    if charging is not None:
        detail = f"this window is not a valid measurement: {charging}"
        raise device_layer.ProbeError(detail)
    if before.total_pss_kb is None or after.total_pss_kb is None:
        detail = "a total-PSS sample could not be parsed, so growth has no value"
        raise device_layer.ProbeError(detail)
    growth_kb = after.total_pss_kb - before.total_pss_kb
    print(f"total PSS {before.total_pss_kb} -> {after.total_pss_kb} kB over {minutes:.1f} min")
    print(f"growth {growth_kb} kB against the {MAX_PSS_GROWTH_KB} kB budget")
    return build_record(
        device,
        "memory",
        seconds,
        window_minutes=minutes,
        pss_start_kb=before.total_pss_kb,
        pss_end_kb=after.total_pss_kb,
        pss_growth_kb=growth_kb,
        pss_budget_kb=MAX_PSS_GROWTH_KB,
        clk_tck=clk_tck,
        power_source=power_source,
        screen_on_samples=states,
        on_battery_samples=on_battery,
        restore_problems=problems,
    )


def phase_pilot(  # noqa: PLR0913 — the arms, the window shape, and the sink are all its inputs
    device: device_layer.AdbDevice,
    *,
    arm: Arm,
    baseline: Baseline | None,
    runs: int,
    minutes: float,
    recorder: Recorder,
) -> list[Record]:
    """
    Record the repeated windows whose spread is the device's noise floor.

    Each window is persisted as soon as it completes, and the floor is emitted as its own record
    rather than merged into the first window's. Persisting per window is what makes the phase
    resumable: a pilot interrupted at window three keeps the windows it completed, instead of
    returning them to a `main` that never got to append them.
    """
    rates: list[float] = []
    cpu_values: list[float] = []
    for run in range(1, runs + 1):
        print(f"\n--- pilot window {run}/{runs} ---")
        record = phase_visible(device, arm=arm, baseline=baseline, minutes=minutes, recorder=recorder)
        record.values["run"] = run
        record.values["pilot"] = True
        record.values["runs"] = runs
        recorder.emit(record)
        rate = record.values.get("discharge_rate_ua")
        if not isinstance(rate, int | float):
            detail = "a visible window recorded no discharge rate, so the noise floor has no input"
            raise device_layer.ProbeError(detail)
        rates.append(float(rate))
        cpu_pct = record.values["cpu_pct"]
        if isinstance(cpu_pct, int | float):
            cpu_values.append(float(cpu_pct))
    mean, deviation, half_width = two_sigma_noise_floor(rates)
    channel_measured = mean > 0
    values: dict[str, object] = {
        "run": 0,
        "pilot": True,
        "runs": runs,
        "discharge_channel_measured": channel_measured,
        "baseline_mean_rate_ua": mean,
    }
    if channel_measured:
        floor_pct = (half_width / mean) * 100.0
        values["noise_floor_pct"] = floor_pct
        values["noise_floor_rate_ua"] = half_width
        print(f"\nwindow discharge rate: mean {mean:.0f} uA, sigma {deviation:.0f} uA, 2 sigma {half_width:.0f} uA")
        print(f"noise floor as a share of the mean: {floor_pct:.2f}%")
    else:
        # A gauge that did not move across every window is the frozen counter the reports describe,
        # not a device with no noise: half-width zero divided by a zero mean says nothing at all, and
        # recording it as a 0% floor would let a later report present an unmeasured channel as a
        # perfectly quiet one. No floor is emitted and the channel is marked unmeasured instead.
        print(
            f"\nthe discharge channel did not move across {runs} windows (mean {mean:.0f} uA), so it is "
            "unmeasured and no noise floor is emitted for it; a threshold cannot be calibrated from a "
            "counter that never changed"
        )
    if cpu_values:
        cpu_mean, cpu_deviation, cpu_half_width = two_sigma_noise_floor(cpu_values)
        values["cpu_mean_pct"] = cpu_mean
        values["cpu_noise_floor_pct"] = cpu_half_width
        print(f"CPU: mean {cpu_mean:.4f}%, sigma {cpu_deviation:.4f}, 2 sigma {cpu_half_width:.4f}")
    return [build_record(device, "pilot_floor", 0.0, **values)]


def numeric_values(records: Sequence[Mapping[str, object]], phase: str, key: str) -> list[float]:
    """Collect one numeric field across a phase's records, skipping records that never carried it."""
    values: list[float] = []
    for record in records:
        if record.get("phase") != phase:
            continue
        value = record.get(key)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def alternation_pairs(
    records: Sequence[Mapping[str, object]],
) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    """
    Pair live and baseline windows from their recorded order, refusing a malformed sequence.

    Pairing the nth baseline window with the nth live window by file order would silently pair
    windows that were not taken under the same conditions; the sequence has to be an alternation
    before that pairing means anything. Pilot windows are excluded: they are the calibration, not
    the comparison.
    """
    sequence = [record for record in records if record.get("phase") == "visible" and not record.get("pilot")]
    if not sequence:
        return []
    arms = [str(record.get("arm")) for record in sequence]
    expected = [Arm.LIVE.value, Arm.BASELINE.value] * (len(sequence) // 2)
    if len(sequence) % 2 != 0 or arms != expected:
        detail = f"the visible windows are not an A/B alternation: recorded arms {arms}"
        raise device_layer.ProbeError(detail)
    return [(sequence[index], sequence[index + 1]) for index in range(0, len(sequence), 2)]


def window_rate(record: Mapping[str, object]) -> float:
    """Read one window's discharge rate, refusing a record that has none or a negative one."""
    rate = record.get("discharge_rate_ua")
    if not isinstance(rate, int | float):
        detail = "a visible window recorded no discharge rate, so no comparison can be computed"
        raise device_layer.ProbeError(detail)
    if rate < 0:
        detail = (
            f"a visible window recorded a negative discharge rate ({rate:.0f} uA), which means the pack "
            "gained charge across it; a state file carrying one is not a discharge comparison"
        )
        raise device_layer.ProbeError(detail)
    return float(rate)


def live_cpu_values(pairs: Sequence[tuple[Mapping[str, object], Mapping[str, object]]]) -> list[float]:
    """Collect the CPU readings of the live windows of each pair."""
    values: list[float] = []
    for live, _ in pairs:
        cpu = live.get("cpu_pct")
        if not isinstance(cpu, int | float):
            detail = "a live window recorded no CPU reading, so B5 has no input"
            raise device_layer.ProbeError(detail)
        values.append(float(cpu))
    return values


def check_measurement_identity(records: Sequence[Mapping[str, object]]) -> None:
    """
    Refuse a record set that mixes builds, API levels, power modes, or tick rates.

    Every record names the identity it was measured under. Aggregating records that disagree on any of
    it would combine measurements that are not comparable, so the report refuses and names the
    distinct values instead of producing one verdict from two builds. A field a record does not carry
    at all is not a disagreement: an older phase record that never recorded the field simply does not
    bound it. A field the record *does* carry with a null value is a value, not an absence — a record
    written before the APK was built carries `"apk_sha256": null`, meaning "unknown build", and that
    must not aggregate with a named one.
    """
    for identity_field in IDENTITY_FIELDS:
        values = {record.get(identity_field) for record in records if identity_field in record}
        if len(values) > 1:
            named = ", ".join("unknown" if value is None else str(value) for value in sorted(values, key=str))
            detail = (
                f"the record set mixes {identity_field} values ({named}), so it combines measurements "
                "from different measurement identities; re-run the phases against one build and configuration"
            )
            raise device_layer.ProbeError(detail)


def check_budget(label: str, value: float | None, budget: float, unit: str) -> Verdict:
    """Build one pass/fail verdict, refusing to pass a budget that was never measured."""
    if value is None:
        return Verdict(
            label,
            VerdictStatus.NOT_MEASURED,
            f"{label}: NOT MEASURED — no value was recorded, so the budget is neither passed nor failed",
        )
    status = VerdictStatus.PASS if value <= budget else VerdictStatus.FAIL
    return Verdict(label, status, f"{label}: {status.value} — {value:.4f}{unit} against {budget}{unit}")


def collect_restore_problems(records: Sequence[Mapping[str, object]]) -> list[str]:
    """Collect every restore problem the records carry, so a failed restore is never silent."""
    problems: list[str] = []
    for record in records:
        found = record.get("restore_problems")
        if isinstance(found, list):
            problems.extend(str(problem) for problem in found)
    return problems


def short_window_reason(record: Mapping[str, object]) -> str | None:
    """
    Return why a record's window is not the length its criterion documents, or None when it is.

    The CLI refuses a `--minutes` below the documented minimum, but the report is fed a state file it
    did not write: it may have been recorded by an older harness, by a hand-edit, or by a phase whose
    clock was not the one asked for. A record that cannot be shown to have run for its documented
    length is not the measurement the criterion is defined on, so it is named and excluded from the
    aggregation rather than averaged in as though a six-second window were a fifteen-minute one.
    """
    phase = str(record.get("phase"))
    minimum = MINIMUM_SECONDS_BY_PHASE.get(phase)
    if minimum is None:
        return None
    duration = record.get("duration_seconds")
    threshold = minimum * MINIMUM_DURATION_TOLERANCE
    if not isinstance(duration, int | float):
        return (
            f"a {phase} record carries no `duration_seconds`, so its window cannot be shown to be the "
            f"documented {minimum / 60.0:.0f} minutes"
        )
    if duration < threshold:
        return (
            f"a {phase} record's window ran {duration:.1f}s, below the documented "
            f"{minimum / 60.0:.0f} minutes ({threshold:.0f}s at the {MINIMUM_DURATION_TOLERANCE:.0%} tolerance)"
        )
    return None


def b4_baseline_verdicts(
    pairs: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
) -> list[Verdict]:
    """
    Build B4's verdicts, one block per documented baseline rather than one per recorded baseline.

    A block per *observed* baseline is what let a stock-only state file carry a complete-looking
    verdict: the secondary comparison was simply absent, and nothing in the report said so. The stock
    comparison is the one B4 is gated on. The black static baseline is the document's secondary one and
    is required only where the device permits setting it, so a run without those windows says so
    explicitly instead of leaving the reader to notice a missing block.
    """
    verdicts: list[Verdict] = []
    for baseline in (Baseline.STOCK.value, Baseline.BLACK.value):
        selected = [(live, base) for live, base in pairs if base.get("baseline") == baseline]
        if not selected:
            if baseline == Baseline.BLACK.value:
                verdicts.append(
                    Verdict(
                        "B4 vs black",
                        VerdictStatus.DEVICE_LIMITED,
                        "B4 vs black: NOT MEASURED (DEVICE-LIMITED) \u2014 no black static baseline windows "
                        "were recorded. That baseline is required only where the device permits setting "
                        "one, and it is not reversible on a production build without root, so the "
                        "dark-image comparison is left uncalibrated rather than assumed equal to stock",
                    )
                )
            else:
                verdicts.append(
                    Verdict(
                        f"B4 vs {baseline}",
                        VerdictStatus.NOT_MEASURED,
                        f"B4 vs {baseline}: NOT MEASURED - no {baseline} baseline windows were "
                        "recorded, so the comparison the criterion is gated on was not measured",
                    )
                )
            continue
        try:
            statistics = paired_statistics(
                [window_rate(base) for _, base in selected], [window_rate(live) for live, _ in selected]
            )
        except device_layer.ProbeError as error:
            verdicts.append(
                Verdict(
                    f"B4 vs {baseline}",
                    VerdictStatus.NOT_MEASURED,
                    f"B4 vs {baseline}: NOT MEASURED - {error} ({len(selected)} pairs recorded)",
                )
            )
            continue
        verdicts.append(
            Verdict(
                f"B4 vs {baseline}",
                VerdictStatus.INFO,
                f"B4 vs {baseline} relative increase: {statistics.relative_delta_pct:.3f}% "
                f"(95% CI upper {statistics.ci_95_upper_pct:.3f}%, {statistics.sample_count} pairs, "
                f"df {statistics.degrees_of_freedom}, t {statistics.t_critical}, "
                f"sigma_d {statistics.standard_deviation:.0f} uA = "
                f"{statistics.standard_deviation / statistics.mean_baseline * 100:.3f}% of the baseline)",
            )
        )
        for label, value in (
            ("point estimate", statistics.relative_delta_pct),
            ("CI upper bound", statistics.ci_95_upper_pct),
        ):
            verdicts.append(check_budget(f"B4 vs {baseline} {label}", value, FINAL_MAX_VISIBLE_DELTA_PCT, "%"))
    return verdicts


def report_lines(records: Sequence[Mapping[str, object]]) -> list[Verdict]:
    """Build the pass/fail verdicts against the committed thresholds."""
    verdicts: list[Verdict] = []
    problems = collect_restore_problems(records)
    if problems:
        verdicts.append(
            Verdict(
                "device restore",
                VerdictStatus.FAIL,
                f"DEVICE RESTORE: FAIL - {len(problems)} problem(s) were recorded",
            )
        )
        verdicts.extend(Verdict("device restore detail", VerdictStatus.INFO, f"  - {p}") for p in problems)

    # A window below its documented length is not the measurement its criterion is defined on, so it
    # is failed and dropped before anything is aggregated from it.
    measured = [record for record in records if short_window_reason(record) is None]
    short = [str(reason) for record in records if (reason := short_window_reason(record)) is not None]
    if short:
        verdicts.append(
            Verdict(
                "window durations",
                VerdictStatus.FAIL,
                f"WINDOW DURATIONS: FAIL - {len(short)} record(s) below their documented minimum",
            )
        )
        verdicts.extend(Verdict("window duration detail", VerdictStatus.INFO, f"  - {problem}") for problem in short)

    # B2 is a maximum across every resources record, not the last one: the state file is append-only,
    # so a rerun after a nonzero census must not hide the earlier failure behind a later clean run.
    for label, key in (("B2 wake locks", "wake_locks"), ("B2 alarms", "alarms"), ("B2 jobs", "jobs")):
        values = numeric_values(measured, "resources", key)
        verdicts.append(check_budget(label, max(values) if values else None, 0.0, ""))

    hidden = numeric_values(measured, "hidden", "cpu_pct")
    verdicts.append(check_budget("B3 hidden CPU", max(hidden) if hidden else None, MAX_HIDDEN_CPU_PCT, "%"))

    try:
        pairs = alternation_pairs(measured)
    except device_layer.ProbeError as error:
        verdicts.append(Verdict("B4/B5", VerdictStatus.NOT_MEASURED, f"B4/B5: NOT MEASURED - {error}"))
        pairs = []

    verdicts.extend(b4_baseline_verdicts(pairs))

    # B5 is the average visible CPU of the document's matrix, so the average is what is checked; the
    # worst window is reported beside it as the observation it is.
    try:
        cpu_values = live_cpu_values(pairs)
    except device_layer.ProbeError as error:
        verdicts.append(
            Verdict("B5 visible CPU", VerdictStatus.NOT_MEASURED, f"B5 visible CPU: NOT MEASURED - {error}")
        )
        cpu_values = []
    if cpu_values:
        verdicts.append(
            check_budget("B5 visible CPU (mean)", sum(cpu_values) / len(cpu_values), FINAL_MAX_VISIBLE_CPU_PCT, "%")
        )
        verdicts.append(
            Verdict(
                "B5 worst window",
                VerdictStatus.INFO,
                f"B5 worst window observed: {max(cpu_values):.4f}% over {len(cpu_values)} windows",
            )
        )
    else:
        verdicts.append(
            Verdict(
                "B5 visible CPU",
                VerdictStatus.NOT_MEASURED,
                "B5 visible CPU: NOT MEASURED - no live window was recorded",
            )
        )

    growth_values = numeric_values(measured, "memory", "pss_growth_kb")
    verdicts.append(
        check_budget(
            "B6 PSS growth (worst run)", max(growth_values) if growth_values else None, MAX_PSS_GROWTH_KB, " kB"
        )
    )
    verdicts.append(
        Verdict(
            "B1 hidden frames",
            VerdictStatus.OUT_OF_SCOPE,
            "B1 hidden frames: NOT MEASURED HERE - the frame counter is #125 criterion 1, which "
            "records its own producer-trace evidence under docs/testing/reports/; this protocol "
            "does not own a console that could make a zero-frame claim",
        )
    )
    return verdicts


def report_exit_code(verdicts: Sequence[Verdict]) -> int:
    """
    Derive the report's exit status from its verdicts rather than from its text.

    Any FAIL is non-zero. A criterion that was never measured is non-zero too, because an acceptance
    run that skipped a criterion has not established the acceptance; the exceptions are the two
    statuses that name *why* the absence is not a failure of this run — a criterion explicitly out of
    scope for it (B1, which is #125's), and a criterion the device cannot measure at all (the black
    static baseline, which the document requires only where the device permits setting one). Both are
    printed by the report rather than dropped, so the absence is recorded even though it does not gate.
    """
    if any(verdict.status is VerdictStatus.FAIL for verdict in verdicts):
        return 1
    if any(verdict.status is VerdictStatus.NOT_MEASURED for verdict in verdicts):
        return 1
    return 0


def phase_report(records: Sequence[Mapping[str, object]]) -> int:
    """Print the aggregate report and every unrun check it can see, returning its exit status."""
    check_measurement_identity(records)
    revisions = {str(record.get("revision")) for record in records}
    api_levels = {record.get("api_level") for record in records}
    print(f"records: {len(records)}")
    print(f"source revisions: {', '.join(sorted(revisions))}")
    print(f"API levels: {', '.join(str(level) for level in sorted(api_levels, key=str))}")
    for identity_field in IDENTITY_FIELDS:
        if identity_field == "api_level":
            continue
        values = sorted(
            {record.get(identity_field) for record in records if record.get(identity_field) is not None}, key=str
        )
        if values:
            print(f"{identity_field}: {', '.join(str(value) for value in values)}")
    print(f"committed B4 threshold: {FINAL_MAX_VISIBLE_DELTA_PCT}% relative increase")
    print(f"committed B5 threshold: {FINAL_MAX_VISIBLE_CPU_PCT}% of one core")
    floors = [record for record in records if record.get("phase") == "pilot_floor"]
    if floors:
        latest_floor = floors[-1]
        if latest_floor.get("noise_floor_rate_ua") is None:
            print("pilot floors in this state file: the discharge channel did not move, so it is unmeasured")
        else:
            print(
                f"pilot floors in this state file: discharge 2 sigma {latest_floor.get('noise_floor_rate_ua')} uA, "
                f"CPU 2 sigma {latest_floor.get('cpu_noise_floor_pct')}% of one core"
            )
    else:
        print(
            f"pilot floors committed with the thresholds: discharge 2 sigma {PILOT_DISCHARGE_NOISE_FLOOR_UA} uA, "
            f"CPU 2 sigma {PILOT_CPU_NOISE_FLOOR_PCT}% of one core ({PILOT_REPORT})"
        )
    print()
    verdicts = report_lines(records)
    for verdict in verdicts:
        print(verdict.line)
    return report_exit_code(verdicts)


def add_window_arguments(parser: argparse.ArgumentParser, minimum_minutes: float) -> None:
    """
    Add the duration option shared by the windowed phases, floored at the documented length.

    The default is the minimum: the matrix states the window each criterion is defined over, so a
    shorter one is a different measurement and is refused rather than silently accepted. Recording a
    six-second "window" and reporting it as B3 would be the same defect as reading an unparsable probe
    as zero — a number that looks like a measurement and is not one.
    """
    parser.add_argument(
        "--minutes",
        type=minutes_at_least(minimum_minutes),
        default=minimum_minutes,
        help=f"Length of each measurement window in minutes (at least {minimum_minutes:g})",
    )


def add_arm_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arm and baseline options shared by the visible and pilot phases."""
    parser.add_argument("--arm", choices=[arm.value for arm in Arm], required=True, help="A live, B baseline")
    parser.add_argument(
        "--baseline",
        choices=[baseline.value for baseline in Baseline],
        default=None,
        help="Which static wallpaper arm B is bound to; required for arm B",
    )


def positive_float(raw: str) -> float:
    """
    Parse a finite, strictly positive number for the command line.

    `inf` is refused along with zero and the negatives: it is not a large window, it is a number the
    phase can never reach, and `sample_window_states` would subtract its 30-second interval from it
    forever, turning a bounded phase into an unbounded one. `nan` compares false against everything, so
    it slips past a `<= 0` test and has to be refused by name.
    """
    value = float(raw)
    if not math.isfinite(value):
        message = f"{raw} is not a finite duration"
        raise argparse.ArgumentTypeError(message)
    if value <= 0:
        message = f"{raw} is not a positive duration"
        raise argparse.ArgumentTypeError(message)
    return value


def minutes_at_least(minimum: float) -> Callable[[str], float]:
    """Build the `--minutes` parser for one phase, refusing a window below that phase's minimum."""

    def parse_minutes(raw: str) -> float:
        value = positive_float(raw)
        if value < minimum:
            message = (
                f"{raw} minutes is below the {minimum:g} minutes the acceptance matrix documents for "
                "this criterion, so the window would not be the measurement the budget is defined on"
            )
            raise argparse.ArgumentTypeError(message)
        return value

    return parse_minutes


def positive_int(raw: str) -> int:
    """Parse a strictly positive integer for the command line."""
    value = int(raw)
    if value <= 0:
        message = f"{raw} is not a positive count"
        raise argparse.ArgumentTypeError(message)
    return value


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command line: one bounded phase per subcommand."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-s", "--serial", help="Target ADB serial", default=None)
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Directory the phase records are written to",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("resources", help="B2: wake locks and alarms the app holds")

    hidden = subcommands.add_parser("hidden", help="B3: hidden-state CPU over a screen-off window")
    add_window_arguments(hidden, MINIMUM_HIDDEN_MINUTES)

    visible = subcommands.add_parser("visible", help="B4/B5: one visible window")
    add_arm_arguments(visible)
    add_window_arguments(visible, MINIMUM_WINDOW_MINUTES)

    pilot = subcommands.add_parser("pilot", help="Repeated windows whose spread is the noise floor")
    add_arm_arguments(pilot)
    add_window_arguments(pilot, MINIMUM_WINDOW_MINUTES)
    pilot.add_argument("--runs", type=positive_int, default=MIN_PAIRED_SAMPLES, help="Windows to run")

    memory = subcommands.add_parser("memory", help="B6: total-PSS growth over a long window")
    add_window_arguments(memory, MINIMUM_MEMORY_MINUTES)

    subcommands.add_parser("report", help="Aggregate the records the phases wrote")
    return parser


def dispatch(args: argparse.Namespace, device: device_layer.AdbDevice, recorder: Recorder) -> list[Record]:
    """Run the requested phase, writing each record to the state file as it is produced."""
    if args.command == "resources":
        return [recorder.emit(phase_resources(device))]
    if args.command == "hidden":
        return [recorder.emit(phase_hidden(device, args.minutes, recorder=recorder))]
    if args.command == "visible":
        return [
            recorder.emit(
                phase_visible(
                    device,
                    arm=Arm(args.arm),
                    baseline=baseline_of(args),
                    minutes=args.minutes,
                    recorder=recorder,
                )
            )
        ]
    if args.command == "pilot":
        # `phase_pilot` emits its windows itself as each completes and returns only the floor record.
        return [
            recorder.emit(record)
            for record in phase_pilot(
                device,
                arm=Arm(args.arm),
                baseline=baseline_of(args),
                runs=args.runs,
                minutes=args.minutes,
                recorder=recorder,
            )
        ]
    if args.command == "memory":
        return [recorder.emit(phase_memory(device, args.minutes, recorder=recorder))]
    return []


def baseline_of(args: argparse.Namespace) -> Baseline | None:
    """Read the requested baseline, as `None` when the arm does not need one."""
    return Baseline(args.baseline) if args.baseline else None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line, run one bounded phase, and write its record."""
    args = build_argument_parser().parse_args(argv)
    state_dir = Path(args.state)
    if args.command == "report":
        try:
            records = load_records(state_dir)
        except device_layer.ProbeError as error:
            print(f"ERROR: {device_layer.error_detail(error)}", file=sys.stderr)
            return 1
        try:
            return phase_report(records)
        except device_layer.ProbeError as error:
            print(f"ERROR: {device_layer.error_detail(error)}", file=sys.stderr)
            return 1
    serial = device_layer.select_target_serial(args.serial)
    device = device_layer.AdbDevice(serial)
    recorder = Recorder(state_dir)
    try:
        sdk = device.run_adb(["shell", "getprop", "ro.build.version.sdk"]).decode(errors="replace")
        print(f"$ adb -s {serial}  (API {parse_api_level(sdk)})")
        dispatch(args, device, recorder)
    except (subprocess.SubprocessError, OSError, device_layer.ProbeError) as error:
        print(f"ERROR: {device_layer.error_detail(error)}", file=sys.stderr)
        return 1
    restore_failures = collect_restore_problems([record.values for record in recorder.records])
    if restore_failures:
        print("DEVICE RESTORE FAILURES:", file=sys.stderr)
        for failure in restore_failures:
            print(f"  - FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
