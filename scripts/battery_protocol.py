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
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

import device_layer

# The judgement half of the protocol: the budgets, the statistics, the pairing rule, and the report
# itself. Names are imported rather than the module, so that every call site below is unchanged by
# the split and the slice that moved them out is exactly the diff a reviewer has to read. The
# dependency runs one way only — `battery_report` imports nothing from here.
from battery_report import (
    MAX_HIDDEN_CPU_PCT,
    MAX_PSS_GROWTH_KB,
    MIN_PAIRED_SAMPLES,
    MINIMUM_HIDDEN_MINUTES,
    MINIMUM_MEMORY_MINUTES,
    MINIMUM_WINDOW_MINUTES,
    WAKE_LOCK_COVERAGE,
    Arm,
    Baseline,
    collect_restore_problems,
    phase_report,
    two_sigma_noise_floor,
)

# A run of digits as a device prints one, bounded. Every pattern below that captures a number is handed
# to `int()`, and `int()` refuses a value of more than about 4300 digits by *raising* rather than
# converting it — so an unbounded `\d+` turns a dump that lost its mind into a traceback instead of the
# named refusal the harness promises. No field, census, or tick count this harness reads is 18 digits
# wide, so the bound costs nothing that a real dump has.
#
# The trailing lookahead is what makes the bound a refusal rather than a truncation: in a pattern with
# no trailing anchor — `TOTAL_PSS_PATTERN` is the one — `\d{1,18}` would happily match the first
# eighteen digits of a longer run and report them as the reading, which is the silent misreading the
# bound exists to prevent. `(?!\d)` refuses the run instead.
DIGITS: Final = r"\d{1,18}(?!\d)"
CHARGE_COUNTER_PATTERN: Final = re.compile(rf"^[ \t]*Charge counter:[ \t]*({DIGITS})[ \t]*$", re.MULTILINE)
BATTERY_LEVEL_PATTERN: Final = re.compile(rf"^[ \t]*level:[ \t]*({DIGITS})[ \t]*$", re.MULTILINE)
# Anchored to the field name so that `Max charging voltage:` can never be read as the pack voltage.
BATTERY_VOLTAGE_PATTERN: Final = re.compile(rf"^[ \t]*voltage:[ \t]*({DIGITS})[ \t]*$", re.MULTILINE)
# The separators are `[ \t]` and not `\s` for the reason the census patterns give: `\s` matches a
# newline, so `\s+` after `TOTAL` would let a truncated `TOTAL` line reach the next line for its number
# and report an unrelated figure as the app's total PSS. The trailing lookahead is what keeps the
# *captured* value from doing the same thing: this is the one capture with no other field name or
# anchor after it, so without a boundary the `123` of a corrupted `TOTAL 123oops` would match and be
# recorded as a reading. A real line continues with more columns after horizontal whitespace.
TOTAL_PSS_PATTERN: Final = re.compile(rf"^[ \t]*TOTAL[ \t]+({DIGITS})(?=[ \t]|$)", re.MULTILINE)
SDK_PATTERN: Final = re.compile(rf"^({DIGITS})$")
# Every shape `dumpsys jobscheduler` prints a job line in, taken from the test device's own dump.
# The two components at the end of a job line are a `<hash> <component>` pair; the component is
# either `<pkg>/<class>` or `<ns>@<pkg>/<class>`, and one legacy line on this device is tagged
# rather than component-shaped (`DownloadManager:<pkg>`). The old pattern recognised the plain
# `JOB #<uid>/<id>` form only, which matched 74 of the 521 job lines this device lists: the
# namespaced form (`androidx.work.systemjobscheduler:<uid>/<id>`, `SyncManager:<uid>/<id>`) is where
# WorkManager and the sync adapters keep their jobs, so an app's own jobs could be silently dropped
# from the count. Group 1 is the package the job belongs to, which is what attributes it to the app.
JOB_LINE_PATTERN: Final = re.compile(
    r"^\s*JOB \S+/-?\d+:\s+\S+\s+(?:@[\w.]+@)?(?:[A-Za-z][\w.]*:)?([\w.\-]+)(?:[/:]|$)",
    re.MULTILINE,
)
# The job *entry* predicate: one line per job, whatever its shape. It is deliberately wider than
# JOB_LINE_PATTERN, so that a job line the attribution cannot parse shows up as a count mismatch or
# as an unparsable entry rather than being dropped silently.
JOB_ENTRY_PATTERN: Final = re.compile(r"^\s*JOB \S+/-?\d+:", re.MULTILINE)
# The wake-lock entry predicate counts every lock type, not `PARTIAL_WAKE_LOCK` alone. The census
# counts the whole device, and this device's `Wake Locks: size=1` is a `DOZE_WAKE_LOCK`, so an
# app-scoped count of partial locks can never equal the census and the census could never be
# checked against it.
WAKE_LOCK_ENTRY_PATTERN: Final = re.compile(r"^\s*\w+_WAKE_LOCK\b", re.MULTILINE)
# The alarm entry predicate: the census block prints one `<TYPE> #<n>: Alarm{...}` header per
# pending alarm. It must be read inside the census block only, because a later section lists alarms
# of its own in exactly this shape — this device prints `Past-due non-wakeup alarms: 1` followed by
# `ELAPSED #1: Alarm{...}` — and a whole-dump scan counts that one too.
ALARM_ENTRY_PATTERN: Final = re.compile(r"^\s*\w+\s*#\d+:\s*Alarm\{", re.MULTILINE)
# A `dumpsys jobscheduler` dump that has been read at all prints a `Registered <n> jobs:` census line
# before the job list; requiring it is what separates "the app has no jobs" from "this is not a
# jobscheduler dump", so an unparsable probe is an error rather than a zero.
#
# The line must *end* after the count, as the power and alarm census patterns below already require,
# with the same `[ \t]` trailing class. Anchored at the start only, this matched a malformed line such
# as `Registered 0 jobs: CORRUPT` and read its leading zero as the census — a count from a line the
# probe never actually read, which for a zero is a B2 pass.
JOBSCHEDULER_CENSUS_PATTERN: Final = re.compile(rf"^[ \t]*Registered ({DIGITS}) jobs:[ \t]*$", re.MULTILINE)
# A `dumpsys power` that has been read at all prints its wake-lock census before listing the locks,
# the same way `dumpsys jobscheduler` prints its job census. Requiring it is what separates "the app
# holds no wake locks" from "this is not a power dump", so a truncated or reshaped dump is an error
# rather than a zero.
#
# The leading and trailing whitespace classes are `[ \t]` and not `\s`. `\s` matches a newline, so a
# pattern anchored `^\s*` on a census line can begin its match at the blank line *above* the census and
# run into it — the match then starts one line early, and a parser that locates the censused block from
# the match start reads the block from the wrong line. Requiring a line's own spaces keeps the match on
# the census line.
POWER_WAKELOCK_CENSUS_PATTERN: Final = re.compile(rf"^[ \t]*Wake Locks: size=({DIGITS})[ \t]*$", re.MULTILINE)
# `dumpsys alarm` likewise prints a census before listing the pending alarms, and that census is what
# makes an alarm count trustworthy. The line is taken from the test device's own dump, which reads
# `  164 pending alarms: ` — count first, no `Pending alarm batches:` line anywhere in it. The count
# is a count of entries and not the highest entry index: the block below this census is numbered `#1`
# onwards, and on five consecutive reads of one device the declared count and the number of entry
# lines agreed exactly, which is what `census_entry_block` holds them to.
ALARM_CENSUS_PATTERN: Final = re.compile(rf"^[ \t]*({DIGITS}) pending alarms:[ \t]*$", re.MULTILINE)
POWERED_LINE_PATTERN: Final = re.compile(r"^\s*(\w+) powered:\s*(true|false)\s*$", re.MULTILINE)
# The four power sources `dumpsys battery` reports. A reading is only complete when all four are
# present: a dump that lost three of them must not be read as "the remaining one is false, so the
# device is on battery", which is what a per-line scan would conclude from a truncated dump.
EXPECTED_POWER_SOURCES: Final = ("AC", "USB", "Wireless", "Dock")
# A decimal integer as a device prints one, bounded so that a long run of digits is refused rather
# than handed to `int()`. `str.isdigit()` is not the same question: `"²"` is a digit but not a decimal
# one, and it reaches `int()` as a `ValueError`, as does any value of more than a few thousand digits.
# A device setting is never 18 digits wide, so the bound costs nothing and makes "unreadable" the
# answer for everything the pattern does not match. The sign is accepted here because
# `screen_brightness` is legitimately `-1` on a device using automatic brightness.
DECIMAL_INT_PATTERN: Final = re.compile(rf"[+-]?{DIGITS}")
# What `settings get <namespace> <key>` prints for a key that was never set. It is the only reading
# that means "unset", which is why it is a literal of its own rather than "anything that is not a
# decimal integer" — that reading is also what a settings service that did not answer looks like, and
# treating the two alike loses a value the restore was supposed to put back.
UNSET_SETTING_TEXT: Final = "null"
# A count a device prints that cannot be negative — a UID from `stat -c %u`, or the utime and stime
# ticks from `/proc/<pid>/stat`. It is a pattern of its own rather than the signed one above, because a
# read that failed or was malformed can answer `-1` and `int()` would take it. Both callers then treat a
# negative reading as a measurement: `phase_resources` refuses only `None` and would build the wake-lock
# predicate `uid=-1`, which matches no lock and reports a B2 pass the app did not earn, while a negative
# utime makes a window's CPU delta larger than the CPU the process actually spent.
UNSIGNED_INT_PATTERN: Final = re.compile(DIGITS)

DEFAULT_STATE_DIR: Final = Path("build/reports/battery")
RECORD_FILE_NAME: Final = "records.jsonl"

# The launcher takes a moment to come forward after KEYCODE_HOME, so the readback that the home
# screen is in front is bounded exactly the way the service-log confirmations in `device_layer` are:
# five reads half a second apart, which covers the transition without holding the phase open.
HOME_CONFIRM_ATTEMPTS: Final = 5
HOME_CONFIRM_INTERVAL_SECONDS: Final = 0.5

SAMPLE_INTERVAL_SECONDS: Final = 30.0
COMMAND_TIMEOUT_SECONDS: Final = 60
# The longest a measurement window will hold the shared device, and the longest a pilot invocation —
# which is a run of windows rather than one — will. Both are read off `docs/battery-budget.md`, which
# requires that "no single invocation, and above all no single window, blocks for hours": the longest
# criterion its matrix documents is thirty minutes, so a window is bounded at forty-five, and the
# recorded six-pair pilot took ninety minutes, so an invocation is bounded at two hours. A bound is
# needed at all because a finite duration passes every test arithmetic can pose — `1e15` minutes is
# finite, positive, and one the sampling loop makes progress through — so neither finiteness nor
# progress is what makes a phase bounded.
MAXIMUM_WINDOW_MINUTES: Final = 45.0
MAXIMUM_PILOT_MINUTES: Final = 120.0
# The count the pilot will accept. It cannot bound the invocation on its own — a count at the floor
# with a window at the ceiling is far past the time bound — so `phase_pilot` checks the product as
# well; this is what bounds the rates and CPU lists the run accumulates, and what refuses an absurd
# count at the command line rather than after the device has been reached.
MAXIMUM_PILOT_RUNS: Final = int(MAXIMUM_PILOT_MINUTES // MINIMUM_WINDOW_MINUTES)

PROC_STAT_UTIME_INDEX: Final = 11
PROC_STAT_INDEX_STIME: Final = 12
MIN_PROC_STAT_FIELDS: Final = 13
# `/proc/<pid>/stat` counts CPU in kernel clock ticks, and the ticks per second is a kernel constant
# (`getconf CLK_TCK`) that is not 100 on every device. It is probed per run through
# `device_layer.AdbDevice.read_process_cpu_clock_ticks` and the run fails when the probe cannot
# answer, because scaling a CPU percentage by an assumed rate misreports every CPU criterion.


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
# How the device's identity is recorded. The digest is salted so that it is meaningful only inside
# this project's own state files, and truncated because it is only ever compared for equality.
DEVICE_ID_SALT: Final = "godaniya-126 device|"
DEVICE_ID_CHARS: Final = 12


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
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """Hold the values read before a phase changed anything."""
        self.device = device
        self.brightness = brightness
        self.brightness_mode = brightness_mode
        self.screen_timeout = screen_timeout
        self.airplane_mode = airplane_mode
        self.stay_on = stay_on
        self.screen_on = screen_on
        self._sleep = sleep

    @classmethod
    def capture(
        cls,
        device: device_layer.AdbDevice,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> DeviceStateSnapshot:
        """
        Read the state a measurement phase pins, including the display's own power state.

        Four of the settings `pin()` writes are required rather than best-effort: a value that could
        not be read is a value the restore cannot put back, and skipping it would leave the shared
        device changed by a phase that reported success. The stay-awake setting is the one exception —
        it is legitimately unset on a device that never had it, and an unset value *is* captured, as
        `None`, and restored by deleting the key. The exception is only for the setting's *unset*
        reading, not for an unreadable one: `read_unsettable_int_setting` tells the device's `null`
        from a service that did not answer, and refuses the second before anything is pinned.
        """
        return cls(
            device,
            brightness=require_int_setting(device, "system", "screen_brightness"),
            brightness_mode=require_int_setting(device, "system", "screen_brightness_mode"),
            screen_timeout=require_int_setting(device, "system", "screen_off_timeout"),
            airplane_mode=require_int_setting(device, "global", "airplane_mode_on"),
            stay_on=read_unsettable_int_setting(device, "global", "stay_on_while_plugged_in"),
            screen_on=device.read_screen_on(),
            sleep=sleep,
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
            # restore and skip the display step that follows it. The reader's refusal is caught here
            # with it, and that is the point of using this reader rather than `read_int_setting`: a
            # readback of `""` or any other unreadable text reads as `None` there — indistinguishable
            # from the unset value the delete was meant to produce — so a delete nobody could verify
            # was reported as a clean restore. Here it is a problem like any other.
            try:
                remaining = read_unsettable_int_setting(self.device, "global", "stay_on_while_plugged_in")
            except (subprocess.SubprocessError, OSError, device_layer.ProbeError) as error:
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
            sleep_fn = self._sleep if self._sleep is not None else time.sleep
            sleep_fn(RESTORE_SETTLE_SECONDS)
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
        """
        Return the display to the power state it was found in.

        The reads are guarded the way the settings steps are, and for the same reason. `restore()` is
        reached from `undo_phase_changes` through `problems.extend(...)`, so a read that raised here
        would discard every problem the settings steps had already collected — `extend` never runs and
        the caller reports one generic failure in their place — and the sleep below would be skipped.
        """
        if self.screen_on is None:
            return ["the display state could not be read before the phase, so it cannot be restored"]
        if self.screen_on:
            if not self.device.wake_screen():
                return ["the display was on before the phase and could not be woken again"]
            return []
        problem = self._sleep_display()
        return [problem] if problem is not None else []

    def _sleep_display(self) -> str | None:
        """Put a display that started off back to sleep, or say why it could not be."""
        try:
            if self.device.read_screen_on() is False:
                return None
        except (subprocess.SubprocessError, OSError) as error:
            return f"the display state could not be read to restore it: {device_layer.error_detail(error)}"
        self.device.sleep_screen()
        try:
            if self.device.read_screen_on() is not False:
                return "the display was off before the phase and could not be put back to sleep"
        except (subprocess.SubprocessError, OSError) as error:
            return f"the display state could not be read back after the sleep: {device_layer.error_detail(error)}"
        return None


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

    The reset and the readback that verifies it are two steps rather than one. `power_source_restored`
    reads a battery dump, and a dump that cannot be parsed raises `ProbeError` from there; sharing the
    reset's guard meant an unparsable readback escaped this function entirely, so the settings restore
    below never ran and the problems list stayed empty — the shared device left pinned with nothing on
    disk for `report` to print.
    """
    problems: list[str] = []
    if simulated:
        try:
            reset_battery_simulation(device)
        except (subprocess.SubprocessError, OSError) as error:
            problems.append(f"battery simulation reset failed: {device_layer.error_detail(error)}")
        if prior_power is not None:
            try:
                restored = power_source_restored(device, prior_power)
            except (subprocess.SubprocessError, OSError, device_layer.ProbeError) as error:
                problems.append(f"the restored power sources could not be read: {device_layer.error_detail(error)}")
            else:
                if not restored:
                    problems.append("the reported power sources did not return to their pre-phase state")
    try:
        problems.extend(snapshot.restore())
    except (subprocess.SubprocessError, OSError) as error:
        problems.append(f"device settings restore failed: {device_layer.error_detail(error)}")
    return problems


def read_setting_text(device: device_layer.AdbDevice, namespace: str, key: str) -> str:
    """Read one device setting as the text `settings get` printed for it, stripped."""
    return device.run_adb(["shell", "settings", "get", namespace, key]).decode(errors="replace").strip()


def read_int_setting(device: device_layer.AdbDevice, namespace: str, key: str) -> int | None:
    """
    Read one integer device setting, or None when it is unset or not a decimal integer.

    The readback runs inside the restore as well as before it, and the restore's contract is that no
    step can abort the ones after it. A readback that raised here would do exactly that — the settings
    restore would stop at this step and the display restore would never run — so text that cannot be
    read is `None`, which the restore already reports as a value that did not read back.

    That conflation is the right reading for a *comparison* — `None` is never the value a step wrote,
    so it fails one — but not for deciding what a setting was worth. `read_unsettable_int_setting` is
    the reader that tells the two apart, and the stay-awake setting is captured through it.
    """
    return parse_signed_int(read_setting_text(device, namespace, key))


def read_unsettable_int_setting(device: device_layer.AdbDevice, namespace: str, key: str) -> int | None:
    """
    Read one integer device setting that is legitimately unset, refusing an unreadable reading.

    `settings get` prints `null` for a key that was never set, and a decimal integer for one that was,
    so those are the only two readings that say anything about the setting's value. Anything else is
    a settings service that did not answer — and `read_int_setting` reports it as `None`, which is
    exactly the sentinel for "never set". Nothing downstream can tell them apart afterwards: the
    capture stores it, `pin()` overwrites the setting anyway, and the restore then *deletes* a key
    that may have held a real value, reporting a clean restore of a device it left changed. The
    distinction has to be made where the text is, so it is made here and an unreadable reading stops
    the phase before it pins anything — the same refusal `require_int_setting` makes for the four
    settings that cannot be captured as unset, for the same reason.
    """
    raw = read_setting_text(device, namespace, key)
    if raw == UNSET_SETTING_TEXT:
        return None
    value = parse_signed_int(raw)
    if value is None:
        detail = (
            f"{namespace}/{key} read back as {raw!r}, which is neither a decimal integer nor the "
            f"device's {UNSET_SETTING_TEXT} for an unset setting, so whether it held a value to "
            "restore cannot be read from this device"
        )
        raise device_layer.ProbeError(detail)
    return value


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


def power_mode_for(device: device_layer.AdbDevice) -> str:
    """
    Name the discharge state a window needs, reading the device without changing it.

    A device that is genuinely discharging already has the live fuel gauge this measurement wants,
    and `cmd battery unplug` would replace that with a frozen override — the very artefact the
    simulation was invented to work around. The override is therefore chosen only when the device is
    on a charger, and the mode is recorded in the window as part of its identity.

    Deciding is separate from applying because applying can fail *after* the device has obeyed: a
    dropped transport or a timeout on `cmd battery unplug` leaves the override on the device while the
    call raises. The caller records what this returned before anything is written, so the restore
    knows a reset is owed even when the call that applied it came back with an error.
    """
    return POWER_SOURCE_DEVICE if device_on_own_battery(device) else POWER_SOURCE_SIMULATED


def apply_power_mode(device: device_layer.AdbDevice, mode: str) -> None:
    """Apply the mode `power_mode_for` named, which its caller has already recorded."""
    if mode == POWER_SOURCE_SIMULATED:
        simulate_unplug(device)


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


def parse_signed_int(raw: str) -> int | None:
    """
    Read a device's signed decimal integer, or None when the text is not one.

    A parse rather than a `str.isdigit()` test: `isdigit()` answers a different question, and the
    guard built on it here — `raw.lstrip("-").isdigit()` — accepted `"--5"` and `"²"`, both of which
    then raised `ValueError` from `int()` at a call site whose caller treats "unreadable" as a value.
    """
    return int(raw) if DECIMAL_INT_PATTERN.fullmatch(raw) else None


def parse_unsigned_int(raw: str) -> int | None:
    """Read a device's non-negative decimal integer, or None when the text is not one."""
    return int(raw) if UNSIGNED_INT_PATTERN.fullmatch(raw) else None


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
    # The tokens are read as non-negative decimals, not handed to `int()`: a tick count cannot be
    # negative, and `int()` would take one. A start below zero would make the window's delta larger
    # than the CPU the process actually spent, and the delta is the only check downstream — it refuses
    # a counter that went *backwards*, not one that began below the origin.
    utime = parse_unsigned_int(parts[PROC_STAT_UTIME_INDEX])
    stime = parse_unsigned_int(parts[PROC_STAT_INDEX_STIME])
    if utime is None or stime is None:
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


def wallpaper_pid(device: device_layer.AdbDevice) -> int | None:
    """
    Read the wallpaper process id, refusing an answer that cannot be read as one.

    `device_layer.get_wallpaper_pid` reads `pidof` and answers `None` when nothing matches. A token
    that is *not* a process id is a different thing, and that reader converts it with `int()` after
    an `isdigit()` test, so a digit run past `int()`'s limit raises `ValueError` from inside it.
    `ValueError` is not one `main` catches, so a corrupted response reached the operator as a
    traceback rather than the `ERROR:` line every other unreadable probe produces.

    The translation is made here rather than in the device-layer reader because that reader is shared
    with the repository's other harnesses and carries the same unbounded conversion at four sites;
    widening its contract is its own change, and this slice owes its own callers the named refusal.
    `None` still means exactly what it means there — no live wallpaper process — because a response
    nobody could read is not evidence of one.
    """
    try:
        return device.get_wallpaper_pid()
    except ValueError as error:
        detail = (
            f"the wallpaper process id probe answered something that is not a process id ({error}), so "
            "the wallpaper's process cannot be identified; refusing rather than reading it as no live "
            "process"
        )
        raise device_layer.ProbeError(detail) from error


def read_process_uid(device: device_layer.AdbDevice, pid: int | None) -> int | None:
    """
    Read the owning UID of a process from `/proc/<pid>`, or None when it cannot be read.

    A UID is non-negative, so a negative read is a failed or malformed one rather than a process's
    owner. Returning it would not stop at this function: `phase_resources` refuses only `None`, and the
    wake-lock predicate would be built as `uid=-1`, which matches no lock and reports the app as
    holding none — a B2 pass from a read that answered nothing.
    """
    if pid is None or pid <= 0:
        return None
    output = device.run_adb(["shell", "stat", "-c", "%u", f"/proc/{pid}"]).decode(errors="replace").strip()
    return parse_unsigned_int(output)


def require_clk_tck(device: device_layer.AdbDevice) -> int:
    """
    Probe the device's kernel clock-tick rate, refusing to run without a real one.

    `device_layer.read_process_cpu_clock_ticks` reads `getconf CLK_TCK`. When it cannot answer, the
    CPU criteria cannot be scaled correctly, so the run fails loudly instead of assuming 100.

    "Cannot answer" includes answering with something that cannot be converted: that reader tests the
    text with `str.isdigit()` and then hands it to `int()`, so a digit run past `int()`'s limit — or a
    character `isdigit()` accepts and `int()` does not, such as `"²"` — raises `ValueError` from
    inside it. `ValueError` is not one `main` catches, so a corrupted response reached the operator as
    a traceback rather than the `ERROR:` line this refusal is. The translation is made here rather
    than in the device-layer reader because that reader is shared with the repository's other
    harnesses and carries the same unbounded conversion at four sites; widening its contract is its
    own change, and this slice owes its own callers the named refusal.
    """
    try:
        clk_tck = device.read_process_cpu_clock_ticks()
    except ValueError as error:
        detail = (
            f"the CLK_TCK probe answered something that is not a tick rate ({error}), so process CPU "
            "ticks cannot be converted to seconds; refusing to scale every CPU criterion by an "
            "assumed rate"
        )
        raise device_layer.ProbeError(detail) from error
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


def census_entry_block(  # noqa: PLR0913 — the dump, the two patterns, and the three names it refuses by
    dump: str,
    census_pattern: re.Pattern[str],
    entry_pattern: re.Pattern[str],
    *,
    probe: str,
    census_line: str,
    entry_shape: str,
) -> list[str]:
    """
    Return the entry lines of a censused dump section, refusing unless they account for the census.

    The census alone is not a check. A dump truncated after its census line still carries the count,
    and a parser that reads only "does the census exist" then reports a legitimate zero from output
    that lists nothing at all — a fabricated pass for the budget that measures it. Requiring the
    enumerated entries to *equal* the declared count is what makes the census mean something, and it
    fails in both directions: fewer entries is a truncated dump, more is a shape the probe does not
    know that the count was never meant to include.

    The block runs from the census line to the first line that is neither an entry nor indented
    deeper than the census, so a later section cannot inflate it. That boundary is not hypothetical:
    this device's alarm dump prints `Past-due non-wakeup alarms: 1` followed by
    `ELAPSED #1: Alarm{...}` in exactly the entry shape, four hundred lines below the pending-alarm
    census, and a scan of the whole dump counts it. Multi-line entries are why the boundary is drawn
    by indentation rather than by "the first line that is not an entry": an alarm entry is an
    `Alarm{...}` header followed by six or more `key=value` continuation lines, none of which are
    entries.

    A line inside the block that is neither an entry nor a continuation of one is a third failure and
    is refused too, because reconciliation alone cannot see it: a block holding a malformed entry
    beside exactly the declared number of recognised ones reconciles perfectly. A continuation is
    identified by indentation — it is deeper than the entry it belongs to — which is the only
    structural fact the three dumps share, so it is what the check is built on.

    `probe` names the dump for the message, `census_line` is the literal line required of it, and
    `entry_shape` is what one entry looks like, so that a refusal says which failure it is rather
    than only that something was wrong.
    """
    census = census_pattern.search(dump)
    if census is None:
        detail = (
            f"the {probe} carried no {census_line} census, so it cannot be read as a count of its "
            f"entries; refusing to report a zero from output that lists none"
        )
        raise device_layer.ProbeError(detail)
    declared = int(census.group(1))
    lines = dump.splitlines()
    census_index = dump.count("\n", 0, census.start())
    census_text = lines[census_index] if census_index < len(lines) else ""
    indent = len(census_text) - len(census_text.lstrip())
    entries: list[str] = []
    entry_indent: int | None = None
    for line in lines[census_index + 1 :]:
        if not line.strip():
            continue
        if entry_pattern.match(line):
            entries.append(line)
            entry_indent = len(line) - len(line.lstrip())
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent:
            break
        # A line inside the block that is not an entry is a continuation of one — the alarm dump
        # prints six or more `key=value` lines under each `Alarm{...}` header, and the jobscheduler
        # dump more below each `JOB`. A continuation is *deeper indented than the entry it belongs
        # to*, which is what tells it apart from an entry or a header the pattern did not recognise:
        # such a line either appears before any entry (a block that holds content under a census of
        # zero) or sits at the entry's own depth. Skipping those silently was the same defect as
        # reading a count from a line that was never the census: a malformed entry could sit in the
        # block while the recognised ones reconciled exactly, so the dump was accepted and the
        # unrecognised entry counted as nothing.
        if entry_indent is None or line_indent <= entry_indent:
            detail = (
                f"the {probe}'s {census_line} census block holds a line that is neither an entry "
                f"shaped like {entry_shape} nor a continuation of one: {line.strip()!r}; the census "
                "cannot be reconciled against a block it does not describe"
            )
            raise device_layer.ProbeError(detail)
    if len(entries) < declared:
        detail = (
            f"the {probe} declares {declared} entries but the block below its {census_line} census "
            f"lists {len(entries)}: the dump is truncated, or an entry shaped like {entry_shape} was "
            f"not recognised"
        )
        raise device_layer.ProbeError(detail)
    if len(entries) > declared:
        detail = (
            f"the {probe} declares {declared} entries but its census block lists {len(entries)}: a "
            f"line matching {entry_shape} that the census does not count is inside the block"
        )
        raise device_layer.ProbeError(detail)
    return entries


def parse_wakelock_count(dump: str, app_uid: int) -> int:
    """
    Count the wake locks a `dumpsys power` dump attributes to one UID.

    The dump's own `Wake Locks: size=<n>` census is required *and* the enumerated locks must account
    for it, for the reason `census_entry_block` gives. A dump that carries the census and no lock for
    this UID is a real zero.

    Every lock type is counted before the UID is applied, not `PARTIAL_WAKE_LOCK` alone. The census
    counts the whole device and this device's `Wake Locks: size=1` is a `DOZE_WAKE_LOCK`, so a
    partial-only scan could never be reconciled with the census and the census would go unchecked.
    The B2 budget is about locks the app holds, whatever their type, so widening the predicate to
    match the census also matches the budget.
    """
    entries = census_entry_block(
        dump,
        POWER_WAKELOCK_CENSUS_PATTERN,
        WAKE_LOCK_ENTRY_PATTERN,
        probe="power dump",
        census_line="`Wake Locks: size=`",
        entry_shape="`<TYPE>_WAKE_LOCK '<name>' ACQ=… (uid=<uid> …)`",
    )
    # The uid is matched as a whole token, not as a prefix of one: a lock line for uid 10026 contains
    # the text `uid=1002`, so a prefix test would attribute another process's wake lock to an app
    # whose own uid happens to be a prefix of it, and report a B2 failure the app did not cause.
    uid_field = re.compile(rf"\buid={app_uid}\b")
    return sum(1 for line in entries if uid_field.search(line))


def parse_alarm_count(dump: str, package: str = device_layer.PACKAGE_NAME) -> int:
    """
    Count the pending alarms a `dumpsys alarm` dump attributes to one package.

    As with the power and jobscheduler probes, the dump's own `<n> pending alarms:` census is
    required *and* the enumerated alarms must account for it, for the reason `census_entry_block`
    gives.
    """
    entries = census_entry_block(
        dump,
        ALARM_CENSUS_PATTERN,
        ALARM_ENTRY_PATTERN,
        probe="alarm dump",
        census_line="`pending alarms:`",
        entry_shape="`<TYPE> #<n>: Alarm{…}`",
    )
    # The package is matched as a whole token, not as a substring, for the reason the uid is: an alarm
    # owned by a package such as `…astronomicalclockswallpaper.debug2` contains this app's name as a
    # prefix, and counting it would report a B2 failure the app did not cause. The dump prints the
    # package as the last token inside `Alarm{...}`, so it is delimited there in the text.
    #
    # Attribution runs over the census block rather than the whole dump, which is what stops an alarm
    # that is *not* pending from counting as one: this device's `Past-due non-wakeup alarms:` section
    # lists fired alarms that still name their owner in the same `Alarm{...}` shape.
    package_field = re.compile(rf"(?<![\w.]){re.escape(package)}(?![\w.])")
    return sum(1 for line in entries if package_field.search(line))


def count_held_wakelocks(device: device_layer.AdbDevice, app_uid: int | None) -> int:
    """Count the wake locks of any type `dumpsys power` attributes to the app's UID."""
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

    The dump's own `Registered <n> jobs:` census is required *and* the enumerated jobs must account
    for it, for the reason `census_entry_block` gives. A dump that carries the census and no job for
    the package is a real zero.

    Every job line must also be attributable to a package. The census check counts entries, so a job
    line the attribution cannot parse would otherwise be counted towards the census, dropped from the
    per-package total, and reported as "the app holds no such job" — the silent drop this refusal
    replaces.
    """
    entries = census_entry_block(
        dump,
        JOBSCHEDULER_CENSUS_PATTERN,
        JOB_ENTRY_PATTERN,
        probe="jobscheduler dump",
        census_line="`Registered <n> jobs:`",
        entry_shape="`JOB [<namespace>:]#<uid>/<id>: <hash> <component>`",
    )
    unattributable = [line for line in entries if JOB_LINE_PATTERN.match(line) is None]
    if unattributable:
        detail = (
            f"the jobscheduler dump lists {len(unattributable)} job lines whose component cannot be "
            f"attributed to a package, so the per-package count would silently drop them; the first "
            f"is {unattributable[0].strip()!r}"
        )
        raise device_layer.ProbeError(detail)
    # Every entry line was shown above to match exactly once, so counting over the rejoined block is
    # the same as counting per line, and it keeps the pattern's own match object for the package.
    return sum(1 for match in JOB_LINE_PATTERN.finditer("\n".join(entries)) if match.group(1) == package)


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


def read_apk_sha256(device: device_layer.AdbDevice) -> str:
    """
    Read the SHA-256 of the APK as it is *installed on the device*, or refuse.

    The record's `apk_sha256` is the identity of the build a window measured, and every report row
    that aggregates windows is gated on it: `check_measurement_identity` refuses a set that mixes
    two. Hashing the host checkout's `app/build/outputs/apk/debug/app-debug.apk` answered a
    different question — the digest of the artifact on this machine, which need not be the build the
    device is running — and answered it with `None` on a machine that never built the APK, so a
    record could carry "unknown build" as though that were an identity.

    Both steps are required to answer: a package that is not installed has no `base.apk` to hash, and
    a device without `sha256sum` returns a shell error that is not a digest. Either is a refusal
    rather than a null, because a null is what the field must never carry for a window that ran.
    """
    digest = device.read_installed_apk_sha256()
    if digest is None:
        detail = (
            f"the SHA-256 of {device_layer.PACKAGE_NAME}'s installed base.apk could not be read, so the "
            f"record cannot say which build this window measured"
        )
        raise device_layer.ProbeError(detail)
    return digest


# adb presents a wirelessly-debugged device under more than one selector. An explicit connection is
# `host:port`; the devices adb discovers itself are published as mDNS service names shaped
# `adb-<serial>-<suffix>._adb-tls-connect._tcp` (the older pairing form is `_adb._tcp`). Neither
# names the hardware: the port and the service suffix belong to the transport and change with it, so
# a digest of either splits one device's records or merges two devices'.
TRANSPORT_SERVICE_MARKERS: Final = ("._adb-tls-connect._tcp", "._adb._tcp")


def is_transport_selector(serial: str) -> bool:
    """Return whether an adb serial is a transport selector rather than a hardware serial."""
    if ":" in serial:
        return True
    return serial.startswith("adb-") and any(marker in serial for marker in TRANSPORT_SERVICE_MARKERS)


def read_hardware_serial(device: device_layer.AdbDevice) -> str | None:
    """
    Read the device's own serial property, or None when no stable handle can be read.

    This is the identity behind a transport selector rather than a second opinion on a USB serial:
    `adb devices` reports a wirelessly-debugged device as `host:port` or as an mDNS service name,
    which are endpoints of the transport and not properties of the hardware. The serial property is
    the handle the device itself reports, and it survives a reconnect that changes either.
    """
    for prop in ("ro.serialno", "ro.boot.serialno"):
        try:
            output = device.run_adb(["shell", "getprop", prop])
        except (subprocess.SubprocessError, OSError):
            # One property being unreadable does not mean the other is: a device that does not
            # expose `ro.serialno` may still answer for `ro.boot.serialno`, and the refusal below
            # names the case where neither could be read.
            continue
        value = output.decode(errors="replace").strip()
        # `getprop` prints an empty line for an unset property, and some builds print `unknown`;
        # neither is a device handle, so neither is hashed as though it named one.
        if value and value != "unknown":
            return value
    return None


def device_identity(device: device_layer.AdbDevice) -> str:
    """
    Name the device a record was measured on, without naming the device.

    The serial is the only stable handle the harness has on which physical device a window ran on,
    and it cannot be recorded as it stands: `docs/device-testing.md` keeps the model, OEM, and serial
    off public surfaces, and `phase_report` prints every identity field. A salted digest distinguishes
    one device from another while disclosing neither, and it is what stops two devices' records from
    aggregating into one verdict.

    A `host:port` serial or an `adb-…._adb-tls-connect._tcp` service name is adb's wireless-debugging
    *transport selector*, not a device handle: the port and the service suffix change on every
    reconnect, and an address can be reused by another device. Hashing either would make one
    physical device's records look like several devices' — which `report` refuses as a mixed set —
    and two devices' records look like one. When the serial is a selector, the digest is taken from
    the hardware serial the device reports; when that cannot be read, the record is refused rather
    than stamped with a selector that says nothing about the device.
    """
    serial = device.serial
    if not serial:
        detail = "the target device reported no serial, so a record cannot say which device it measured"
        raise device_layer.ProbeError(detail)
    if is_transport_selector(serial):
        hardware_serial = read_hardware_serial(device)
        if hardware_serial is None:
            detail = (
                f"the target device was reached over the transport selector {serial!r}, and its own "
                "serial property could not be read, so a record cannot say which device it measured"
            )
            raise device_layer.ProbeError(detail)
        serial = hardware_serial
    return hashlib.sha256(f"{DEVICE_ID_SALT}{serial}".encode()).hexdigest()[:DEVICE_ID_CHARS]


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
    # The decode is guarded like the parse below it. An append interrupted mid-character leaves a
    # partial UTF-8 sequence, which `read_text` raises `UnicodeDecodeError` on — the same corrupt
    # state file the JSON branch names, failing one step earlier and as a traceback.
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        detail = f"{path} is not valid UTF-8 ({error}); the record file is truncated or corrupt"
        raise device_layer.ProbeError(detail) from error
    records: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError as error:
            # A truncated write is a state file the run can be told about, not a traceback: the
            # phases append as they go, so the last line is the one an interrupted run leaves short.
            # `JSONDecodeError` is caught here as the `ValueError` it is, and so is the other
            # `ValueError` `json` raises — a number with more digits than `int()` will convert.
            detail = f"{path}:{number} is not valid JSON ({error}); the record is truncated or corrupt"
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
    """Assemble one record, stamping the device and build identity every report row has to carry."""
    api_level = parse_api_level(device.run_adb(["shell", "getprop", "ro.build.version.sdk"]).decode(errors="replace"))
    revision_val = values.pop("revision", None)
    revision = str(revision_val) if revision_val is not None else read_source_revision()
    return Record(
        phase=phase,
        recorded_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        revision=revision,
        apk_sha256=read_apk_sha256(device),
        api_level=api_level,
        duration_seconds=duration_seconds,
        # The device is stamped here rather than passed in: every record has to carry it, so there is
        # no call site that could forget it.
        values={**values, "device_id": device_identity(device)},
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

    A capture that cannot be decoded is refused as a probe error. `decode_frame` raises
    `device_layer.ScreencapError`, which is this module's sibling in `device_layer` rather than one of
    the refusals `main` catches, so it would leave the CLI printing a traceback instead of the named
    `ERROR` a garbled capture deserves — and a truncated `screencap` payload is exactly the transport
    wobble this must survive as a report rather than as a stack trace.
    """
    try:
        _, _, pixels = device_layer.decode_frame(device.run_adb(["exec-out", "screencap"]))
    except device_layer.ScreencapError as error:
        detail = f"the captured screen could not be decoded into a frame: {device_layer.error_detail(error)}"
        raise device_layer.ProbeError(detail) from error
    # The shortest channel slice covers exactly the complete triples, so the outer zip is non-strict.
    stride = device_layer.PIXEL_STRIDE * LUMA_STRIDE
    luma = sorted(
        sum(weight * channel for weight, channel in zip(LUMA_WEIGHTS, rgb, strict=True))
        for rgb in zip(pixels[0::stride], pixels[1::stride], pixels[2::stride], strict=False)
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


def component_is_static(component: str) -> bool:
    """
    Report whether a home-wallpaper component is the platform's own static wallpaper engine.

    Arm B accepts a component on the strength of it not being this app's live service, which admits
    *another package's* live wallpaper: a third-party live service is neither this app nor a static
    image, and a window measured on one would be recorded as the stock or black baseline while
    draining whatever that service costs. Requiring the platform engine closes that, and it is the
    strongest statement the component supports — which static *image* the engine carries is a
    separate, undecidable question, so this predicate does not claim one.
    """
    return component_name(component) == device_layer.PLATFORM_STATIC_WALLPAPER


def verify_arm(device: device_layer.AdbDevice, arm: Arm, baseline: Baseline | None) -> str:
    """
    Refuse a window whose device does not match the arm it is about to be recorded against.

    The component is the fact that decides the arm, and it is decidable: the home slot names the live
    service when the app is bound and the platform's static engine when it is not. Arm B therefore
    requires the platform engine by name rather than accepting anything that is not this app's
    service — a third-party live wallpaper satisfies "not this app" and would be recorded as the
    stock or black baseline. Which *static* image a baseline carries is not decidable this way, and
    the screen's own luminance does not decide it either — this device's OEM stock wallpaper is a
    dark starfield, so a brightness threshold would refuse valid stock windows while still not
    separating the two baselines. The luminance is recorded with every window as an observation and
    is not used to admit one.
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
    if not component_is_static(component):
        detail = (
            f"arm B was requested but the bound wallpaper is {component_name(component)}, which is "
            f"neither this app's service nor the platform's static engine "
            f"({device_layer.PLATFORM_STATIC_WALLPAPER}); another package's live wallpaper is not a "
            f"baseline"
        )
        raise device_layer.ProbeError(detail)
    return component


def arm_conditions(device: device_layer.AdbDevice, arm: Arm, baseline: Baseline | None) -> dict[str, object]:
    """Describe the verified arm for the record, including the luminance recorded for it."""
    return {
        "arm": arm.value,
        "baseline": baseline.value if baseline else None,
        "wallpaper_component": device_layer.read_wallpaper_component(device.serial),
        "median_luma": round(median_luma(device), 2),
        "median_luma_note": MEDIAN_LUMA_NOTE,
    }


def sample_window_states(
    device: device_layer.AdbDevice,
    seconds: float,
    *,
    home_required: bool,
) -> tuple[list[bool | None], list[bool], list[bool | None], list[bool | None]]:
    """
    Sample the display state, the power source, and — for a visible window — the surface and keyguard.

    The display and the power source are sampled *through* the window rather than checked once before
    it, because either can change while it runs. The surface in front is the same kind of fact, and it
    was the one still checked only at the start. `device_layer.read_home_in_front` reads the foreground
    and the home package in two separate round trips, and says so itself: "a caller measuring a window
    must verify the surface at the points it requires rather than trusting this one answer for the
    whole window". Another app coming forward mid-window leaves the bound wallpaper component
    unchanged, so every other guard still passes and the window would be recorded as a home-screen
    B4/B5 measurement of a screen that is not the home screen — and, for B6, as a window on the live
    wallpaper while that wallpaper is not the surface being drawn.

    The keyguard is sampled alongside the surface for the same reason. `ensure_visible_home` reads it
    once before the window, but a screen that locks mid-window draws its lock surface over the home
    screen while the display stays lit and every other guard still passes; the window would then be
    recorded as a B4/B5/B6 measurement of the wallpaper the lock surface covers. A sample that shows
    the keyguard, or that cannot be read, refuses the window — the same three-valued rule the
    pre-window check uses.

    A hidden window turns the display off and has no surface to sample, so it passes
    `home_required=False` and gets empty lists back.
    """
    states: list[bool | None] = []
    on_battery: list[bool] = []
    homes: list[bool | None] = []
    locks: list[bool | None] = []
    remaining = seconds
    while remaining > 0:
        interval = min(SAMPLE_INTERVAL_SECONDS, remaining)
        time.sleep(interval)
        remaining -= interval
        states.append(device.read_screen_on())
        on_battery.append(device_on_own_battery(device))
        reading = f"  {seconds - remaining:6.0f}s: screen_on={states[-1]} on_battery={on_battery[-1]}"
        if home_required:
            homes.append(device.read_home_in_front())
            locks.append(device.read_keyguard_locked())
            reading += f" home_in_front={homes[-1]} keyguard_locked={locks[-1]}"
        print(reading, flush=True)
    return states, on_battery, homes, locks


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


def wait_home_in_front(
    device: device_layer.AdbDevice,
    *,
    sleep: Callable[[float], None] | None = None,
) -> bool | None:
    """
    Read back that the home screen is in front, bounded the way the service-log confirmations are.

    `show_home` sends an input event and reports nothing about what it did; an event the system drops
    leaves the previous app in front, and every other check in `ensure_visible_home` still passes —
    the display is on, the keyguard is not locked, and the bound wallpaper component is the one the
    arm requested. The surface measured would then be that other app's, and the wallpaper the window
    claims to measure would not be the wallpaper on screen. The launcher takes a moment to come
    forward, so this re-reads a bounded number of times rather than once.

    Returns None when no read succeeded, which the caller must treat as a refusal: "could not be
    read" is not "the home screen is there".
    """
    saw_not_in_front = False
    sleep_fn = sleep if sleep is not None else time.sleep
    for attempt in range(HOME_CONFIRM_ATTEMPTS):
        reading = device.read_home_in_front()
        if reading is True:
            return True
        # A `False` is the device saying some other package is in front, which no later reading
        # retracts; a `None` is a read that said nothing. They are kept apart so that a run of
        # unreadable dumps is not reported as "another app is in front", and vice versa.
        saw_not_in_front = saw_not_in_front or reading is False
        if attempt + 1 < HOME_CONFIRM_ATTEMPTS:
            sleep_fn(HOME_CONFIRM_INTERVAL_SECONDS)
    return False if saw_not_in_front else None


def ensure_visible_home(
    device: device_layer.AdbDevice,
    *,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """
    Put the device on the home screen with the display on, or refuse the window.

    A visible window means the home screen with the wallpaper showing. `wake_screen()` only confirms
    the display is lit — it does not dismiss a keyguard or bring the launcher forward — so a device
    that started locked, or with another app in front, would have its lock screen or that app measured
    while the bound component still matched the requested arm. The qualification harness establishes
    this state explicitly before its own screen-on phases, and this one now does too.

    The two checks after `show_home()` are what make the state *established* rather than requested,
    and they cover different failures: the keyguard read says no lock surface is covering the home
    screen, and the foreground read says the home screen is the thing in front at all. Neither
    implies the other — a device unlocked on another app's activity passes the first and fails the
    second — and both are three-valued, so an unreadable device is refused rather than assumed.

    The keyguard is also read *before* the device is touched at all, and a locked or unreadable one
    stops the phase there. `dismiss_keyguard()` below is a change to a shared device that this
    protocol never puts back, so the check that it was safe to make has to come first: read
    afterwards, it cannot tell a device that was already unlocked from one this call just unlocked,
    and the phase would pass its own guard having changed the device it was guarding.
    """
    if not device.wake_screen():
        detail = "the display could not be woken for a visible window"
        raise device_layer.ProbeError(detail)
    # The lock state is read *before* anything is changed, because `wm dismiss-keyguard` changes the
    # shared device and nothing here puts it back: the snapshot restores the display state, not the
    # lock state. Dismissing first and reading afterwards cannot tell "the device was already
    # unlocked" from "this call unlocked it", so a run started on a locked device would unlock it and
    # then pass its own guard. `scripts/device_qualification.py` refuses a locked device before
    # mutating for the same reason, and `docs/device-testing.md` says to start with the keyguard
    # unlocked.
    locked = device.read_keyguard_locked()
    if locked is None:
        detail = (
            "the keyguard state could not be read before the window was set up, so a locked device "
            "cannot be told from an unlocked one; refusing to dismiss a keyguard that may be there"
        )
        raise device_layer.ProbeError(detail)
    if locked:
        detail = (
            "the keyguard is showing, so the device is not in the state this phase measures in, and "
            "dismissing it would change a shared device that nothing here puts back; unlock the device "
            "and re-run"
        )
        raise device_layer.ProbeError(detail)
    device.dismiss_keyguard()
    device.show_home()
    if device.read_screen_on() is not True:
        detail = "the display did not read back as on while the home screen was being established"
        raise device_layer.ProbeError(detail)
    # The keyguard read is three-valued — locked, unlocked, and could not be determined — and only a
    # positive *unlocked* reading shows the home screen is what the window will measure. Treating an
    # unreadable dump as "not locked" is the same defect as reading an unparsable probe as a zero: a
    # locked device would have its lock screen measured for the whole window while the bound home
    # component still matched the requested arm.
    keyguard = device.read_keyguard_locked()
    if keyguard is True:
        detail = "the device is still locked after dismissing the keyguard, so the home screen is not displayed"
        raise device_layer.ProbeError(detail)
    if keyguard is None:
        detail = "the keyguard state could not be read, so it cannot be shown that the home screen is displayed"
        raise device_layer.ProbeError(detail)
    # `show_home()` above sent the event; this is what says it landed. The keyguard and the display
    # can both read as the visible home state wants while another app is still in front, because
    # neither of them names the surface: KEYCODE_HOME is an input event the system may drop, and a
    # dropped one leaves the previous app in front with the bound home component still matching the
    # requested arm. The readback is three-valued for the same reason the keyguard's is.
    in_front = wait_home_in_front(device, sleep=sleep)
    if in_front is False:
        detail = (
            f"another package is in front after KEYCODE_HOME: the device reports "
            f"{device.read_foreground_package()} where it resolves {device.read_home_package()} as home, "
            f"so the wallpaper is not the surface this window would measure"
        )
        raise device_layer.ProbeError(detail)
    if in_front is None:
        detail = "the foreground package could not be read, so it cannot be shown that the home screen is displayed"
        raise device_layer.ProbeError(detail)


def run_window(
    device: device_layer.AdbDevice,
    pid: int | None,
    minutes: float,
    *,
    screen_on: bool,
    clk_tck: int,
) -> tuple[Window, list[bool | None], list[bool], list[bool | None], list[bool | None]]:
    """Sample a bounded window, holding the requested display state for its whole duration."""
    if not screen_on:
        device.run_adb(["shell", "input", "keyevent", "KEYCODE_SLEEP"])
        time.sleep(2.0)
        # The sleep key event is a request, not the state: `device_layer.sleep_screen` reports that the
        # event was *delivered*, and a sleep that failed or merely lagged leaves the display on. The
        # first display sample is a whole sampling interval away from here, so a lag would be invisible
        # in the readings — every later one would be `False` while the start of the window was lit —
        # and B3 would be accepted for a window that was not screen-off throughout. This is the same
        # readback `wake_screen` makes for the opposite transition.
        if device.read_screen_on() is not False:
            detail = (
                "the display did not read back as off after the sleep key event, so the window would "
                "not be screen-off throughout"
            )
            raise device_layer.ProbeError(detail)
    start = take_battery_sample(device, pid, clk_tck)
    seconds = minutes * 60.0
    # A window whose display is on is a window on the home screen, so the surface in front is sampled
    # with the rest of the state; see `sample_window_states`.
    states, on_battery, homes, locks = sample_window_states(device, seconds, home_required=screen_on)
    end = take_battery_sample(device, pid, clk_tck)
    window = Window(start_sample=start, end_sample=end, duration_seconds=end.timestamp - start.timestamp)
    return window, states, on_battery, homes, locks


def phase_resources(device: device_layer.AdbDevice) -> Record:
    """Record B2: the wake locks of any type, alarms, and jobs the app holds, with their commands."""
    pid = wallpaper_pid(device)
    uid = read_process_uid(device, pid)
    print(f"$ adb -s {device.serial} shell pidof {device_layer.PACKAGE_NAME} -> {pid}")
    print(f"$ adb -s {device.serial} shell stat -c %u /proc/{pid} -> {uid}")
    if uid is None:
        detail = "the wallpaper has no live process, so its wake locks cannot be attributed"
        raise device_layer.ProbeError(detail)
    wake_locks = count_held_wakelocks(device, uid)
    alarms = count_alarms(device)
    jobs = count_jobs(device)
    # The count is only evidence for the all-type budget if the record says so: a legacy partial-only
    # census wrote the same `wake_locks` key without this stamp, and `report` will not pass it under
    # the current budget. See `battery_report.WAKE_LOCK_COVERAGE`.
    print(f"$ adb -s {device.serial} shell dumpsys power   (every wake-lock type, uid={uid}) -> {wake_locks}")
    print(f"$ adb -s {device.serial} shell dumpsys alarm   ({device_layer.PACKAGE_NAME}) -> {alarms}")
    print(f"$ adb -s {device.serial} shell dumpsys jobscheduler   ({device_layer.PACKAGE_NAME}) -> {jobs}")
    summary = read_batterystats_summary(device)
    print(f"  batterystats corroboration: {summary or 'no matching lines'}")
    record = build_record(
        device,
        "resources",
        0.0,
        wake_locks=wake_locks,
        wake_lock_coverage=WAKE_LOCK_COVERAGE,
        alarms=alarms,
        jobs=jobs,
        wallpaper_pid=pid,
        wallpaper_uid=uid,
        batterystats_summary=summary,
    )
    print(f"wake locks={wake_locks} (budget 0), alarms={alarms} (budget 0), jobs={jobs} (budget 0)")
    return record


def require_hidden_measurement(
    window: Window,
    states: Sequence[bool | None],
    on_battery: Sequence[bool],
) -> float:
    """
    Refuse a hidden window that is not a B3 measurement, returning the CPU it measured.

    A window whose display came on, that saw a charger, or whose process CPU could not be read is not
    the measurement B3 is defined on. These checks run after the phase's restore has been attempted,
    so each refusal is raised from here rather than beside that path: the caller runs this inside the
    `try` that persists the restore problems, and a refusal means the phase aborted with the device
    possibly still changed.
    """
    if not states or not all(state is False for state in states):
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
    return cpu_pct


def require_visible_measurement(  # noqa: PLR0913 — the window and the four state samples are all inputs
    window: Window,
    states: Sequence[bool | None],
    on_battery: Sequence[bool],
    home_in_front: Sequence[bool | None],
    arm: Arm,
    *,
    keyguard_locked: Sequence[bool | None],
) -> float:
    """
    Refuse a visible window that is not a B4/B5 measurement, returning its discharge rate.

    `Window.discharge_rate_ua` is one of the refusals — it raises when the pack gained charge across
    the window — so it is read here, inside the same path as the others, rather than at the print that
    happens to need it first.

    The surface in front is checked over the whole window, not only before it: the guard that runs
    before the window establishes the home screen, and another app coming forward after that leaves
    the bound wallpaper component unchanged, so every other reading here would still pass while the
    screen measured is not the home screen. A sample that could not be read is a refusal for the same
    reason it is in `ensure_visible_home`.

    The keyguard is checked over the whole window for the same reason: a screen that locks mid-window
    covers the home screen with its lock surface while the display stays on. A sample that shows the
    keyguard or could not be read refuses the window, the same three-valued rule the pre-window check
    applies.
    """
    if states and not all(state is True for state in states):
        detail = f"the display was not on for the whole visible window: {states}"
        raise device_layer.ProbeError(detail)
    if home_in_front and not all(state is True for state in home_in_front):
        detail = f"the home screen was not the surface in front for the whole visible window: {home_in_front}"
        raise device_layer.ProbeError(detail)
    if keyguard_locked and not all(state is False for state in keyguard_locked):
        detail = f"the keyguard was showing or unreadable inside the visible window: {keyguard_locked}"
        raise device_layer.ProbeError(detail)
    charging = power_source_problem(on_battery)
    if charging is not None:
        detail = f"this is not a discharge measurement: {charging}"
        raise device_layer.ProbeError(detail)
    if arm is Arm.LIVE and window.cpu_pct is None:
        detail = "the live wallpaper's CPU counters could not be read over a visible window"
        raise device_layer.ProbeError(detail)
    return window.discharge_rate_ua


def require_memory_measurement(  # noqa: PLR0913 — the four state samples and the two battery samples
    states: Sequence[bool | None],
    on_battery: Sequence[bool],
    home_in_front: Sequence[bool | None],
    before: BatterySample,
    after: BatterySample,
    *,
    keyguard_locked: Sequence[bool | None],
) -> int:
    """
    Refuse a memory window that is not a B6 measurement, returning the total-PSS growth in kB.

    B6 is defined over a window on the live wallpaper, so the surface in front is checked over the
    whole window for the reason `require_visible_measurement` gives: a different app in front means
    the wallpaper is not the surface being drawn, and the PSS growth recorded would be the growth of
    a wallpaper that was not on screen for the window the record claims. The keyguard is checked over
    the whole window for the same reason: a lock surface over the home screen means the wallpaper is
    not the surface being drawn, and a sample that shows it or cannot be read refuses the window.
    """
    if states and not all(state is True for state in states):
        detail = f"the display was not on for the whole memory window: {states}"
        raise device_layer.ProbeError(detail)
    if home_in_front and not all(state is True for state in home_in_front):
        detail = f"the home screen was not the surface in front for the whole memory window: {home_in_front}"
        raise device_layer.ProbeError(detail)
    if keyguard_locked and not all(state is False for state in keyguard_locked):
        detail = f"the keyguard was showing or unreadable inside the memory window: {keyguard_locked}"
        raise device_layer.ProbeError(detail)
    charging = power_source_problem(on_battery)
    if charging is not None:
        detail = f"this window is not a valid measurement: {charging}"
        raise device_layer.ProbeError(detail)
    if before.total_pss_kb is None or after.total_pss_kb is None:
        detail = "a total-PSS sample could not be parsed, so growth has no value"
        raise device_layer.ProbeError(detail)
    return after.total_pss_kb - before.total_pss_kb


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
            pid = wallpaper_pid(device)
            snapshot.pin()
            # The decision is recorded before the override is applied: `cmd battery unplug` can take
            # effect and still fail on the way back, and a restore that read the flag afterwards would
            # skip the reset and leave the shared device simulated.
            power_source = power_mode_for(device)
            simulated = power_source == POWER_SOURCE_SIMULATED
            apply_power_mode(device, power_source)
            window, states, on_battery, _, _ = run_window(device, pid, minutes, screen_on=False, clk_tck=clk_tck)
        finally:
            problems = undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
        # The acceptance checks and the record build sit inside this `try`, after the restore, rather
        # than after the `except`: a window this phase refuses once the restore has already run is
        # still an abort, and the restore problems it collected have to reach disk with it instead of
        # leaving the shared device changed with nothing saying so. That is why the checks are a
        # function of their own — they belong to that path rather than sitting beside it.
        cpu_pct = require_hidden_measurement(window, states, on_battery)
        print(f"hidden window {window.duration_seconds:.1f}s: screen_on readings={states}")
        print(f"CPU {cpu_pct:.4f}% of one core against the {MAX_HIDDEN_CPU_PCT}% budget")
        record = build_record(
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
            restore_problems=problems,
        )
    except Exception as error:
        emit_aborted_restore_record(recorder, device, "hidden", error, problems)
        raise
    return record


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
            pid = wallpaper_pid(device) if arm is Arm.LIVE else None
            conditions = arm_conditions(device, arm, baseline)
            print(f"arm guard admitted {conditions['wallpaper_component']} (median luma {conditions['median_luma']})")
            snapshot.pin()
            # The decision is recorded before the override is applied: `cmd battery unplug` can take
            # effect and still fail on the way back, and a restore that read the flag afterwards would
            # skip the reset and leave the shared device simulated.
            power_source = power_mode_for(device)
            simulated = power_source == POWER_SOURCE_SIMULATED
            apply_power_mode(device, power_source)
            window, states, on_battery, homes, locks = run_window(device, pid, minutes, screen_on=True, clk_tck=clk_tck)
        finally:
            problems = undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
        # The acceptance checks and the record build sit inside this `try`, after the restore, rather
        # than after the `except`: a window this phase refuses once the restore has already run is
        # still an abort, and the restore problems it collected have to reach disk with it instead of
        # leaving the shared device changed with nothing saying so. That is why the checks are a
        # function of their own — they belong to that path rather than sitting beside it.
        discharge_rate_ua = require_visible_measurement(window, states, on_battery, homes, arm, keyguard_locked=locks)
        print(f"window {window.duration_seconds:.1f}s: discharge {window.discharge_uah} uAh")
        print(f"discharge rate {discharge_rate_ua:.0f} uA, CPU {window.cpu_pct}% of one core")
        record = build_record(
            device,
            "visible",
            window.duration_seconds,
            window_minutes=minutes,
            discharge_uah=window.discharge_uah,
            discharge_rate_ua=discharge_rate_ua,
            cpu_pct=window.cpu_pct,
            charge_counter_start_uah=window.start_sample.charge_counter_uah,
            charge_counter_end_uah=window.end_sample.charge_counter_uah,
            clk_tck=clk_tck,
            power_source=power_source,
            screen_on_samples=states,
            on_battery_samples=on_battery,
            home_in_front_samples=homes,
            keyguard_locked_samples=locks,
            restore_problems=problems,
            **conditions,
        )
    except Exception as error:
        emit_aborted_restore_record(recorder, device, "visible", error, problems)
        raise
    return record


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
    homes: list[bool | None] = []
    locks: list[bool | None] = []
    power_source = POWER_SOURCE_DEVICE
    simulated = False
    # The restore sits in its own `finally` inside the outer `try`, so it runs before the abort is
    # handled and the problems it collected are in hand when the record is written.
    try:
        try:
            ensure_visible_home(device)
            verify_arm(device, Arm.LIVE, None)
            pid = wallpaper_pid(device)
            snapshot.pin()
            # The decision is recorded before the override is applied: `cmd battery unplug` can take
            # effect and still fail on the way back, and a restore that read the flag afterwards would
            # skip the reset and leave the shared device simulated.
            power_source = power_mode_for(device)
            simulated = power_source == POWER_SOURCE_SIMULATED
            apply_power_mode(device, power_source)
            before = take_battery_sample(device, pid, clk_tck)
            seconds = minutes * 60.0
            states, on_battery, homes, locks = sample_window_states(device, seconds, home_required=True)
            after = take_battery_sample(device, pid, clk_tck)
        finally:
            problems = undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
        # The acceptance checks and the record build sit inside this `try`, after the restore, rather
        # than after the `except`: a window this phase refuses once the restore has already run is
        # still an abort, and the restore problems it collected have to reach disk with it instead of
        # leaving the shared device changed with nothing saying so. That is why the checks are a
        # function of their own — they belong to that path rather than sitting beside it.
        growth_kb = require_memory_measurement(states, on_battery, homes, before, after, keyguard_locked=locks)
        print(f"total PSS {before.total_pss_kb} -> {after.total_pss_kb} kB over {minutes:.1f} min")
        print(f"growth {growth_kb} kB against the {MAX_PSS_GROWTH_KB} kB budget")
        record = build_record(
            device,
            "memory",
            # The span the samples were actually taken over, not the length that was requested. Both
            # other windowed phases record the measured span, and the report reads this field as a
            # measurement whose length has to hold up: a memory record carrying the requested value
            # instead would assert a window length it never measured.
            after.timestamp - before.timestamp,
            window_minutes=minutes,
            pss_start_kb=before.total_pss_kb,
            pss_end_kb=after.total_pss_kb,
            pss_growth_kb=growth_kb,
            pss_budget_kb=MAX_PSS_GROWTH_KB,
            clk_tck=clk_tck,
            power_source=power_source,
            screen_on_samples=states,
            on_battery_samples=on_battery,
            home_in_front_samples=homes,
            keyguard_locked_samples=locks,
            restore_problems=problems,
        )
    except Exception as error:
        emit_aborted_restore_record(recorder, device, "memory", error, problems)
        raise
    return record


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

    A window that could not restore the device stops the run where it stands, after its record is on
    disk. The windows are one measurement of one device in one state, and the next window would be
    measured under whatever the failed restore left behind — a power override still applied, a display
    that never returned to the state it was found in. Reporting that at the end of the run, as `main`
    does with the accumulated problems, is reporting it after the later windows have already been
    taken on it.

    The run is bounded before the first window opens. `--runs` has a count bound and `--minutes` a
    length bound, but neither bounds this invocation on its own: a count at the floor with a window at
    the ceiling is hours past what `docs/battery-budget.md` allows an invocation to take, and refusing
    it here is refusing it before any window has been opened.
    """
    if runs * minutes > MAXIMUM_PILOT_MINUTES:
        detail = (
            f"{runs} windows of {minutes:g} minutes is {runs * minutes:g} minutes, longer than the "
            f"{MAXIMUM_PILOT_MINUTES:g} minutes a pilot invocation will hold the device for"
        )
        raise device_layer.ProbeError(detail)
    rates: list[float] = []
    cpu_values: list[float] = []
    for run in range(1, runs + 1):
        print(f"\n--- pilot window {run}/{runs} ---")
        record = phase_visible(device, arm=arm, baseline=baseline, minutes=minutes, recorder=recorder)
        record.values["run"] = run
        record.values["pilot"] = True
        record.values["runs"] = runs
        recorder.emit(record)
        # The window is persisted before the run is stopped, so stopping here loses no evidence. A
        # window whose restore failed left the device in a state this run no longer controls — a power
        # override that did not reset, a display that did not return to the state it was found in —
        # and every later window would be measured on it. `main` reports the accumulated problems, but
        # only once the whole pilot has returned, which for a full pilot is two hours of windows.
        restore_problems = record.values.get("restore_problems")
        if isinstance(restore_problems, list) and restore_problems:
            named = "; ".join(str(problem) for problem in restore_problems)
            detail = (
                f"pilot window {run} of {runs} did not restore the device ({named}), so the remaining "
                f"windows would be measured on a device this run does not control; stopping after "
                f"{run} window(s)"
            )
            raise device_layer.ProbeError(detail)
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
    Parse a finite, strictly positive duration within the bound a phase will hold the device for.

    `inf` is refused along with zero and the negatives: it is not a large window, it is a number the
    phase can never reach, and `sample_window_states` would subtract its 30-second interval from it
    forever, turning a bounded phase into an unbounded one. `nan` compares false against everything, so
    it slips past a `<= 0` test and has to be refused by name.

    Finiteness is not enough on its own either, and neither is the arithmetic the loop needs: a window
    is accepted against `MAXIMUM_WINDOW_MINUTES`, which is the rule that actually bounds the run.
    `1e308` minutes overflows to `inf` seconds at `minutes * 60.0`, and `1e15` makes progress and would
    still be about two billion years of samples — so the one thing every too-large duration has in
    common is being larger than a window a phase will hold a device for.
    """
    value = float(raw)
    if not math.isfinite(value):
        message = f"{raw} is not a finite duration"
        raise argparse.ArgumentTypeError(message)
    if value <= 0:
        message = f"{raw} is not a positive duration"
        raise argparse.ArgumentTypeError(message)
    if value > MAXIMUM_WINDOW_MINUTES:
        message = (
            f"{raw} minutes is longer than the {MAXIMUM_WINDOW_MINUTES / 60.0:.0f} hours a measurement "
            "phase will hold the device for; a longer window is not a longer measurement, and the "
            "sampling loop would be running on the shared device long after the criterion was met"
        )
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


def nonblank_reason(raw: str) -> str:
    """
    Parse the reason a run declares one of the document's baselines unavailable.

    The declaration is what earns the exemption, so a blank one declares nothing and is refused rather
    than stored: an empty string would print a device-limited line that names no reason, which reads as
    exactly the silent omission the declaration exists to replace.
    """
    reason = raw.strip()
    if not reason:
        message = "an empty reason declares nothing; say why the device cannot set the baseline"
        raise argparse.ArgumentTypeError(message)
    return reason


def positive_int(raw: str) -> int:
    """Parse a strictly positive integer for the command line."""
    value = int(raw)
    if value <= 0:
        message = f"{raw} is not a positive count"
        raise argparse.ArgumentTypeError(message)
    return value


def positive_int_between(minimum: int, maximum: int) -> Callable[[str], int]:
    """
    Build a count parser that refuses a value outside the range the phase can use.

    Both ends are the same rule stated twice: a count the phase cannot make a measurement from is
    refused before the device work rather than after it. The lower bound is what a noise floor needs,
    and the upper one is what a phase will hold the device for.
    """

    def parse_count(raw: str) -> int:
        value = positive_int(raw)
        if value < minimum:
            message = (
                f"{raw} windows is below the {minimum} a noise floor needs, so the run would measure "
                "no floor at all; the refusal is here rather than after the windows have been run"
            )
            raise argparse.ArgumentTypeError(message)
        if value > maximum:
            message = (
                f"{raw} windows is more than the {maximum} a pilot will hold the device for; a noise "
                "floor does not need that many, and the phase would still be opening windows long "
                "after the floor was established"
            )
            raise argparse.ArgumentTypeError(message)
        return value

    return parse_count


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
    pilot.add_argument(
        "--runs",
        type=positive_int_between(MIN_PAIRED_SAMPLES, MAXIMUM_PILOT_RUNS),
        default=MIN_PAIRED_SAMPLES,
        help=(
            f"Windows to run (between {MIN_PAIRED_SAMPLES}, which is what a noise floor needs, and "
            f"{MAXIMUM_PILOT_RUNS}, which is what the device will be held for)"
        ),
    )

    memory = subcommands.add_parser("memory", help="B6: total-PSS growth over a long window")
    add_window_arguments(memory, MINIMUM_MEMORY_MINUTES)

    report = subcommands.add_parser("report", help="Aggregate the records the phases wrote")
    report.add_argument(
        "--black-unavailable",
        type=nonblank_reason,
        default=None,
        metavar="REASON",
        help=(
            "Declare that this device cannot set a static wallpaper, so B4's black baseline is "
            "device-limited rather than an unmeasured criterion"
        ),
    )
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
            return phase_report(records, black_unavailable=args.black_unavailable)
        except device_layer.ProbeError as error:
            print(f"ERROR: {device_layer.error_detail(error)}", file=sys.stderr)
            return 1
    recorder = Recorder(state_dir)
    try:
        # Target selection is inside the guard because it runs `adb devices`: a missing or wedged adb
        # is the most common way this harness fails, and outside the guard it printed a traceback
        # rather than the named `ERROR` every other failure gets. `select_target_serial` reports an
        # unusable selection by exiting, which is a `SystemExit` and is not caught here, so that
        # behaviour is unchanged.
        serial = device_layer.select_target_serial(args.serial)
        device = device_layer.AdbDevice(serial)
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
