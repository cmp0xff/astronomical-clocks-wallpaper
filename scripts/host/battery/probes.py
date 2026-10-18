# SPDX-License-Identifier: Apache-2.0
"""Battery, CPU, PSS, wake-lock, alarm, job, luma, and device-identity probes for the battery protocol."""

import datetime as dt
import hashlib
import re
import subprocess
import sys
import time
from typing import Final

from host import contract
from host.battery import snapshot
from host.device import frame
from host.device import probes as device_probes

CHARGE_COUNTER_PATTERN: Final = re.compile(rf"^[ \t]*Charge counter:[ \t]*({snapshot.DIGITS})[ \t]*$", re.MULTILINE)

BATTERY_LEVEL_PATTERN: Final = re.compile(rf"^[ \t]*level:[ \t]*({snapshot.DIGITS})[ \t]*$", re.MULTILINE)

# Anchored to the field name so that `Max charging voltage:` can never be read as the pack voltage.
BATTERY_VOLTAGE_PATTERN: Final = re.compile(rf"^[ \t]*voltage:[ \t]*({snapshot.DIGITS})[ \t]*$", re.MULTILINE)

# `[ \t]` rather than `\s`, which matches a newline and would let a truncated `TOTAL` line borrow the
# next line's number. The trailing lookahead keeps the captured value from doing the same.
TOTAL_PSS_PATTERN: Final = re.compile(rf"^[ \t]*TOTAL[ \t]+({snapshot.DIGITS})(?=[ \t]|$)", re.MULTILINE)

SDK_PATTERN: Final = re.compile(rf"^({snapshot.DIGITS})$")

# Every shape `dumpsys jobscheduler` prints a job line in. The component is `<pkg>/<class>` or
# `<ns>@<pkg>/<class>`, plus one legacy `DownloadManager:<pkg>` form, and group 1 is the owning
# package. The namespaced forms are where WorkManager and the sync adapters keep their jobs, so a
# plain `JOB #<uid>/<id>` pattern silently drops them from the count.
JOB_LINE_PATTERN: Final = re.compile(
    r"^\s*JOB \S+/-?\d+:\s+\S+\s+(?:@[\w.]+@)?(?:[A-Za-z][\w.]*:)?([\w.\-]+)(?:[/:]|$)",
    re.MULTILINE,
)

# Wider than JOB_LINE_PATTERN, so an unparsable job line surfaces as a count mismatch rather than
# being dropped.
JOB_ENTRY_PATTERN: Final = re.compile(r"^\s*JOB \S+/-?\d+:", re.MULTILINE)

# Every lock type, not `PARTIAL_WAKE_LOCK` alone: the census counts the whole device, and this
# device's only lock is a `DOZE_WAKE_LOCK`, so a partial-only count could never reconcile with it.
WAKE_LOCK_ENTRY_PATTERN: Final = re.compile(r"^\s*\w+_WAKE_LOCK\b", re.MULTILINE)

# One `<TYPE> #<n>: Alarm{...}` header per pending alarm, read inside the census block only: a later
# section prints `Past-due non-wakeup alarms:` entries in the same shape.
ALARM_ENTRY_PATTERN: Final = re.compile(r"^\s*\w+\s*#\d+:\s*Alarm\{", re.MULTILINE)

# The census line separates "the app has no jobs" from "this is not a jobscheduler dump". It must
# end after the count: anchored only at the start, `Registered 0 jobs: CORRUPT` reads as a zero.
JOBSCHEDULER_CENSUS_PATTERN: Final = re.compile(rf"^[ \t]*Registered ({snapshot.DIGITS}) jobs:[ \t]*$", re.MULTILINE)

# The census separates "no wake locks" from "not a power dump". `[ \t]` rather than `\s`, which would
# let a match begin on the blank line above and locate the census block from the wrong line.
POWER_WAKELOCK_CENSUS_PATTERN: Final = re.compile(rf"^[ \t]*Wake Locks: size=({snapshot.DIGITS})[ \t]*$", re.MULTILINE)

# `<n> pending alarms:` is a count of entries, not the highest entry index; the device's own dump
# (no `Pending alarm batches:` line) confirms that reading.
ALARM_CENSUS_PATTERN: Final = re.compile(rf"^[ \t]*({snapshot.DIGITS}) pending alarms:[ \t]*$", re.MULTILINE)

# A device count that cannot be negative. A failed read can answer `-1`, and `int()` would take it: a
# negative UID builds a wake-lock predicate matching nothing (a B2 pass), and a negative utime inflates
# a window's CPU delta.
UNSIGNED_INT_PATTERN: Final = re.compile(snapshot.DIGITS)


PROC_STAT_UTIME_INDEX: Final = 11

PROC_STAT_INDEX_STIME: Final = 12

MIN_PROC_STAT_FIELDS: Final = 13

# Ticks per second is a per-device kernel constant (`getconf CLK_TCK`), probed once per run; scaling
# by an assumed rate would misreport every CPU criterion.


# The live arm is compared as a whole component, not a substring: another package shipping the same
# class name would otherwise be admitted as the wallpaper under test.
COMPONENT_INFO_PATTERN: Final = re.compile(r"^ComponentInfo\{(.*)\}$")

LUMA_STRIDE: Final = 8

LUMA_WEIGHTS: Final = (0.2126, 0.7152, 0.0722)


MEDIAN_LUMA_NOTE: Final = "recorded, not gated: the OEM stock wallpaper on this device is a dark image"

# How the device's identity is recorded. The digest is salted so that it is meaningful only inside
# this project's own state files, and truncated because it is only ever compared for equality.
DEVICE_ID_SALT: Final = "godaniya-126 device|"

DEVICE_ID_CHARS: Final = 12


def parse_battery_output(output: str) -> tuple[int, int, int]:
    """Extract charge counter (uAh), level (%), and voltage (mV); a missing field is refused, not zeroed."""

    def value(pattern: re.Pattern[str], name: str) -> int:
        if (match := pattern.search(output)) is None:
            detail = f"dumpsys battery output carried no {name} field; refusing to read it as zero"
            raise device_probes.ProbeError(detail)
        return int(match.group(1))

    return (
        value(CHARGE_COUNTER_PATTERN, "Charge counter"),
        value(BATTERY_LEVEL_PATTERN, "level"),
        value(BATTERY_VOLTAGE_PATTERN, "voltage"),
    )


def parse_unsigned_int(raw: str) -> int | None:
    """Read a device's non-negative decimal integer, or None when the text is not one."""
    return int(raw) if UNSIGNED_INT_PATTERN.fullmatch(raw) else None


def parse_proc_stat_cpu_seconds(stat_content: str, clk_tck: int) -> float | None:
    """Parse utime + stime from `/proc/<pid>/stat` as CPU seconds; `clk_tck` is required, never assumed."""
    if clk_tck <= 0:
        detail = f"a CPU-tick rate of {clk_tck} cannot scale a tick count"
        raise device_probes.ProbeError(detail)
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
        raise device_probes.ProbeError(detail)
    return int(stripped)


def wallpaper_pid(device: device_probes.AdbDevice) -> int | None:
    """
    Read the wallpaper process id, refusing an answer that cannot be read as one.

    The shared reader converts an oversized digit run with `int()`, whose `ValueError` `main` does not
    catch; translating it here keeps this harness's named `ERROR` line. `None` still means no live
    wallpaper process, because an unreadable answer is not evidence of one.
    """
    try:
        return device.get_wallpaper_pid()
    except ValueError as error:
        detail = (
            f"the wallpaper process id probe answered something that is not a process id ({error}), so "
            "the wallpaper's process cannot be identified; refusing rather than reading it as no live "
            "process"
        )
        raise device_probes.ProbeError(detail) from error


def read_process_uid(device: device_probes.AdbDevice, pid: int | None) -> int | None:
    """
    Read the owning UID of a process from `/proc/<pid>`, or None when it cannot be read.

    A negative read is malformed, not an owner: `uid=-1` matches no wake lock and would report a B2
    pass from a read that answered nothing.
    """
    if pid is None or pid <= 0:
        return None
    output = device.run_adb(["shell", "stat", "-c", "%u", f"/proc/{pid}"]).decode(errors="replace").strip()
    return parse_unsigned_int(output)


def require_clk_tck(device: device_probes.AdbDevice) -> int:
    """
    Probe the device's kernel clock-tick rate, refusing to run without a real one.

    An unreadable answer includes one `isdigit()` accepts and `int()` does not (such as `"²"`); that
    `ValueError` is translated here so the operator sees the named refusal, not a traceback.
    """
    try:
        clk_tck = device.read_process_cpu_clock_ticks()
    except ValueError as error:
        detail = (
            f"the CLK_TCK probe answered something that is not a tick rate ({error}), so process CPU "
            "ticks cannot be converted to seconds; refusing to scale every CPU criterion by an "
            "assumed rate"
        )
        raise device_probes.ProbeError(detail) from error
    if clk_tck is None or clk_tck <= 0:
        detail = (
            "the device did not report its CLK_TCK rate, so process CPU ticks cannot be converted to "
            "seconds; refusing to scale every CPU criterion by an assumed rate"
        )
        raise device_probes.ProbeError(detail)
    return clk_tck


def take_battery_sample(device: device_probes.AdbDevice, pid: int | None, clk_tck: int) -> snapshot.BatterySample:
    """Read the battery fuel gauge plus the process CPU and PSS counters."""
    battery_output = device.run_adb(["shell", "dumpsys", "battery"]).decode(errors="replace")
    charge_uah, level_pct, voltage_mv = parse_battery_output(battery_output)

    cpu_seconds: float | None = None
    if pid is not None and pid > 0:
        stat_output = device.run_adb(["shell", "cat", f"/proc/{pid}/stat"]).decode(errors="replace")
        cpu_seconds = parse_proc_stat_cpu_seconds(stat_output, clk_tck)

    mem_output = device.run_adb(["shell", "dumpsys", "meminfo", contract.PACKAGE_NAME]).decode(errors="replace")
    return snapshot.BatterySample(
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

    The census alone is not a check: a dump truncated after it still carries the count, and reporting
    a zero then is a fabricated pass. Fewer entries than declared is a truncated dump; more is a shape
    the probe does not know. The block ends at the first line neither shaped as an entry nor indented
    deeper than the census, because a later section prints entries in the same shape. Indentation is
    the only structural fact the three dumps share, so continuations are identified by it.
    """
    census = census_pattern.search(dump)
    if census is None:
        detail = (
            f"the {probe} carried no {census_line} census, so it cannot be read as a count of its "
            f"entries; refusing to report a zero from output that lists none"
        )
        raise device_probes.ProbeError(detail)
    declared = int(census.group(1))
    lines = dump.splitlines()
    census_index = dump.count("\n", 0, census.start())
    census_text = lines[census_index] if census_index < len(lines) else ""
    indent = len(census_text) - len(census_text.lstrip())
    entries = _census_entry_lines(
        lines,
        census_index + 1,
        indent,
        entry_pattern,
        probe=probe,
        census_line=census_line,
        entry_shape=entry_shape,
    )
    if len(entries) < declared:
        detail = (
            f"the {probe} declares {declared} entries but the block below its {census_line} census "
            f"lists {len(entries)}: the dump is truncated, or an entry shaped like {entry_shape} was "
            f"not recognised"
        )
        raise device_probes.ProbeError(detail)
    if len(entries) > declared:
        detail = (
            f"the {probe} declares {declared} entries but its census block lists {len(entries)}: a "
            f"line matching {entry_shape} that the census does not count is inside the block"
        )
        raise device_probes.ProbeError(detail)
    return entries


def _census_entry_lines(  # noqa: PLR0913 — the block's location, its shape, and the names it refuses by
    lines: list[str],
    start: int,
    indent: int,
    entry_pattern: re.Pattern[str],
    *,
    probe: str,
    census_line: str,
    entry_shape: str,
) -> list[str]:
    """Return the entries of one census block, refusing an unrecognised line inside it."""
    entries: list[str] = []
    entry_indent: int | None = None
    for line in lines[start:]:
        if not line.strip():
            continue
        if entry_pattern.match(line):
            entries.append(line)
            entry_indent = len(line) - len(line.lstrip())
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent:
            break
        # A continuation is deeper indented than the entry it belongs to; a line before any entry or
        # at the entry's own depth is a malformed shape, not a continuation.
        if entry_indent is None or line_indent <= entry_indent:
            detail = (
                f"the {probe}'s {census_line} census block holds a line that is neither an entry "
                f"shaped like {entry_shape} nor a continuation of one: {line.strip()!r}; the census "
                "cannot be reconciled against a block it does not describe"
            )
            raise device_probes.ProbeError(detail)
    return entries


def parse_wakelock_count(dump: str, app_uid: int) -> int:
    """
    Count the wake locks a `dumpsys power` dump attributes to one UID.

    The census and the entry count must reconcile, per `census_entry_block`; a reconciled dump with no
    lock for this UID is a real zero. Every lock type is counted, because the census counts them all.
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


def parse_alarm_count(dump: str, package: str = contract.PACKAGE_NAME) -> int:
    """
    Count the pending alarms a `dumpsys alarm` dump attributes to one package.

    The `<n> pending alarms:` census and the entry count must reconcile, per `census_entry_block`.
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


def count_held_wakelocks(device: device_probes.AdbDevice, app_uid: int | None) -> int:
    """Count the wake locks of any type `dumpsys power` attributes to the app's UID."""
    if app_uid is None:
        detail = "no UID was resolved for the wallpaper process, so wake locks cannot be attributed"
        raise device_probes.ProbeError(detail)
    output = device.run_adb(["shell", "dumpsys", "power"]).decode(errors="replace")
    return parse_wakelock_count(output, app_uid)


def count_alarms(device: device_probes.AdbDevice) -> int:
    """Count the pending alarms `dumpsys alarm` attributes to the wallpaper package."""
    output = device.run_adb(["shell", "dumpsys", "alarm"]).decode(errors="replace")
    return parse_alarm_count(output)


def parse_job_count(dump: str, package: str = contract.PACKAGE_NAME) -> int:
    """
    Count the JobScheduler jobs a package holds in a `dumpsys jobscheduler` dump.

    The census and entry count must reconcile, per `census_entry_block`, and every line must be
    attributable: a dropped unparsable entry would otherwise read as "the app holds no job".
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
        raise device_probes.ProbeError(detail)
    # Every entry line was shown above to match exactly once, so counting over the rejoined block is
    # the same as counting per line, and it keeps the pattern's own match object for the package.
    return sum(1 for match in JOB_LINE_PATTERN.finditer("\n".join(entries)) if match.group(1) == package)


def count_jobs(device: device_probes.AdbDevice) -> int:
    """Count the JobScheduler jobs `dumpsys jobscheduler` attributes to the wallpaper package."""
    output = device.run_adb(["shell", "dumpsys", "jobscheduler"]).decode(errors="replace")
    return parse_job_count(output)


def read_batterystats_summary(device: device_probes.AdbDevice) -> str | None:
    """Read the historical per-app CPU lines from `dumpsys batterystats` as corroborating evidence."""
    output = device.run_adb(["shell", "dumpsys", "batterystats", contract.PACKAGE_NAME]).decode(errors="replace")
    keys = ("Total cpu time:", "Fg Service for:", "Total running:")
    lines = [line.strip() for line in output.splitlines() if any(key in line for key in keys)]
    return ", ".join(lines) if lines else None


def read_source_revision() -> str:
    """Read the source revision of this working tree."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def read_apk_sha256(device: device_probes.AdbDevice) -> str:
    """
    Read the SHA-256 of the APK as it is *installed on the device*, or refuse.

    This is the identity every aggregating report row is gated on; the host artifact answers a
    different question and can be absent. An uninstalled package or missing `sha256sum` is refused
    rather than recorded as an unknown build.
    """
    digest = device.read_installed_apk_sha256()
    if digest is None:
        detail = (
            f"the SHA-256 of {contract.PACKAGE_NAME}'s installed base.apk could not be read, so the "
            f"record cannot say which build this window measured"
        )
        raise device_probes.ProbeError(detail)
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


def read_hardware_serial(device: device_probes.AdbDevice) -> str | None:
    """
    Read the device's own serial property, or None when no stable handle can be read.

    The property is a hardware handle, unlike a `host:port` or mDNS transport selector, and survives
    a reconnect that changes either.
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


def device_identity(device: device_probes.AdbDevice) -> str:
    """
    Name the device a record was measured on, without naming the device.

    The serial is kept off public surfaces, so a salted digest distinguishes devices without
    disclosing one. A transport selector changes on reconnect and can be reused, so it is replaced by
    the hardware serial; when that cannot be read the record is refused rather than stamped with a
    selector that says nothing about the device.
    """
    serial = device.serial
    if not serial:
        detail = "the target device reported no serial, so a record cannot say which device it measured"
        raise device_probes.ProbeError(detail)
    if is_transport_selector(serial):
        hardware_serial = read_hardware_serial(device)
        if hardware_serial is None:
            detail = (
                f"the target device was reached over the transport selector {serial!r}, and its own "
                "serial property could not be read, so a record cannot say which device it measured"
            )
            raise device_probes.ProbeError(detail)
        serial = hardware_serial
    return hashlib.sha256(f"{DEVICE_ID_SALT}{serial}".encode()).hexdigest()[:DEVICE_ID_CHARS]


def build_record(
    device: device_probes.AdbDevice, phase: str, duration_seconds: float, **values: object
) -> snapshot.Record:
    """Assemble one record, stamping the device and build identity every report row has to carry."""
    api_level = parse_api_level(device.run_adb(["shell", "getprop", "ro.build.version.sdk"]).decode(errors="replace"))
    revision_val = values.pop("revision", None)
    revision = str(revision_val) if revision_val is not None else read_source_revision()
    return snapshot.Record(
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
    recorder: snapshot.Recorder,
    device: device_probes.AdbDevice,
    phase: str,
    error: BaseException,
    problems: list[str],
) -> None:
    """
    Persist a failed phase's restore problems when it aborted before building its own record.

    An exception through the restore `finally` would otherwise leave the device changed with nothing
    on disk for `report`. Only a non-empty problem list is written; a failed build is swallowed so the
    original error stands, and `main` still exits non-zero.
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
    except (device_probes.ProbeError, subprocess.SubprocessError, OSError):
        print(f"restore problems could not be persisted for the aborted {phase} phase: {problems}", file=sys.stderr)
        return
    recorder.emit(record)


def median_luma(device: device_probes.AdbDevice) -> float:
    """
    Return the median luminance of a freshly captured screen, a bounded proxy for what is shown.

    The raw screencap is used because a baseline arm has no dial to validate. A decode failure is
    translated to `ProbeError`, so a truncated payload is the named `ERROR` rather than a traceback.
    """
    try:
        _, _, pixels = frame.decode_frame(device.run_adb(["exec-out", "screencap"]))
    except frame.ScreencapError as error:
        detail = f"the captured screen could not be decoded into a frame: {device_probes.error_detail(error)}"
        raise device_probes.ProbeError(detail) from error
    # The shortest channel slice covers exactly the complete triples, so the outer zip is non-strict.
    stride = contract.PIXEL_STRIDE * LUMA_STRIDE
    luma = sorted(
        sum(weight * channel for weight, channel in zip(LUMA_WEIGHTS, rgb, strict=True))
        for rgb in zip(pixels[0::stride], pixels[1::stride], pixels[2::stride], strict=False)
    )
    if not luma:
        detail = "the captured frame carried no pixels to measure"
        raise device_probes.ProbeError(detail)
    middle = len(luma) // 2
    return luma[middle] if len(luma) % 2 else (luma[middle - 1] + luma[middle]) / 2.0


def component_name(component: str) -> str:
    """Return the `package/class` inside a `ComponentInfo{...}` wrapper, or the text unchanged."""
    stripped = component.strip()
    match = COMPONENT_INFO_PATTERN.match(stripped)
    # `captured_text` is the shared helper for this: a match group is `Any`, and returning it
    # unchanged would let a non-string through a function the arm guard depends on.
    return device_probes.captured_text(match, 1) if match else stripped


def component_is_live(component: str) -> bool:
    """
    Report whether a home-wallpaper component is exactly this app's live service.

    A substring test would admit `ComponentInfo{other.pkg/…AstronomicalClocksWallpaperService}` and
    attribute another wallpaper's CPU and drain to this app.
    """
    return component_name(component) == contract.SERVICE_NAME


def component_is_static(component: str) -> bool:
    """
    Report whether a home-wallpaper component is the platform's own static wallpaper engine.

    Without this, arm B would admit another package's live wallpaper as a baseline. Which static
    *image* the engine carries is a separate, undecidable question this predicate does not claim.
    """
    return component_name(component) == contract.PLATFORM_STATIC_WALLPAPER
