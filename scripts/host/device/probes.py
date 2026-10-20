# SPDX-License-Identifier: Apache-2.0
"""
ADB command transport, system/window/process/site state probes, and the AdbDevice facade.

Provides `AdbDevice`, `ProbeError`, and shared ADB inspection and control primitives.
"""

import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Final

from host import contract
from host.device import frame, trace

# Bound every ADB call: a stalled transport would otherwise block the run forever.
ADB_TIMEOUT_SECONDS: Final = 30
ADB_RESTORE_TIMEOUT_SECONDS: Final = 60

# `adb shell` exits with the status of the command it ran, so a shell that cannot find that command
# answers this status. It is how a device without `sha256sum` reports the tool's absence.
SHELL_COMMAND_NOT_FOUND_STATUS: Final = 127

# The service logs its acceptance asynchronously; a deferred or racy write can land after the
# broadcast command returns, so the confirmation re-reads a bounded number of times.
LOG_CONFIRM_ATTEMPTS: Final = 5
LOG_CONFIRM_INTERVAL_SECONDS: Final = 0.5

PHYSICAL_SIZE_PATTERN: Final = re.compile(r"^Physical size:\s*(\d+x\d+)$")
OVERRIDE_SIZE_PATTERN: Final = re.compile(r"^Override size:\s*(\d+x\d+)$")
WAKE_READ_PATTERN: Final = re.compile(r"mWakefulness=(\w+)")
DISPLAY_STATE_PATTERN: Final = re.compile(r"Display State=(ON|OFF)")
KEYGUARD_SHOWING_PATTERN: Final = re.compile(r"\bisKeyguardShowing[ \t]*=[ \t]*([^\s,;}]*)")
WALLPAPER_VISIBLE_PATTERN: Final = re.compile(r"\bmVisible=([^\s,;}]*)")
# The two lines `dumpsys window displays` uses to name what is in front. Either may be absent or
# carry no package: this device's Android 16 build prints `mCurrentFocus=Window{39e680d u0
# NotificationShade}` while the shade is down, which names a window and no package at all. They are
# read in this order, and `mCurrentFocus` is the answer whenever it names a window: `mFocusedApp` is
# consulted only when no focused window is reported at all, never to fill a package into one that
# carries none.
FOCUS_WINDOW_PATTERN: Final = re.compile(r"\bmCurrentFocus=Window\{[^}]*\}")
FOCUSED_APP_PATTERN: Final = re.compile(r"\bmFocusedApp=ActivityRecord\{[^}]*\}")
# The `package/class` token inside those bodies. The package is captured and the class is not,
# because the class varies between launchers and Android versions while the package does not: the
# launcher the physical device resolves is `com.example.launcher/.activities.LauncherActivity` (a
# placeholder; the resolved package is withheld per docs/device-testing.md), and comparing activity
# classes would make the comparison depend on which launcher build is installed.
FOCUS_COMPONENT_PATTERN: Final = re.compile(r"\b([A-Za-z][\w]*(?:\.[\w]+)+)/")
SHA256_HEX_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")

# In /proc/<pid>/stat, fields after the closing parenthesis of comm:
# index 11 is field 14 (utime) and index 12 is field 15 (stime).
PROC_STAT_UTIME_INDEX: Final = 11
PROC_STAT_STIME_INDEX: Final = 12
PROC_STAT_MIN_FIELDS: Final = 13

MIN_SPLIT_FIELDS: Final = 2
# The home-screen wallpaper is the one the `mLastWallpaper` section names; the lock slot and the
# desktop-file component are reported separately and must not be mistaken for it.
HOME_WALLPAPER_SECTION: Final = "mLastWallpaper state:"
HOME_WALLPAPER_COMPONENT_FIELD: Final = "mInfo.component="
LOCK_WALLPAPER_SECTION: Final = "mLastLockWallpaper state:"
PER_FILE_WALLPAPER_SECTION: Final = "Wallpaper per-file components:"
STATIC_WALLPAPER_COMPONENT_FIELD: Final = "mWallpaperComponent="
# The dump prints a component as `ComponentInfo{package/class}`, while the identifiers this harness
# compares against — `SERVICE_NAME` and `PLATFORM_STATIC_WALLPAPER` — are written without the wrapper,
# as `am` and `dumpsys activity service` take them.
COMPONENT_INFO_WRAPPER: Final = re.compile(r"^ComponentInfo\{(.*)\}$")


class ProbeError(RuntimeError):
    """A device probe that did not complete, as opposed to a successful reading that found nothing."""


def component_value(line: str, field: str) -> str:
    """Return what a wallpaper dump line carries for one component field, or `""` when it carries none."""
    if field not in line:
        return ""
    return line.split(field, 1)[1].strip()


def component_identifier(value: str) -> str:
    """
    Return the `package/class` identifier a wallpaper dump's component field carries.

    The field holds `ComponentInfo{package/class}`, and stripping the wrapper is what makes the
    reading comparable to the identifiers this harness names: `SERVICE_NAME` and
    `PLATFORM_STATIC_WALLPAPER` are written unwrapped, as `am` and `dumpsys activity service` take
    them, so an arm check comparing this reading against either name would otherwise reject both.
    Normalizing here keeps that to one representation, at the single place that knows the dump's
    format.

    A value that carries no wrapper is returned trimmed, and `ComponentInfo{}` normalizes to the
    empty string so that the caller's emptiness check refuses it rather than admitting a component
    with no name in it.
    """
    match = COMPONENT_INFO_WRAPPER.match(value.strip())
    return captured_text(match, 1) if match is not None else value.strip()


def read_wallpaper_component(serial: str | None = None) -> str:
    """
    Return the home-screen wallpaper component from `dumpsys wallpaper`, as `package/class`.

    The `mLastWallpaper` section names the live home component, and that read is bounded to its own
    section: the lock slot that follows it reports the same field, and the per-file list beyond that
    names components of its own, so reading past either boundary would silently report another slot's
    component. When nothing live is bound the home section has no component at all, and the dump then
    falls back to the per-file components, where the first `mWallpaperComponent` line is the home
    slot; that fallback is what a static baseline reports.

    The dump wraps both fields as `ComponentInfo{package/class}`, and the wrapper is stripped by
    `component_identifier` so the reading is directly comparable to `SERVICE_NAME` and
    `PLATFORM_STATIC_WALLPAPER`, which are written unwrapped. A caller that compares this value to a
    component it named itself therefore compares like with like.

    A field that is present but empty is skipped rather than returned. `mInfo.component=` with nothing
    after it parses to `""`, which `component_is_live` reports as not-live, so a truncated or reshaped
    dump would be admitted as a valid static-baseline window recorded against no component at all —
    an unreadable input taking the place of a reading. Skipping the empty value lets the fallback run
    and, when nothing usable is left, the refusal below fire.
    """
    output = run_adb(["shell", "dumpsys", "wallpaper"], serial=serial).decode("utf-8", errors="replace")
    lines = output.splitlines()
    section = next((index for index, line in enumerate(lines) if line.strip() == HOME_WALLPAPER_SECTION), None)
    if section is not None:
        boundary = next(
            (
                index
                for index, line in enumerate(lines[section + 1 :], start=section + 1)
                if line.strip() in (LOCK_WALLPAPER_SECTION, PER_FILE_WALLPAPER_SECTION)
            ),
            len(lines),
        )
        for line in lines[section + 1 : boundary]:
            value = component_identifier(component_value(line, HOME_WALLPAPER_COMPONENT_FIELD))
            if value:
                return value
    for line in lines:
        value = component_identifier(component_value(line, STATIC_WALLPAPER_COMPONENT_FIELD))
        if value:
            return value
    detail = "dumpsys wallpaper reported no home-screen wallpaper component"
    raise ProbeError(detail)


def error_detail(error: BaseException) -> str:
    """Format an exception with any captured stderr, which is where ADB explains itself."""
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace").strip()
    if stderr:
        return f"{error}: {stderr}"
    return str(error)


def captured_text(match: re.Match[str], group: int) -> str:
    """Return one capture group of a successful match."""
    return str(match.group(group))


def run_adb(args: list[str], serial: str | None = None, timeout: int = ADB_TIMEOUT_SECONDS) -> bytes:
    """Run one `adb` invocation and return its stdout, raising on a non-zero exit or a timeout."""
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    # S603: the executable is the developer's own `adb`, resolved from PATH; the argv is built here
    # from literals and parsed device output, and no shell is involved.
    res = subprocess.run(  # noqa: S603
        cmd, capture_output=True, check=True, timeout=timeout
    )
    return res.stdout


def list_devices() -> dict[str, str]:
    """Map every serial `adb devices` reports to its state, dropping the header line."""
    output = run_adb(["devices"]).decode("utf-8")
    return {parts[0]: parts[1] for line in output.splitlines()[1:] if len(parts := line.split()) >= MIN_SPLIT_FIELDS}


def select_target_serial(requested: str | None) -> str:
    """Resolve the one device to operate on, refusing to guess."""
    devices = list_devices()
    active = [serial for serial, state in devices.items() if state == "device"]
    if requested is not None:
        if requested in active:
            return requested
        state = devices.get(requested, "not attached")
        active_text = ", ".join(active) if active else "none"
        print(
            f"ERROR: --serial {requested} is not an active device (state: {state}).",
            file=sys.stderr,
        )
        print(f"Active devices: {active_text}", file=sys.stderr)
        sys.exit(1)
    if len(active) == 1:
        return active[0]
    print(
        f"ERROR: expected exactly one active ADB device but found {len(active)}.",
        file=sys.stderr,
    )
    if active:
        print(f"Active devices: {', '.join(active)}", file=sys.stderr)
    inactive = [f"{serial} ({state})" for serial, state in devices.items() if state != "device"]
    if inactive:
        print(f"Attached but not ready: {', '.join(inactive)}", file=sys.stderr)
    print("Pass --serial to choose one.", file=sys.stderr)
    sys.exit(1)


def read_display_size(serial: str) -> tuple[str | None, str | None]:
    """Read display sizes; return (None, None) for missing, invalid, or conflicting fields."""
    output = run_adb(["shell", "wm", "size"], serial=serial).decode("utf-8")
    sizes: dict[str, str] = {}
    for line in output.splitlines():
        field = line.strip()
        for label, pattern in (("Physical size", PHYSICAL_SIZE_PATTERN), ("Override size", OVERRIDE_SIZE_PATTERN)):
            if not field.startswith(label):
                continue
            match = pattern.fullmatch(field)
            if match is None:
                return None, None
            size = captured_text(match, 1)
            if any(int(dimension) <= 0 for dimension in size.split("x")):
                return None, None
            if label in sizes and sizes[label] != size:
                return None, None
            sizes[label] = size
    if "Physical size" not in sizes:
        return None, None
    return sizes["Physical size"], sizes.get("Override size")


def read_keyguard_locked(serial: str) -> bool | None:
    """Read whether the keyguard is currently showing, or None when it cannot be determined."""
    output = run_adb(["shell", "dumpsys", "window", "displays"], serial=serial).decode("utf-8")
    readings = [captured_text(match, 1) for match in KEYGUARD_SHOWING_PATTERN.finditer(output)]
    if "true" in readings:
        return True
    if readings and all(reading == "false" for reading in readings):
        return False
    return None


def read_foreground_package(serial: str) -> str | None:
    """
    Read the package in front from `dumpsys window displays`, or None when it cannot be read as one.

    `mCurrentFocus` is the window with input focus and is asked first. `mFocusedApp` is the resumed
    activity and is the fallback, but only when `mCurrentFocus` names no window at all — `null`, or
    no such line — because the two answer different questions and a gap in one is not the other.

    A `mCurrentFocus` that names a window carrying no package is not a gap to be filled in from the
    line below it. That is a system window holding the focus, and this device prints exactly that —
    `Window{39e680d u0 NotificationShade}` — while the shade is down. Falling through to
    `mFocusedApp` there would report the resumed app as the surface in front while an overlay covers
    it, which is precisely the reading `read_home_in_front` must not be given: a visible window would
    be measured on the shade rather than on the wallpaper it claims to measure, with the display and
    the keyguard both reading as the visible home state. Returning None instead makes the caller
    refuse, which is what "the foreground could not be read" is for.

    Only the package is returned. The activity class after the `/` is not, because it varies between
    launcher builds while the package that owns the home screen does not.
    """
    output = run_adb(["shell", "dumpsys", "window", "displays"], serial=serial).decode("utf-8", errors="replace")
    focus = FOCUS_WINDOW_PATTERN.search(output)
    if focus is not None:
        component = FOCUS_COMPONENT_PATTERN.search(focus.group(0))
        return captured_text(component, 1) if component is not None else None
    resumed = FOCUSED_APP_PATTERN.search(output)
    if resumed is not None:
        component = FOCUS_COMPONENT_PATTERN.search(resumed.group(0))
        if component is not None:
            return captured_text(component, 1)
    return None


def read_home_package(serial: str) -> str | None:
    """
    Read the package the device resolves for the `HOME` intent, or None when it resolves none.

    The device is asked which launcher it runs instead of the harness naming one. A launcher package
    written into this repository would be an OEM and model statement on a public surface, which
    `docs/device-testing.md` forbids; resolving it here keeps that identification on the device,
    where it is already true.
    """
    output = run_adb(
        [
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.HOME",
        ],
        serial=serial,
    ).decode("utf-8", errors="replace")
    for line in output.splitlines():
        component = FOCUS_COMPONENT_PATTERN.search(line)
        if component is not None:
            return captured_text(component, 1)
    return None


def read_home_in_front(serial: str) -> bool | None:
    """
    Report whether the home screen is the surface in front, or None when that cannot be decided.

    Three-valued for the same reason the keyguard read is: a device whose foreground package or
    launcher package could not be read has not been shown to be on the home screen, and treating
    "could not be read" as "yes" would let another app's surface be measured for the whole window
    while the bound wallpaper component still matched the requested arm.

    The comparison is between packages, not activity classes: the launcher resolves to
    `com.example.launcher/.activities.LauncherActivity` and to a different class on another build of
    the same launcher.
    """
    # The `HOME` resolution is the more stable of the two and is read first, so the foreground
    # snapshot that decides the answer is taken as late as possible and a switch away from home
    # during the first round trip is less likely to be read as the state at this point. The two reads
    # are still not atomic, so a caller measuring a window must verify the surface at the points it
    # requires rather than trusting this one answer for the whole window.
    home = read_home_package(serial)
    foreground = read_foreground_package(serial)
    if foreground is None or home is None:
        return None
    return foreground == home


def read_installed_apk_sha256(serial: str) -> str | None:
    """
    Read the SHA-256 of the installed APK from the device, or None when the installed build is unnamed.

    Hashing the host checkout's build output was a different measurement from the one the window
    ran against: the APK on the device may be an older install, and the artifact is absent on a
    machine that did not build it, so the digest then recorded was `None` — an unreadable input
    standing in for an identity. Reading the installed file is what the identity field claims.

    `pm path` lists the split APKs of the package, and `base.apk` is the one whose digest identifies
    the build. Neither step is allowed to fall back to a zero, so None is returned when there is no
    installed build to name: the listing carries no `base.apk`, the shell cannot find `sha256sum`, or
    the answer's first token is not a digest.

    A step that *failed* is a different reading and does not return None. A `pm path` that exits
    non-zero, a `sha256sum` that exits with anything other than the shell's command-not-found, and a
    transport that drops all propagate `CalledProcessError`, so a caller cannot mistake a probe that
    failed for a device that has no installed build.
    """
    paths = run_adb(["shell", "pm", "path", contract.PACKAGE_NAME], serial=serial).decode("utf-8", errors="replace")
    base_apk = next(
        (
            line.split("package:", 1)[1].strip()
            for line in paths.splitlines()
            if line.startswith("package:") and line.strip().endswith("/base.apk")
        ),
        None,
    )
    if base_apk is None:
        return None
    try:
        output = run_adb(["shell", "sha256sum", base_apk], serial=serial).decode("utf-8", errors="replace")
    except subprocess.CalledProcessError as error:
        # `adb shell` exits with the status of the command it ran and `run_adb` checks that status,
        # so a missing tool reaches this probe as a status rather than as error text: that status is
        # the refusal the docstring above promises. Any other non-zero exit is this probe failing —
        # a transport that dropped or a `pm` that could not answer — and propagating it keeps such a
        # failure from being read as an absent tool.
        if error.returncode == SHELL_COMMAND_NOT_FOUND_STATUS:
            return None
        raise
    digest = output.split(maxsplit=1)[0] if output.split() else ""
    # `sha256sum` prints `<digest>  <path>`. A shell that reports an error while still exiting 0 —
    # or an answer that is truncated, wrapped or otherwise reshaped — leaves a first token that is
    # not a digest, and requiring the shape keeps that text from being recorded as the build's
    # identity. It is a second defence, independent of the exit status handled above.
    if SHA256_HEX_PATTERN.match(digest) is None:
        return None
    return digest


def read_screen_on(serial: str) -> bool | None:
    """Read the screen state, returning None when neither wakefulness nor display state is readable."""
    power = run_adb(["shell", "dumpsys", "power"], serial=serial).decode("utf-8")
    for line in power.splitlines():
        match = WAKE_READ_PATTERN.search(line)
        if match:
            state = captured_text(match, 1)
            if state == "Awake":
                return True
            if state in ("Asleep", "Dozing"):
                return False
    display = run_adb(["shell", "dumpsys", "display"], serial=serial).decode("utf-8")
    match = DISPLAY_STATE_PATTERN.search(display)
    if match:
        return captured_text(match, 1) == "ON"
    return None


def read_night_mode(serial: str) -> str | None:
    """Read the current night mode setting from `cmd uimode night`."""
    output = run_adb(["shell", "cmd", "uimode", "night"], serial=serial).decode("utf-8")
    for line in output.splitlines():
        if "Night mode:" in line:
            return line.split("Night mode:")[-1].strip().lower()
    return None


def get_wallpaper_pid(serial: str) -> int | None:
    """Return the wallpaper process id, or None when the package has no live process."""
    try:
        output = run_adb(["shell", "pidof", contract.PACKAGE_NAME], serial=serial).decode().strip()
    except subprocess.CalledProcessError as error:
        # `pidof` exits 1 both when nothing matches and when the transport itself failed; only a
        # failure that wrote to stderr is a transport error that must not be read as "no process".
        stderr = error.stderr
        if stderr and (not isinstance(stderr, str) or stderr.strip()):
            raise
        return None
    pids = output.split()
    return int(pids[0]) if pids and pids[0].isdigit() else None


def read_process_cpu_ticks(serial: str, pid: int) -> int | None:
    """Return the combined user and kernel CPU ticks for a process from /proc/<pid>/stat, or None."""
    try:
        output = run_adb(["shell", "cat", f"/proc/{pid}/stat"], serial=serial).decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: process CPU stat probe failed: {error_detail(error)}", file=sys.stderr)
        return None
    rparen_index = output.rfind(")")
    if rparen_index == -1:
        return None
    fields = output[rparen_index + 1 :].split()
    if (
        len(fields) >= PROC_STAT_MIN_FIELDS
        and fields[PROC_STAT_UTIME_INDEX].isdigit()
        and fields[PROC_STAT_STIME_INDEX].isdigit()
    ):
        return int(fields[PROC_STAT_UTIME_INDEX]) + int(fields[PROC_STAT_STIME_INDEX])
    return None


def read_process_cpu_clock_ticks(serial: str) -> int | None:
    """Read the kernel's CPU ticks per second (getconf CLK_TCK), or None when it is unreadable."""
    try:
        output = run_adb(["shell", "getconf", "CLK_TCK"], serial=serial).decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: clock-tick rate probe failed: {error_detail(error)}", file=sys.stderr)
        return None
    value = output.strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


def is_saved_coordinate_in_range(value: object, limit: float) -> bool:
    """Report whether value is a JSON number within ±limit, excluding JSON true/false (a Python int)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and -limit <= value <= limit


def saved_record_zone_id(record: Mapping[str, object]) -> str | None:
    """Return a record's stored zoneId when LocationStore.load() accepts the record, else None."""
    # json.loads rejects trailing data after the object, as Kotlin's parseRecord does.
    store = contract.CONTRACT.location_store
    version = record.get(store.fields.version)
    if isinstance(version, bool) or not isinstance(version, int) or version != store.record_version:
        return None
    if not (
        is_saved_coordinate_in_range(record.get(store.fields.latitude), store.max_latitude)
        and is_saved_coordinate_in_range(record.get(store.fields.longitude), store.max_longitude)
    ):
        return None
    source = record.get(store.fields.source)
    if not isinstance(source, str) or source not in store.sources:
        return None
    zone_id = record.get(store.fields.zone_id)
    # A stored zoneId is returned as-is, without testing that Python can build it. The store treats an
    # absent, empty, or non-string id as "no stored zone" (None here, so the caller uses the device
    # zone), but for a present id the app's ZoneId.of and Python's ZoneInfo accept different strings:
    # ZoneId.of takes fixed offsets ("+02:00"), "Z", and "UT" that ZoneInfo rejects. The phase must
    # reject an id it cannot build loudly rather than substitute the device zone for one the app
    # would still have rendered, which would let the rollover check pass on the wrong civil midnight.
    return zone_id if isinstance(zone_id, str) and zone_id else None


def parse_saved_site_zone(prefs_xml: str) -> str | None:
    """Return the saved site's stored zoneId when LocationStore.load() accepts the record, else None."""
    # S314: the XML is the app's own SharedPreferences file, read back from the debug package that
    # wrote it over an authenticated ADB channel; there is no attacker-supplied document here, and
    # the stdlib-only constraint rules out defusedxml.
    try:
        root = ET.fromstring(prefs_xml)  # noqa: S314
    except ET.ParseError:
        return None
    for element in root.iter("string"):
        if element.get("name") != contract.CONTRACT.location_store.record_key:
            continue
        raw = element.text
        if not raw:
            return None
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(record, dict):
            return None
        return saved_record_zone_id(record)
    return None


def read_saved_site_zone_id(serial: str) -> str | None:
    """
    Read the saved observing-site zone from the debug package's prefs.

    Returns None only when the read succeeded and the store holds no loadable site (including the
    prefs file never having been written). Any other failure raises ProbeError: a false None would
    let the rollover phase fall back to the device timezone and pass on a civil midnight the
    wallpaper never renders, whereas a false ProbeError only fails the phase loudly.
    """
    try:
        output = run_adb(
            ["shell", "run-as", contract.PACKAGE_NAME, "cat", contract.LOCATION_PREFS_PATH], serial=serial
        ).decode("utf-8", errors="replace")
    except subprocess.CalledProcessError as error:
        stderr = error.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        # The absent-file strerror must name the prefs path itself; an ENOENT about any other path is
        # a probe error, not the store's "no saved site", so it must not reach the device-zone fallback.
        if stderr and contract.PREFS_FILE_ABSENT_MARKER in stderr and contract.LOCATION_PREFS_PATH in stderr:
            return None
        message = f"saved-site prefs probe failed: {error_detail(error)}"
        raise ProbeError(message) from error
    except (subprocess.SubprocessError, OSError) as error:
        # subprocess.TimeoutExpired lands here: like any other transport failure it must fail the
        # phase through ProbeError rather than escape and abort the rest of the run.
        message = f"saved-site prefs probe failed: {error_detail(error)}"
        raise ProbeError(message) from error
    return parse_saved_site_zone(output)


def read_device_timezone(serial: str) -> str | None:
    """Read the device's system timezone property, or None when it is unavailable or empty."""
    try:
        output = run_adb(["shell", "getprop", "persist.sys.timezone"], serial=serial).decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: device timezone probe failed: {error_detail(error)}", file=sys.stderr)
        return None
    zone_id = output.strip()
    return zone_id or None


def read_wallpaper_visible(serial: str) -> bool | None:
    """Return any visible engine, all explicitly hidden engines, or an uncertain dump as True/False/None."""
    try:
        output = run_adb(
            ["shell", "dumpsys", "activity", "service", contract.SERVICE_NAME],
            serial=serial,
        ).decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: wallpaper visibility probe failed: {error_detail(error)}", file=sys.stderr)
        return None
    values = [captured_text(match, 1) for match in WALLPAPER_VISIBLE_PATTERN.finditer(output)]
    if "true" in values:
        return True
    if values and all(value == "false" for value in values):
        return False
    return None


def sleep_screen(serial: str) -> bool:
    """Put the screen back to sleep, reporting whether the request was delivered."""
    try:
        run_adb(
            ["shell", "input", "keyevent", "KEYCODE_SLEEP"],
            serial=serial,
            timeout=ADB_RESTORE_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: screen-state restore failed: {error_detail(error)}", file=sys.stderr)
        return False
    return True


def wake_screen(serial: str) -> bool:
    """Wake the screen, returning True only when the readback confirms it is on."""
    try:
        run_adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial)
        return read_screen_on(serial) is True
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: screen wake failed: {error_detail(error)}", file=sys.stderr)
        return False


def run_restore_command(serial: str, command: list[str]) -> bool:
    """Run one restore command, reporting a failure without raising so the remaining ones still run."""
    try:
        run_adb(command, serial=serial, timeout=ADB_RESTORE_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: restore command failed: {error_detail(error)}", file=sys.stderr)
        return False
    return True


def count_service_log_messages(serial: str, message: str) -> int:
    """
    Count the service-tagged lines containing the message in an unfiltered logcat dump.

    The tag and message match host-side: logcat's device-side `-s` filter reads the compressed
    buffer entry by entry and measured about 17s on a full buffer against 2.5s for the unfiltered
    dump, which would otherwise leave little room under the ADB timeout.
    """
    output = run_adb(["logcat", "-d"], serial=serial).decode("utf-8", errors="replace")
    return sum(message in line and f"{contract.SERVICE_LOG_TAG}: " in line for line in output.splitlines())


def send_debug_clock_broadcast(serial: str, extras: list[str], expected_message: str) -> bool:
    """Send a debug-clock broadcast and report whether the service logged a new acceptance line."""
    before = count_service_log_messages(serial, expected_message)
    run_adb(
        ["shell", "am", "broadcast", "-a", contract.DEBUG_ACTION, *extras],
        serial=serial,
        timeout=ADB_RESTORE_TIMEOUT_SECONDS,
    )
    for attempt in range(LOG_CONFIRM_ATTEMPTS):
        if count_service_log_messages(serial, expected_message) > before:
            return True
        if attempt + 1 < LOG_CONFIRM_ATTEMPTS:
            time.sleep(LOG_CONFIRM_INTERVAL_SECONDS)
    return False


def confirm_virtual_clock_reset(serial: str) -> bool:
    """Reset the virtual clock and report whether the service log confirmed it."""
    try:
        confirmed = send_debug_clock_broadcast(
            serial, contract.DEBUG_CLOCK_RESET_EXTRAS, contract.DEBUG_CLOCK_RESET_MESSAGE
        )
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: virtual-clock reset failed: {error_detail(error)}", file=sys.stderr)
        return False
    if not confirmed:
        print("WARNING: virtual-clock reset was not confirmed", file=sys.stderr)
    return confirmed


class AdbDevice:
    """Encapsulates ADB operations and state inspection for a specific target device serial."""

    def __init__(self, serial: str) -> None:
        """Initialize an AdbDevice with the target device serial."""
        self.serial: Final = serial

    def run_adb(self, args: list[str], timeout: int = ADB_TIMEOUT_SECONDS) -> bytes:
        """Run an adb invocation targeting this device serial."""
        return run_adb(args, serial=self.serial, timeout=timeout)

    def capture_frame(self) -> tuple[int, int, bytes]:
        """Capture one screencap frame from this device."""
        return frame.capture_frame(serial=self.serial)

    def read_display_size(self) -> tuple[str | None, str | None]:
        """Read physical and override display sizes from `wm size`."""
        return read_display_size(self.serial)

    def read_foreground_package(self) -> str | None:
        """Read the package in front, or None when no focus line names one."""
        return read_foreground_package(self.serial)

    def read_home_package(self) -> str | None:
        """Read the package the device resolves for the `HOME` intent, or None."""
        return read_home_package(self.serial)

    def read_home_in_front(self) -> bool | None:
        """Report whether the home screen is the surface in front, or None when undecidable."""
        return read_home_in_front(self.serial)

    def read_installed_apk_sha256(self) -> str | None:
        """Read the SHA-256 of this package's installed `base.apk`, or None; raises when the probe fails."""
        return read_installed_apk_sha256(self.serial)

    def read_keyguard_locked(self) -> bool | None:
        """Read whether the keyguard is currently showing on this device."""
        return read_keyguard_locked(self.serial)

    def read_screen_on(self) -> bool | None:
        """Read whether this device's screen is currently on."""
        return read_screen_on(self.serial)

    def read_night_mode(self) -> str | None:
        """Read the current night mode setting on this device."""
        return read_night_mode(self.serial)

    def get_wallpaper_pid(self) -> int | None:
        """Return the wallpaper process ID on this device, or None."""
        return get_wallpaper_pid(self.serial)

    def read_process_cpu_ticks(self, pid: int) -> int | None:
        """Return the combined user and kernel CPU ticks for a process from /proc/<pid>/stat, or None."""
        return read_process_cpu_ticks(self.serial, pid)

    def read_process_cpu_clock_ticks(self) -> int | None:
        """Read this device's kernel CPU ticks per second, or None."""
        return read_process_cpu_clock_ticks(self.serial)

    def read_saved_site_zone_id(self) -> str | None:
        """Read this device's saved observing-site zone id, or None; raises ProbeError on failure."""
        return read_saved_site_zone_id(self.serial)

    def read_device_timezone(self) -> str | None:
        """Read this device's system timezone, or None."""
        return read_device_timezone(self.serial)

    def read_wallpaper_component(self) -> str:
        """Read this device's home-screen wallpaper component; raises ProbeError when none is reported."""
        return read_wallpaper_component(self.serial)

    def read_wallpaper_visible(self) -> bool | None:
        """Read whether the wallpaper engine is reported visible on this device."""
        return read_wallpaper_visible(self.serial)

    def capture_producer_window(
        self,
        pid: int,
        *,
        seconds: float | None = None,
        sample_seconds: float | None = None,
    ) -> "trace.ProducerWindow":
        """
        Trace this device's producer pipeline for a bounded window, sampling the display.

        The defaults are resolved at call time and the return annotation is a string: `trace` imports
        this module for transport, so a trace-first import leaves it partially initialized while this
        class is defined, and touching its attributes there would fail.
        """
        return trace.capture_producer_window(
            self.serial,
            pid,
            seconds=trace.TRACE_WINDOW_SECONDS if seconds is None else seconds,
            sample_seconds=trace.TRACE_DISPLAY_SAMPLE_SECONDS if sample_seconds is None else sample_seconds,
        )

    def sleep_screen(self) -> bool:
        """Put the screen to sleep."""
        return sleep_screen(self.serial)

    def wake_screen(self) -> bool:
        """Wake the screen and verify it is awake."""
        return wake_screen(self.serial)

    def dismiss_keyguard(self) -> None:
        """Send wm dismiss-keyguard."""
        self.run_adb(["shell", "wm", "dismiss-keyguard"])

    def show_home(self) -> None:
        """
        Send KEYCODE_HOME.

        This sends the event and reports nothing about what it did: an input event can be dropped,
        and a caller that needs the home screen to actually be in front has to read it back with
        `read_home_in_front`. `battery_protocol.ensure_visible_home` is the caller that does.
        """
        self.run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"])

    def run_restore_command(self, command: list[str]) -> bool:
        """Run one restore command on this device."""
        return run_restore_command(self.serial, command)

    def count_service_log_messages(self, message: str) -> int:
        """Count service-tagged logcat lines containing the given message."""
        return count_service_log_messages(self.serial, message)

    def send_debug_clock_broadcast(self, extras: list[str], expected_message: str) -> bool:
        """Send a debug-clock broadcast and verify the expected log message."""
        return send_debug_clock_broadcast(self.serial, extras, expected_message)

    def confirm_virtual_clock_reset(self) -> bool:
        """Reset the virtual clock and verify confirmation."""
        return confirm_virtual_clock_reset(self.serial)
