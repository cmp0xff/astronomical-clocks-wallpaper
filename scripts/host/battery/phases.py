# SPDX-License-Identifier: Apache-2.0
"""Windowed battery measurement phases (`resources`, `hidden`, `visible`, `memory`, `pilot`)."""

import math
import time
from collections.abc import Callable, Sequence
from typing import Final, TypedDict

from host import contract
from host.battery import probes, stats
from host.battery import snapshot as battery_snapshot
from host.device import probes as device_probes

# The launcher takes a moment to come forward after KEYCODE_HOME, so the readback is bounded like the
# service-log confirmations: five reads half a second apart.
HOME_CONFIRM_ATTEMPTS: Final = 5

HOME_CONFIRM_INTERVAL_SECONDS: Final = 0.5

SAMPLE_INTERVAL_SECONDS: Final = 30.0

# `docs/battery-budget.md` requires that "no single invocation, and above all no single window, blocks
# for hours": the longest documented window is thirty minutes and the six-pair pilot took ninety.
MAXIMUM_WINDOW_MINUTES: Final = 45.0

MAXIMUM_PILOT_MINUTES: Final = 120.0

# A count alone does not bound the invocation; `phase_pilot` checks the product before the first window.
MAXIMUM_PILOT_RUNS: Final = int(MAXIMUM_PILOT_MINUTES // stats.MINIMUM_WINDOW_MINUTES)


def verify_arm(device: device_probes.AdbDevice, arm: stats.Arm, baseline: stats.Baseline | None) -> str:
    """
    Refuse a window whose device does not match the arm it is about to be recorded against.

    Arm B requires the platform engine by name: a third-party live wallpaper satisfies "not this app"
    and would be recorded as the baseline. Which static image is bound is not decidable here, and the
    recorded luminance does not gate the window.
    """
    component = device_probes.read_wallpaper_component(device.serial)
    if arm is stats.Arm.LIVE:
        if not probes.component_is_live(component):
            detail = (
                f"arm A was requested but the home wallpaper is {probes.component_name(component)}, not "
                f"{contract.SERVICE_NAME}"
            )
            raise device_probes.ProbeError(detail)
        return component
    if baseline is None:
        detail = "arm B needs --baseline stock or --baseline black to say which static wallpaper is bound"
        raise device_probes.ProbeError(detail)
    if probes.component_is_live(component):
        detail = f"arm B was requested but the live service is still bound as {probes.component_name(component)}"
        raise device_probes.ProbeError(detail)
    if not probes.component_is_static(component):
        detail = (
            f"arm B was requested but the bound wallpaper is {probes.component_name(component)}, which is "
            f"neither this app's service nor the platform's static engine "
            f"({contract.PLATFORM_STATIC_WALLPAPER}); another package's live wallpaper is not a "
            f"baseline"
        )
        raise device_probes.ProbeError(detail)
    return component


class ArmConditions(TypedDict):
    """The verified arm a visible window was recorded under."""

    arm: str
    baseline: str | None
    wallpaper_component: str
    median_luma: float
    median_luma_note: str


class PilotFloorValues(TypedDict, total=False):
    """The pilot floor record's fields; the noise-floor channels are absent when unmeasured."""

    run: int
    pilot: bool
    runs: int
    discharge_channel_measured: bool
    baseline_mean_rate_ua: float
    noise_floor_pct: float
    noise_floor_rate_ua: float
    cpu_mean_pct: float
    cpu_noise_floor_pct: float


def arm_conditions(
    device: device_probes.AdbDevice, arm: stats.Arm, baseline: stats.Baseline | None, component: str
) -> ArmConditions:
    """Describe the verified arm for the record, including the luminance recorded for it."""
    return {
        "arm": arm.value,
        "baseline": baseline.value if baseline else None,
        "wallpaper_component": component,
        "median_luma": round(probes.median_luma(device), 2),
        "median_luma_note": probes.MEDIAN_LUMA_NOTE,
    }


def sample_window_states(
    device: device_probes.AdbDevice,
    seconds: float,
    *,
    home_required: bool,
) -> tuple[list[bool | None], list[bool], list[bool | None], list[bool | None]]:
    """
    Sample display, power source, and — for a visible window — surface and keyguard throughout.

    A start-only check would miss another app or a lock surface that arrived mid-window; an unreadable
    sample refuses the window. A hidden window passes `home_required=False` and gets empty lists.
    """
    states: list[bool | None] = []
    on_battery: list[bool] = []
    homes: list[bool | None] = []
    locks: list[bool | None] = []
    for index in range(math.ceil(seconds / SAMPLE_INTERVAL_SECONDS)):
        elapsed = min((index + 1) * SAMPLE_INTERVAL_SECONDS, seconds)
        time.sleep(elapsed - index * SAMPLE_INTERVAL_SECONDS)
        states.append(device.read_screen_on())
        on_battery.append(battery_snapshot.device_on_own_battery(device))
        reading = f"  {elapsed:6.0f}s: screen_on={states[-1]} on_battery={on_battery[-1]}"
        if home_required:
            homes.append(device.read_home_in_front())
            locks.append(device.read_keyguard_locked())
            reading += f" home_in_front={homes[-1]} keyguard_locked={locks[-1]}"
        print(reading, flush=True)
    return states, on_battery, homes, locks


def power_source_problem(on_battery: Sequence[bool]) -> str | None:
    """Return why a window is not a discharge measurement, or None when every sample was on battery."""
    if not on_battery:
        return "the window took no power-source sample, so it is not known to have run on battery"
    if not all(on_battery):
        return f"the device reported a charger connected inside the window: {list(on_battery)}"
    return None


def wait_home_in_front(
    device: device_probes.AdbDevice,
    *,
    sleep: Callable[[float], None] | None = None,
) -> bool | None:
    """
    Read back that the home screen is in front, bounded the way the service-log confirmations are.

    `show_home` reports nothing and every other guard passes with another app in front. None means no
    read succeeded, which the caller treats as a refusal.
    """
    saw_not_in_front = False
    sleep_fn = sleep if sleep is not None else time.sleep
    for attempt in range(HOME_CONFIRM_ATTEMPTS):
        reading = device.read_home_in_front()
        if reading is True:
            return True
        # `False` is a positive "another app is in front"; `None` said nothing.
        saw_not_in_front = saw_not_in_front or reading is False
        if attempt + 1 < HOME_CONFIRM_ATTEMPTS:
            sleep_fn(HOME_CONFIRM_INTERVAL_SECONDS)
    return False if saw_not_in_front else None


def ensure_visible_home(
    device: device_probes.AdbDevice,
    *,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """
    Put the device on the home screen with the display on, or refuse the window.

    `wake_screen()` only lights the display, so the keyguard and foreground reads decide what is
    measured. The keyguard is read before `dismiss_keyguard()` mutates the device, or the phase could
    unlock it and then pass its own guard.
    """
    if not device.wake_screen():
        detail = "the display could not be woken for a visible window"
        raise device_probes.ProbeError(detail)
    # Read before mutating: `wm dismiss-keyguard` is never restored.
    locked = device.read_keyguard_locked()
    if locked is None:
        detail = (
            "the keyguard state could not be read before the window was set up, so a locked device "
            "cannot be told from an unlocked one; refusing to dismiss a keyguard that may be there"
        )
        raise device_probes.ProbeError(detail)
    if locked:
        detail = (
            "the keyguard is showing, so the device is not in the state this phase measures in, and "
            "dismissing it would change a shared device that nothing here puts back; unlock the device "
            "and re-run"
        )
        raise device_probes.ProbeError(detail)
    device.dismiss_keyguard()
    device.show_home()
    if device.read_screen_on() is not True:
        detail = "the display did not read back as on while the home screen was being established"
        raise device_probes.ProbeError(detail)
    # Three-valued: only a positive *unlocked* reading shows the home screen is what is measured.
    keyguard = device.read_keyguard_locked()
    if keyguard is True:
        detail = "the device is still locked after dismissing the keyguard, so the home screen is not displayed"
        raise device_probes.ProbeError(detail)
    if keyguard is None:
        detail = "the keyguard state could not be read, so it cannot be shown that the home screen is displayed"
        raise device_probes.ProbeError(detail)
    # `show_home()` can be dropped with every other guard passing, so the readback is three-valued.
    in_front = wait_home_in_front(device, sleep=sleep)
    if in_front is False:
        detail = (
            f"another package is in front after KEYCODE_HOME: the device reports "
            f"{device.read_foreground_package()} where it resolves {device.read_home_package()} as home, "
            f"so the wallpaper is not the surface this window would measure"
        )
        raise device_probes.ProbeError(detail)
    if in_front is None:
        detail = "the foreground package could not be read, so it cannot be shown that the home screen is displayed"
        raise device_probes.ProbeError(detail)


def run_window(
    device: device_probes.AdbDevice,
    pid: int | None,
    minutes: float,
    *,
    screen_on: bool,
    clk_tck: int,
) -> tuple[battery_snapshot.Window, list[bool | None], list[bool], list[bool | None], list[bool | None]]:
    """Sample a bounded window, holding the requested display state for its whole duration."""
    if not screen_on:
        device.run_adb(["shell", "input", "keyevent", "KEYCODE_SLEEP"])
        time.sleep(2.0)
        # The sleep event is a request, not the state: a lag would leave the display lit for the first
        # sampling interval while every later read was off, so B3 needs the same readback `wake_screen`
        # makes for the opposite transition.
        if device.read_screen_on() is not False:
            detail = (
                "the display did not read back as off after the sleep key event, so the window would "
                "not be screen-off throughout"
            )
            raise device_probes.ProbeError(detail)
    start = probes.take_battery_sample(device, pid, clk_tck)
    seconds = minutes * 60.0
    # A lit window is a home-screen window, so the surface in front is sampled with the rest.
    states, on_battery, homes, locks = sample_window_states(device, seconds, home_required=screen_on)
    end = probes.take_battery_sample(device, pid, clk_tck)
    window = battery_snapshot.Window(
        start_sample=start, end_sample=end, duration_seconds=end.timestamp - start.timestamp
    )
    return window, states, on_battery, homes, locks


def phase_resources(device: device_probes.AdbDevice) -> battery_snapshot.Record:
    """Record B2: the wake locks of any type, alarms, and jobs the app holds, with their commands."""
    pid = probes.wallpaper_pid(device)
    uid = probes.read_process_uid(device, pid)
    print(f"$ adb -s {device.serial} shell pidof {contract.PACKAGE_NAME} -> {pid}")
    print(f"$ adb -s {device.serial} shell stat -c %u /proc/{pid} -> {uid}")
    if uid is None:
        detail = "the wallpaper has no live process, so its wake locks cannot be attributed"
        raise device_probes.ProbeError(detail)
    wake_locks = probes.count_held_wakelocks(device, uid)
    alarms = probes.count_alarms(device)
    jobs = probes.count_jobs(device)
    # The all-type budget needs this stamp: a legacy partial-only census wrote `wake_locks` without it.
    print(f"$ adb -s {device.serial} shell dumpsys power   (every wake-lock type, uid={uid}) -> {wake_locks}")
    print(f"$ adb -s {device.serial} shell dumpsys alarm   ({contract.PACKAGE_NAME}) -> {alarms}")
    print(f"$ adb -s {device.serial} shell dumpsys jobscheduler   ({contract.PACKAGE_NAME}) -> {jobs}")
    summary = probes.read_batterystats_summary(device)
    print(f"  batterystats corroboration: {summary or 'no matching lines'}")
    record = probes.build_record(
        device,
        "resources",
        0.0,
        wake_locks=wake_locks,
        wake_lock_coverage=stats.WAKE_LOCK_COVERAGE,
        alarms=alarms,
        jobs=jobs,
        wallpaper_pid=pid,
        wallpaper_uid=uid,
        batterystats_summary=summary,
    )
    print(f"wake locks={wake_locks} (budget 0), alarms={alarms} (budget 0), jobs={jobs} (budget 0)")
    return record


def require_hidden_measurement(
    window: battery_snapshot.Window,
    states: Sequence[bool | None],
    on_battery: Sequence[bool],
) -> float:
    """
    Refuse a hidden window that is not a B3 measurement, returning the CPU it measured.

    The caller runs this inside the `try` that persists restore problems, because a refusal is an
    abort with the device possibly changed.
    """
    if not states or not all(state is False for state in states):
        detail = f"the display was not off for the whole window: {states}"
        raise device_probes.ProbeError(detail)
    charging = power_source_problem(on_battery)
    if charging is not None:
        detail = f"this window is not a valid measurement: {charging}"
        raise device_probes.ProbeError(detail)
    cpu_pct = window.cpu_pct
    if cpu_pct is None:
        detail = "the wallpaper process's CPU counters could not be read over the hidden window"
        raise device_probes.ProbeError(detail)
    return cpu_pct


def require_visible_measurement(  # noqa: PLR0913 — the window and the four state samples are all inputs
    window: battery_snapshot.Window,
    states: Sequence[bool | None],
    on_battery: Sequence[bool],
    home_in_front: Sequence[bool | None],
    arm: stats.Arm,
    *,
    keyguard_locked: Sequence[bool | None],
) -> float:
    """
    Refuse a visible window that is not a B4/B5 measurement, returning its discharge rate.

    Surface and keyguard are checked throughout, because another app or a lock surface leaves the
    bound component unchanged. `discharge_rate_ua` raises when the pack gained charge.
    """
    if states and not all(state is True for state in states):
        detail = f"the display was not on for the whole visible window: {states}"
        raise device_probes.ProbeError(detail)
    if home_in_front and not all(state is True for state in home_in_front):
        detail = f"the home screen was not the surface in front for the whole visible window: {home_in_front}"
        raise device_probes.ProbeError(detail)
    if keyguard_locked and not all(state is False for state in keyguard_locked):
        detail = f"the keyguard was showing or unreadable inside the visible window: {keyguard_locked}"
        raise device_probes.ProbeError(detail)
    charging = power_source_problem(on_battery)
    if charging is not None:
        detail = f"this is not a discharge measurement: {charging}"
        raise device_probes.ProbeError(detail)
    if arm is stats.Arm.LIVE and window.cpu_pct is None:
        detail = "the live wallpaper's CPU counters could not be read over a visible window"
        raise device_probes.ProbeError(detail)
    return window.discharge_rate_ua


def require_memory_measurement(  # noqa: PLR0913 — the four state samples and the two battery samples
    states: Sequence[bool | None],
    on_battery: Sequence[bool],
    home_in_front: Sequence[bool | None],
    before: battery_snapshot.BatterySample,
    after: battery_snapshot.BatterySample,
    *,
    keyguard_locked: Sequence[bool | None],
) -> int:
    """
    Refuse a memory window that is not a B6 measurement, returning total-PSS growth in kB.

    Surface and keyguard are checked throughout, as in the visible window.
    """
    if states and not all(state is True for state in states):
        detail = f"the display was not on for the whole memory window: {states}"
        raise device_probes.ProbeError(detail)
    if home_in_front and not all(state is True for state in home_in_front):
        detail = f"the home screen was not the surface in front for the whole memory window: {home_in_front}"
        raise device_probes.ProbeError(detail)
    if keyguard_locked and not all(state is False for state in keyguard_locked):
        detail = f"the keyguard was showing or unreadable inside the memory window: {keyguard_locked}"
        raise device_probes.ProbeError(detail)
    charging = power_source_problem(on_battery)
    if charging is not None:
        detail = f"this window is not a valid measurement: {charging}"
        raise device_probes.ProbeError(detail)
    if before.total_pss_kb is None or after.total_pss_kb is None:
        detail = "a total-PSS sample could not be parsed, so growth has no value"
        raise device_probes.ProbeError(detail)
    return after.total_pss_kb - before.total_pss_kb


def phase_hidden(
    device: device_probes.AdbDevice, minutes: float, *, recorder: battery_snapshot.Recorder
) -> battery_snapshot.Record:
    """Record B3: hidden-state CPU over a screen-off window, with the visibility sampled."""
    clk_tck = probes.require_clk_tck(device)
    snapshot = battery_snapshot.DeviceStateSnapshot.capture(device)
    problems: list[str] = []
    prior_power = battery_snapshot.read_power_source_lines(device)
    power_source = battery_snapshot.POWER_SOURCE_DEVICE
    simulated = False
    # Restore in its own `finally` inside the outer `try`, so its problems are in hand for the record.
    try:
        try:
            # B3 is evidence for the live engine; a static wallpaper would make this the launcher's CPU.
            verify_arm(device, stats.Arm.LIVE, None)
            pid = probes.wallpaper_pid(device)
            snapshot.pin()
            # Record the mode before applying it: the override can take effect and still fail.
            power_source = battery_snapshot.power_mode_for(device)
            simulated = power_source == battery_snapshot.POWER_SOURCE_SIMULATED
            battery_snapshot.apply_power_mode(device, power_source)
            window, states, on_battery, _, _ = run_window(device, pid, minutes, screen_on=False, clk_tck=clk_tck)
        finally:
            problems = battery_snapshot.undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
        # Inside the `try`, so a post-restore refusal still records the restore problems.
        cpu_pct = require_hidden_measurement(window, states, on_battery)
        print(f"hidden window {window.duration_seconds:.1f}s: screen_on readings={states}")
        print(f"CPU {cpu_pct:.4f}% of one core against the {stats.MAX_HIDDEN_CPU_PCT}% budget")
        record = probes.build_record(
            device,
            "hidden",
            window.duration_seconds,
            window_minutes=minutes,
            cpu_pct=cpu_pct,
            hidden_cpu_budget_pct=stats.MAX_HIDDEN_CPU_PCT,
            clk_tck=clk_tck,
            power_source=power_source,
            screen_on_samples=states,
            on_battery_samples=on_battery,
            restore_problems=problems,
        )
    except Exception as error:
        probes.emit_aborted_restore_record(recorder, device, "hidden", error, problems)
        raise
    return record


def phase_visible(
    device: device_probes.AdbDevice,
    *,
    arm: stats.Arm,
    baseline: stats.Baseline | None,
    minutes: float,
    recorder: battery_snapshot.Recorder,
) -> battery_snapshot.Record:
    """Record one B4/B5 window on the home screen after the arm guard admits it."""
    clk_tck = probes.require_clk_tck(device)
    # Captured before the wake, so a restore can put a sleeping display back to sleep.
    snapshot = battery_snapshot.DeviceStateSnapshot.capture(device)
    prior_power = battery_snapshot.read_power_source_lines(device)
    problems: list[str] = []
    power_source = battery_snapshot.POWER_SOURCE_DEVICE
    simulated = False
    # Restore in its own `finally` inside the outer `try`, so its problems are in hand for the record.
    try:
        try:
            # The wake and arm guard are inside the try, so a refusal after waking still restores.
            ensure_visible_home(device)
            component = verify_arm(device, arm, baseline)
            pid = probes.wallpaper_pid(device) if arm is stats.Arm.LIVE else None
            conditions = arm_conditions(device, arm, baseline, component)
            print(f"arm guard admitted {conditions['wallpaper_component']} (median luma {conditions['median_luma']})")
            snapshot.pin()
            # Record the mode before applying it: the override can take effect and still fail.
            power_source = battery_snapshot.power_mode_for(device)
            simulated = power_source == battery_snapshot.POWER_SOURCE_SIMULATED
            battery_snapshot.apply_power_mode(device, power_source)
            window, states, on_battery, homes, locks = run_window(device, pid, minutes, screen_on=True, clk_tck=clk_tck)
        finally:
            problems = battery_snapshot.undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
        # Inside the `try`, so a post-restore refusal still records the restore problems.
        discharge_rate_ua = require_visible_measurement(window, states, on_battery, homes, arm, keyguard_locked=locks)
        print(f"window {window.duration_seconds:.1f}s: discharge {window.discharge_uah} uAh")
        print(f"discharge rate {discharge_rate_ua:.0f} uA, CPU {window.cpu_pct}% of one core")
        record = probes.build_record(
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
        probes.emit_aborted_restore_record(recorder, device, "visible", error, problems)
        raise
    return record


def phase_memory(
    device: device_probes.AdbDevice, minutes: float, *, recorder: battery_snapshot.Recorder
) -> battery_snapshot.Record:
    """Record B6: the total-PSS growth over a long window on the live wallpaper."""
    clk_tck = probes.require_clk_tck(device)
    # Captured before the wake, like the visible phase.
    snapshot = battery_snapshot.DeviceStateSnapshot.capture(device)
    prior_power = battery_snapshot.read_power_source_lines(device)
    problems: list[str] = []
    states: list[bool | None] = []
    on_battery: list[bool] = []
    homes: list[bool | None] = []
    locks: list[bool | None] = []
    power_source = battery_snapshot.POWER_SOURCE_DEVICE
    simulated = False
    # Restore in its own `finally` inside the outer `try`.
    try:
        try:
            ensure_visible_home(device)
            verify_arm(device, stats.Arm.LIVE, None)
            pid = probes.wallpaper_pid(device)
            snapshot.pin()
            # Record the mode before applying it: the override can take effect and still fail.
            power_source = battery_snapshot.power_mode_for(device)
            simulated = power_source == battery_snapshot.POWER_SOURCE_SIMULATED
            battery_snapshot.apply_power_mode(device, power_source)
            before = probes.take_battery_sample(device, pid, clk_tck)
            seconds = minutes * 60.0
            states, on_battery, homes, locks = sample_window_states(device, seconds, home_required=True)
            after = probes.take_battery_sample(device, pid, clk_tck)
        finally:
            problems = battery_snapshot.undo_phase_changes(device, snapshot, prior_power, simulated=simulated)
        # Inside the `try`, so a post-restore refusal still records the restore problems.
        growth_kb = require_memory_measurement(states, on_battery, homes, before, after, keyguard_locked=locks)
        print(f"total PSS {before.total_pss_kb} -> {after.total_pss_kb} kB over {minutes:.1f} min")
        print(f"growth {growth_kb} kB against the {stats.MAX_PSS_GROWTH_KB} kB budget")
        record = probes.build_record(
            device,
            "memory",
            # The measured span, not the requested length: `report` checks this field as a duration.
            after.timestamp - before.timestamp,
            window_minutes=minutes,
            pss_start_kb=before.total_pss_kb,
            pss_end_kb=after.total_pss_kb,
            pss_growth_kb=growth_kb,
            pss_budget_kb=stats.MAX_PSS_GROWTH_KB,
            clk_tck=clk_tck,
            power_source=power_source,
            screen_on_samples=states,
            on_battery_samples=on_battery,
            home_in_front_samples=homes,
            keyguard_locked_samples=locks,
            restore_problems=problems,
        )
    except Exception as error:
        probes.emit_aborted_restore_record(recorder, device, "memory", error, problems)
        raise
    return record


def phase_pilot(  # noqa: PLR0913 — the arms, the window shape, and the sink are all its inputs
    device: device_probes.AdbDevice,
    *,
    arm: stats.Arm,
    baseline: stats.Baseline | None,
    runs: int,
    minutes: float,
    recorder: battery_snapshot.Recorder,
) -> list[battery_snapshot.Record]:
    """
    Record the repeated windows whose spread is the device's noise floor.

    Each window is persisted as it completes; a failed restore stops the run after recording that
    window. The product of runs and minutes is bounded before the first window opens, because
    `docs/battery-budget.md` forbids an hours-long invocation.
    """
    if runs * minutes > MAXIMUM_PILOT_MINUTES:
        detail = (
            f"{runs} windows of {minutes:g} minutes is {runs * minutes:g} minutes, longer than the "
            f"{MAXIMUM_PILOT_MINUTES:g} minutes a pilot invocation will hold the device for"
        )
        raise device_probes.ProbeError(detail)
    rates: list[float] = []
    cpu_values: list[float] = []
    for run in range(1, runs + 1):
        print(f"\n--- pilot window {run}/{runs} ---")
        record = phase_visible(device, arm=arm, baseline=baseline, minutes=minutes, recorder=recorder)
        record.values["run"] = run
        record.values["pilot"] = True
        record.values["runs"] = runs
        recorder.emit(record)
        # Persisted before stopping, so no evidence is lost.
        restore_problems = record.values.get("restore_problems")
        if isinstance(restore_problems, list) and restore_problems:
            named = "; ".join(str(problem) for problem in restore_problems)
            detail = (
                f"pilot window {run} of {runs} did not restore the device ({named}), so the remaining "
                f"windows would be measured on a device this run does not control; stopping after "
                f"{run} window(s)"
            )
            raise device_probes.ProbeError(detail)
        rate = record.values.get("discharge_rate_ua")
        if not isinstance(rate, int | float):
            detail = "a visible window recorded no discharge rate, so the noise floor has no input"
            raise device_probes.ProbeError(detail)
        rates.append(float(rate))
        cpu_pct = record.values["cpu_pct"]
        if isinstance(cpu_pct, int | float):
            cpu_values.append(float(cpu_pct))
    mean, deviation, half_width = stats.two_sigma_noise_floor(rates)
    channel_measured = mean > 0
    values: PilotFloorValues = {
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
        # A gauge that never moved is a frozen counter, not zero noise: emit no floor instead.
        print(
            f"\nthe discharge channel did not move across {runs} windows (mean {mean:.0f} uA), so it is "
            "unmeasured and no noise floor is emitted for it; a threshold cannot be calibrated from a "
            "counter that never changed"
        )
    if cpu_values:
        cpu_mean, cpu_deviation, cpu_half_width = stats.two_sigma_noise_floor(cpu_values)
        values["cpu_mean_pct"] = cpu_mean
        values["cpu_noise_floor_pct"] = cpu_half_width
        print(f"CPU: mean {cpu_mean:.4f}%, sigma {cpu_deviation:.4f}, 2 sigma {cpu_half_width:.4f}")
    return [probes.build_record(device, "pilot_floor", 0.0, **values)]
