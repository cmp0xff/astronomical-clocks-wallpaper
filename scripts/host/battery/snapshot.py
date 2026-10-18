# SPDX-License-Identifier: Apache-2.0
"""Device snapshot, power-mode pinning, restore verification, and JSONL record persistence."""

import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Self

from host.device import probes as device_probes

# A bounded digit run: `int()` raises above ~4300 digits, and no field this harness reads is 18 wide.
# The trailing lookahead makes the bound a refusal rather than a truncation of a longer run.
DIGITS: Final = r"\d{1,18}(?!\d)"

POWERED_LINE_PATTERN: Final = re.compile(r"^\s*(\w+) powered:\s*(true|false)\s*$", re.MULTILINE)

# All four sources are required: a truncated dump must not read as "the remaining one is false, so
# the device is on battery".
EXPECTED_POWER_SOURCES: Final = ("AC", "USB", "Wireless", "Dock")

# A signed decimal integer, bounded so `int()` cannot raise on it. `isdigit()` accepts `"²"`, which
# `int()` refuses; the sign is accepted because brightness is legitimately `-1` in auto mode.
DECIMAL_INT_PATTERN: Final = re.compile(rf"[+-]?{DIGITS}")

# The only reading that means "never set"; a settings service that did not answer also fails to look
# like a decimal, and conflating the two loses a value the restore was supposed to put back.
UNSET_SETTING_TEXT: Final = "null"

DEFAULT_STATE_DIR: Final = Path("build/reports/battery")

RECORD_FILE_NAME: Final = "records.jsonl"

PINNED_BRIGHTNESS: Final = 128

# Stay awake on every power source; see DeviceStateSnapshot.pin.
STAY_ON_ALWAYS: Final = 7

# A whole hour, so it comfortably exceeds the longest documented window (30 minutes) rather
# than expiring at the same instant the window closes.
LONG_SCREEN_TIMEOUT_MS: Final = 3_600_000

RESTORE_SETTLE_SECONDS: Final = 2.0

# A genuinely discharging device has a live gauge; `cmd battery unplug` can freeze it, so which mode
# a window ran under is part of the measurement's identity.
POWER_SOURCE_DEVICE: Final = "device"

POWER_SOURCE_SIMULATED: Final = "simulated"


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

        The counter must have fallen: a pack that gained charge is not a discharge measurement, and a
        negative draw would enter the comparison as a cheaper live arm.
        """
        if self.duration_seconds <= 0:
            detail = "a window with no elapsed time has no discharge rate"
            raise device_probes.ProbeError(detail)
        if self.discharge_uah < 0:
            detail = (
                f"the charge counter rose by {-self.discharge_uah} uAh across the window "
                f"({self.start_sample.charge_counter_uah} to {self.end_sample.charge_counter_uah} uAh), "
                "so the pack gained charge and this is not a discharge measurement"
            )
            raise device_probes.ProbeError(detail)
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
            raise device_probes.ProbeError(detail)
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

    The display's power state is included: a hidden window turns it off.
    """

    def __init__(  # noqa: PLR0913
        self,
        device: device_probes.AdbDevice,
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
        device: device_probes.AdbDevice,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> Self:
        """
        Read the state a measurement phase pins, including the display's power state.

        Four settings are required, because the restore can only put back what was read. The stay-awake
        setting may legitimately be unset (`None`, restored by deleting the key), but an unreadable
        reading is refused before anything is pinned.
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
        # A device reporting itself on battery ignores the plugged-in stay-awake setting, which is the
        # state a discharge simulation creates; staying awake on every source keeps the window lit.
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
        problems.extend(
            problem
            for description, command, namespace, key, expected in steps
            if expected is not None
            for problem in self._apply(description, command, namespace, key, expected)
        )
        problems.extend(self._restore_stay_on())
        return problems

    def _restore_stay_on(self) -> list[str]:
        """Put the stay-awake setting back, including removing it when it was unset."""
        if self.stay_on is None:
            try:
                self.device.run_adb(["shell", "settings", "delete", "global", "stay_on_while_plugged_in"])
            except (subprocess.SubprocessError, OSError) as error:
                return [f"stay-awake restore failed: {device_probes.error_detail(error)}"]
            # Guarded so a transport failure cannot skip the display restore. This reader is used
            # rather than `read_int_setting` because that one reads unreadable text as `None`, which
            # would report an unverifiable delete as a clean restore.
            try:
                remaining = read_unsettable_int_setting(self.device, "global", "stay_on_while_plugged_in")
            except (subprocess.SubprocessError, OSError, device_probes.ProbeError) as error:
                return [f"stay-awake restore could not be verified: {device_probes.error_detail(error)}"]
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
            return [f"{description} restore failed: {device_probes.error_detail(error)}"]
        # Guarded too, so a transport failure cannot skip the display restore.
        try:
            actual = read_int_setting(self.device, namespace, key)
        except (subprocess.SubprocessError, OSError) as error:
            return [f"{description} restore could not be verified: {device_probes.error_detail(error)}"]
        if actual != expected:
            return [f"{namespace}/{key} did not read back as {expected}"]
        return []

    def _restore_display(self) -> list[str]:
        """
        Return the display to the power state it was found in.

        Reads are guarded so a raise cannot discard the problems the settings steps collected or skip
        the sleep.
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
            return f"the display state could not be read to restore it: {device_probes.error_detail(error)}"
        self.device.sleep_screen()
        try:
            if self.device.read_screen_on() is not False:
                return "the display was off before the phase and could not be put back to sleep"
        except (subprocess.SubprocessError, OSError) as error:
            return f"the display state could not be read back after the sleep: {device_probes.error_detail(error)}"
        return None


def undo_phase_changes(
    device: device_probes.AdbDevice,
    snapshot: DeviceStateSnapshot,
    prior_power: list[str] | None = None,
    *,
    simulated: bool = False,
) -> list[str]:
    """
    Undo a phase's device changes, isolating each step so none can skip the others.

    The battery reset runs only when this phase applied the override. Reset and readback are separate
    steps: sharing a guard let an unparsable readback escape and skip the settings restore.
    """
    problems: list[str] = []
    if simulated:
        try:
            reset_battery_simulation(device)
        except (subprocess.SubprocessError, OSError) as error:
            problems.append(f"battery simulation reset failed: {device_probes.error_detail(error)}")
        if prior_power is not None:
            try:
                restored = power_source_restored(device, prior_power)
            except (subprocess.SubprocessError, OSError, device_probes.ProbeError) as error:
                problems.append(f"the restored power sources could not be read: {device_probes.error_detail(error)}")
            else:
                if not restored:
                    problems.append("the reported power sources did not return to their pre-phase state")
    try:
        problems.extend(snapshot.restore())
    except (subprocess.SubprocessError, OSError) as error:
        problems.append(f"device settings restore failed: {device_probes.error_detail(error)}")
    return problems


def read_setting_text(device: device_probes.AdbDevice, namespace: str, key: str) -> str:
    """Read one device setting as the text `settings get` printed for it, stripped."""
    return device.run_adb(["shell", "settings", "get", namespace, key]).decode(errors="replace").strip()


def read_int_setting(device: device_probes.AdbDevice, namespace: str, key: str) -> int | None:
    """
    Read one integer device setting, or None when it is unset or not a decimal integer.

    `None` keeps the restore running and fails its comparison, but it conflates unset with unreadable;
    `read_unsettable_int_setting` is the capture reader that separates them.
    """
    return parse_signed_int(read_setting_text(device, namespace, key))


def read_unsettable_int_setting(device: device_probes.AdbDevice, namespace: str, key: str) -> int | None:
    """
    Read one integer device setting that is legitimately unset, refusing an unreadable reading.

    `null` and a decimal integer are the only two readings that say anything. Anything else is a
    settings service that did not answer; treating it as "unset" would delete a real value and report
    a clean restore, so the phase stops before it pins anything.
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
        raise device_probes.ProbeError(detail)
    return value


def require_int_setting(device: device_probes.AdbDevice, namespace: str, key: str) -> int:
    """
    Read one device setting the pre-phase capture cannot do without.

    `pin()` overwrites it and the restore can only put back what was captured, so an unreadable value
    stops the phase before it touches anything.
    """
    value = read_int_setting(device, namespace, key)
    if value is None:
        detail = (
            f"{namespace}/{key} could not be read before the phase, so whatever value it had could not "
            "be restored; refusing to pin a setting whose original value is unknown"
        )
        raise device_probes.ProbeError(detail)
    return value


def set_airplane_mode(device: device_probes.AdbDevice, *, enabled: bool) -> None:
    """Turn airplane mode on or off through the connectivity service."""
    device.run_adb(["shell", "cmd", "connectivity", "airplane-mode", "enable" if enabled else "disable"])


def require_complete_power_sources(lines: list[str]) -> None:
    """
    Refuse a power-source reading missing any of the four sources.

    One `powered:` line is not a reading: `Dock powered: false` alone looks like a device on battery,
    and the discharge measurement's identity rests on this reading.
    """
    present = {match.group(1) for match in POWERED_LINE_PATTERN.finditer("\n".join(lines))}
    missing = [source for source in EXPECTED_POWER_SOURCES if source not in present]
    if missing:
        detail = (
            f"the battery dump carried no {' or '.join(missing)} powered: field, so the power source is "
            "not a complete reading; refusing to infer the device's state from the fields that remain"
        )
        raise device_probes.ProbeError(detail)


def read_power_source_lines(device: device_probes.AdbDevice) -> list[str]:
    """Read the battery dump's power-source lines, which `cmd battery unplug` rewrites."""
    output = device.run_adb(["shell", "dumpsys", "battery"]).decode(errors="replace")
    lines = [line.strip() for line in output.splitlines() if POWERED_LINE_PATTERN.match(line.strip())]
    require_complete_power_sources(lines)
    return lines


def power_source_restored(device: device_probes.AdbDevice, before: list[str]) -> bool:
    """
    Report whether the power sources match their pre-simulation reading.

    That separates "the override is gone" from "this device happens to be on its own battery", which
    look identical in the dump.
    """
    return read_power_source_lines(device) == before


def power_lines_show_battery(lines: list[str]) -> bool:
    """
    Report whether all of a dump's `powered:` fields read false, i.e. the device is on battery.

    Every source is required: a probe that stopped reporting some must not look like a discharge.
    """
    require_complete_power_sources(lines)
    return all(match.group(2) == "false" for match in POWERED_LINE_PATTERN.finditer("\n".join(lines)))


def device_on_own_battery(device: device_probes.AdbDevice) -> bool:
    """Report whether the device is already discharging on its own battery rather than on a charger."""
    return power_lines_show_battery(read_power_source_lines(device))


def power_mode_for(device: device_probes.AdbDevice) -> str:
    """
    Name the discharge state a window needs, reading the device without changing it.

    A genuinely discharging device has the live gauge the measurement wants, so the override is chosen
    only on a charger. Deciding is separate from applying because the apply call can fail after the
    device obeyed; the recorded mode tells the restore a reset is owed.
    """
    return POWER_SOURCE_DEVICE if device_on_own_battery(device) else POWER_SOURCE_SIMULATED


def apply_power_mode(device: device_probes.AdbDevice, mode: str) -> None:
    """Apply the mode `power_mode_for` named, which its caller has already recorded."""
    if mode == POWER_SOURCE_SIMULATED:
        simulate_unplug(device)


def parse_signed_int(raw: str) -> int | None:
    """
    Read a device's signed decimal integer, or None when the text is not one.

    A parse, not `lstrip("-").isdigit()`: that guard accepted `"--5"` and `"²"`, which `int()` raises
    on.
    """
    return int(raw) if DECIMAL_INT_PATTERN.fullmatch(raw) else None


def simulate_unplug(device: device_probes.AdbDevice) -> None:
    """Make the device report itself as running on battery while it stays on USB for telemetry."""
    device.run_adb(["shell", "cmd", "battery", "unplug"])


def reset_battery_simulation(device: device_probes.AdbDevice) -> None:
    """Clear any `cmd battery` override so the device reports its real state again."""
    device.run_adb(["shell", "cmd", "battery", "reset"])


def record_path(state_dir: Path) -> Path:
    """Return the JSON Lines file the phase records accumulate in."""
    return state_dir / RECORD_FILE_NAME


def append_record(state_dir: Path, record: Record) -> Path:
    """Append one record to the state file, creating the directory when it is missing."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = record_path(state_dir)
    payload: dict[str, object] = {"phase": record.phase, "recorded_at": record.recorded_at, "revision": record.revision}
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
        raise device_probes.ProbeError(detail)
    # An append interrupted mid-character leaves a partial UTF-8 sequence; name it like a corrupt line.
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        detail = f"{path} is not valid UTF-8 ({error}); the record file is truncated or corrupt"
        raise device_probes.ProbeError(detail) from error
    records: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError as error:
            # A truncated append leaves the last line short; `ValueError` also covers an integer
            # `json` cannot convert. Name it as a corrupt record, not a traceback.
            detail = f"{path}:{number} is not valid JSON ({error}); the record is truncated or corrupt"
            raise device_probes.ProbeError(detail) from error
        if not isinstance(parsed, dict):
            detail = f"{path}:{number} is not a JSON object"
            raise device_probes.ProbeError(detail)
        records.append(parsed)
    return records


class Recorder:
    """
    Writes each record as it is produced, and keeps them for the caller.

    Persisting per record makes a phase resumable: an interrupted run leaves its completed records on
    disk.
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
