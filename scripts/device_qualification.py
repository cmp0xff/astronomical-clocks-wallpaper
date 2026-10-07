#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Run observable live-wallpaper checks on one physical Android device via ADB.

This harness does not measure battery or CPU usage, count successful frames while hidden, inspect
engine cleanup, or verify persisted settings after process recreation.
"""

import argparse
import math
import re
import struct
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Final

ANGLE_TOLERANCE_DEG: Final = 0.5
DEBUG_ACTION: Final = "io.github.godaniya.astronomicalclockswallpaper.DEBUG_SET_TIME"
PACKAGE_NAME: Final = "io.github.godaniya.astronomicalclockswallpaper.debug"
SERVICE_NAME: Final = (
    f"{PACKAGE_NAME}/io.github.godaniya.astronomicalclockswallpaper.AstronomicalClocksWallpaperService"
)
SERVICE_LOG_TAG: Final = "AstronomicalClocksWallpaperService"

ADB_TIMEOUT_SECONDS: Final = 30
ADB_RESTORE_TIMEOUT_SECONDS: Final = 60

PHYSICAL_SIZE_PATTERN: Final = re.compile(r"^Physical size:\s*(\d+x\d+)$")
OVERRIDE_SIZE_PATTERN: Final = re.compile(r"^Override size:\s*(\d+x\d+)$")
WAKE_READ_PATTERN: Final = re.compile(r"mWakefulness=(\w+)")
DISPLAY_STATE_PATTERN: Final = re.compile(r"Display State=(ON|OFF)")

# `dumpsys window` prints `isKeyguardShowing=<bool>` from DisplayPolicy.dump() on every AOSP release
# back to at least API 23 (confirmed in frameworks/base's DisplayPolicy.java and its predecessor,
# PhoneWindowManager.java). It is not verified against this project's own target device, so an
# unreadable or unexpected dump is treated as unknown rather than assumed unlocked.
KEYGUARD_SHOWING_PATTERN: Final = re.compile(r"\bisKeyguardShowing[ \t]*=[ \t]*([^\s,;}]*)")

# DialStyle.kt pins the two civil-scale tones, and DialPaletteTest pins both against the palette
# definitions: RIM is 0x1C2C39 for the dark palette and LIGHT_RIM is 0xE8E2D2 for the light one.
DARK_RIM_RGB: Final = (0x1C, 0x2C, 0x39)
LIGHT_RIM_RGB: Final = (0xE8, 0xE2, 0xD2)
RGB_CHANNELS: Final = 3

# DialRenderer maps the sky radius 0.43 * min(w, h) onto OUTER_RADIUS 1.37, so a normalized radius r
# sits at r / 1.37 * 0.43 * min(w, h) pixels from the dial centre. 0.40 * min(w, h) is normalized
# 1.274: clear of the numeral glyph band, which spans roughly 1.156 to 1.254 for NUMERAL_RADIUS
# 1.205 and NUMERAL_SIZE 0.084, and inside the fine muted-gold stroke at
# OUTER_RADIUS - RIM_INSET = 1.344. Everything between them is filled with palette.rim, because the
# plate draws only within SKY_RADIUS 1.0 and the zodiac band stays inside that.
RIM_PROBE_RADIUS_FRACTION: Final = 0.40

# Bearings are spread around the dial so the modal colour survives a local obstruction such as one
# glyph, notification, or launcher icon. Bearing alone does not separate a probe from a numeral: the
# 24 numerals also sit on 15-degree bearings, so the radial separation above, not the bearing, is
# what keeps every probe in the flat band.
RIM_PROBE_BEARINGS_DEG: Final = tuple(range(15, 360, 30))

# DialRenderer maps the sky radius 0.43 * min(w, h) onto OUTER_RADIUS 1.37, so the hand region is
# scanned between the hub (0.08) and the rim inset, and the scan steps two pixels for speed.
HAND_SCAN_OUTER_FRACTION: Final = 0.43
HAND_SCAN_INNER_FRACTION: Final = 0.08
HAND_SCAN_STEP_PX: Final = 2
PIXEL_STRIDE: Final = 4

# DialStyle.HAND #F4E5B8 is (244, 229, 184) and DialStyle.LIGHT_HAND #4E341B is (78, 52, 27). The
# bounds below admit each ink plus its antialiased edge pixels against the plate it is drawn on.
DARK_HAND_RGB_BOUNDS: Final = ((225, 255), (210, 255), (165, 215))
# The pale hand is warm, so a pixel only counts when its channels fall red > green > blue as well.
LIGHT_HAND_RGB_BOUNDS: Final = ((55, 105), (35, 80), (15, 50))
DARK_HAND_MIN_RED_MINUS_BLUE: Final = 20

# The hand is a long straight stroke, so its pixels dominate one coarse bin; the refine wedge then
# keeps stray same-colour pixels elsewhere from biasing the circular mean.
HAND_MIN_SAMPLES: Final = 20
COARSE_BIN_COUNT: Final = 90
COARSE_BIN_WIDTH_DEG: Final = 4.0
REFINE_WEDGE_DEG: Final = 6.0
FULL_TURN_DEG: Final = 360.0
HALF_TURN_DEG: Final = FULL_TURN_DEG / 2

# A screencap frame starts with a 16-byte width/height/format/stride header.
SCREENCAP_HEADER_BYTES: Final = 16

# A device line from `adb devices` and a TOTAL row from `dumpsys meminfo` both need at least this
# many whitespace-separated fields before the value of interest is present.
MIN_SPLIT_FIELDS: Final = 2

# Commands and messages the debug build answers on.
DEBUG_CLOCK_RESET_EXTRAS: Final = ["--ez", "reset", "true"]
DEBUG_CLOCK_RESET_MESSAGE: Final = "Debug clock reset to system UTC"

# The process-rebind phase polls for a new pid this many times at this interval; the report says
# exactly that rather than presenting the window as a measured latency.
REBIND_POLL_COUNT: Final = 10
REBIND_POLL_SECONDS: Final = 0.5

# Expected civil-hand advance for the two virtual-time steps, in degrees on the 24-hour dial.
EXPECTED_ADVANCE_30M_DEG: Final = 7.500
EXPECTED_ADVANCE_12H_DEG: Final = 180.000

# The gap between the two total-PSS samples, in seconds.
PSS_SAMPLE_SECONDS: Final = 10.0


class ScreencapError(RuntimeError):
    """A screencap that could not be decoded into a frame."""


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


def error_detail(error: BaseException) -> str:
    """Format an exception with any captured stderr, which is where ADB explains itself."""
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace").strip()
    if stderr:
        return f"{error}: {stderr}"
    return str(error)


def run_adb(args: Sequence[str], serial: str | None = None, timeout: int = ADB_TIMEOUT_SECONDS) -> bytes:
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
    devices = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= MIN_SPLIT_FIELDS:
            devices[parts[0]] = parts[1]
    return devices


def select_target_serial(requested: str | None) -> str:
    """Resolve the one device to operate on, exiting rather than guessing when it is ambiguous."""
    devices = list_devices()
    active = [serial for serial, state in devices.items() if state == "device"]
    if requested is not None:
        if requested in active:
            return requested
        state = devices.get(requested, "not attached")
        print(f"ERROR: --serial {requested} is not an active device ({state}).", file=sys.stderr)
        sys.exit(1)
    if len(active) == 1:
        return active[0]
    print(f"ERROR: expected exactly one active ADB device but found {len(active)}.", file=sys.stderr)
    sys.exit(1)


def captured_text(match: re.Match[str], group: int) -> str:
    """
    Return one capture group of a successful match.

    typeshed declares `re.Match.group` as returning `Any`, which a checker that requires a concrete
    return type cannot accept. The requested groups capture text, including an
    empty keyguard assignment.
    """
    return str(match.group(group))


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


def choose_recreate_size(physical: str | None, override: str | None) -> str:
    """Pick a `wm size` target that differs from the size in effect, so the resize is never a no-op."""
    effective = override or physical
    return "1080x1800" if effective == "1080x2000" else "1080x2000"


def size_restore_command(override: str | None) -> list[str]:
    """Return the `wm size` invocation that puts the display back to the size the run found."""
    if override:
        return ["shell", "wm", "size", override]
    return ["shell", "wm", "size", "reset"]


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
    """Read the current `cmd uimode night` value, or None when the output does not carry one."""
    output = run_adb(["shell", "cmd", "uimode", "night"], serial=serial).decode("utf-8")
    for line in output.splitlines():
        if "Night mode:" in line:
            return line.split(":", 1)[1].strip()
    return None


def read_keyguard_locked(serial: str) -> bool | None:
    """
    Read whether the keyguard is currently showing, or None when it cannot be determined.

    Any true reading means locked; only valid false readings establish unlocked. Missing or
    malformed values remain unknown, so the harness refuses mutation when it cannot confirm a lock.
    """
    output = run_adb(["shell", "dumpsys", "window"], serial=serial).decode("utf-8")
    readings = [captured_text(match, 1) for match in KEYGUARD_SHOWING_PATTERN.finditer(output)]
    if "true" in readings:
        return True
    if readings and all(reading == "false" for reading in readings):
        return False
    return None


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
        if len(parts) >= MIN_SPLIT_FIELDS and parts[0] == "TOTAL" and parts[1].isdigit():
            return int(parts[1])
    return None


def evaluate_pss_growth(pss_before: int | None, pss_after: int | None, max_growth_kb: int) -> tuple[int, bool] | None:
    """Return (growth, within budget) for two PSS samples, or None when either is missing."""
    if pss_before is None or pss_after is None:
        return None
    growth_kb = pss_after - pss_before
    return growth_kb, growth_kb <= max_growth_kb


def send_debug_clock_broadcast(serial: str, extras: Sequence[str], expected_message: str) -> bool:
    """Send a debug-clock broadcast and report whether the service logged a new acceptance line."""
    before = count_service_log_messages(serial, expected_message)
    run_adb(["shell", "am", "broadcast", "-a", DEBUG_ACTION, *extras], serial=serial)
    return count_service_log_messages(serial, expected_message) > before


def count_service_log_messages(serial: str, message: str) -> int:
    """Count the current service-tagged logcat lines that contain the given message."""
    output = run_adb(
        ["logcat", "-d", "-s", f"{SERVICE_LOG_TAG}:I"],
        serial=serial,
    ).decode("utf-8")
    return sum(message in line for line in output.splitlines())


def matching_renderer_warnings(output: str) -> list[str]:
    """Keep the logcat lines that are records, dropping blanks and the `---------` separators."""
    return [line for line in output.splitlines() if line.strip() and not line.startswith("---------")]


def get_wallpaper_pid(serial: str) -> int | None:
    """Return the wallpaper process id, or None when the package has no live process."""
    try:
        pid_str = run_adb(["shell", "pidof", PACKAGE_NAME], serial=serial).decode().strip()
        pids = [int(p) for p in pid_str.split() if p.isdigit()]
        return pids[0] if pids else None
    except subprocess.CalledProcessError as error:
        stderr = error.stderr
        if stderr and (not isinstance(stderr, str) or stderr.strip()):
            raise
        return None


def capture_frame(serial: str | None = None) -> tuple[int, int, bytes]:
    """Capture one screencap frame as (width, height, RGBA pixels), validating the payload size."""
    raw = run_adb(["exec-out", "screencap"], serial=serial)
    if len(raw) < SCREENCAP_HEADER_BYTES:
        message = "Failed to capture screencap; payload too small"
        raise ScreencapError(message)
    width, height, _, _ = struct.unpack("<IIII", raw[:SCREENCAP_HEADER_BYTES])
    pixels = raw[SCREENCAP_HEADER_BYTES:]
    expected_bytes = width * height * PIXEL_STRIDE
    if width <= 0 or height <= 0 or len(pixels) != expected_bytes:
        message = f"Unexpected screencap payload size: expected {expected_bytes} pixel bytes, got {len(pixels)}"
        raise ScreencapError(message)
    return width, height, pixels


def rim_probe_points(width: int, height: int) -> Iterator[tuple[int, int]]:
    """Sample points in the civil-scale annulus, in the dial's own bearing convention."""
    cx, cy = width / 2.0, height / 2.0
    radius = RIM_PROBE_RADIUS_FRACTION * min(width, height)
    for bearing in RIM_PROBE_BEARINGS_DEG:
        radians = math.radians(bearing)
        x = round(cx + radius * math.sin(radians))
        y = round(cy - radius * math.cos(radians))
        if 0 <= x < width and 0 <= y < height:
            yield x, y


def modal_pixel_rgb(width: int, pixels: bytes, points: Iterable[tuple[int, int]]) -> tuple[int, int, int] | None:
    """Return the RGB triple occurring most often among points, or None when there are none."""
    counts: dict[tuple[int, int, int], int] = {}
    for x, y in points:
        offset = (y * width + x) * PIXEL_STRIDE
        rgb = (pixels[offset], pixels[offset + 1], pixels[offset + 2])
        counts[rgb] = counts.get(rgb, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.__getitem__)


def nearest_rim_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Return whichever pinned civil-scale literal is geometrically closer to the sampled colour."""
    dark = sum((channel - target) ** 2 for channel, target in zip(rgb, DARK_RIM_RGB, strict=True))
    light = sum((channel - target) ** 2 for channel, target in zip(rgb, LIGHT_RIM_RGB, strict=True))
    return DARK_RIM_RGB if dark <= light else LIGHT_RIM_RGB


def is_dark_palette(width: int, height: int, pixels: bytes) -> bool:
    """
    Classify the visible palette from the civil scale's own rim tone.

    The probe samples the annulus rather than the plate margin, as the previous single top-centre
    sample did. That sample sat at normalized radius 1.211, essentially on the numeral ring, and the
    numeral is palette.gold in both themes; a dark frame whose gold glyph dominated the sample was
    read as light, and detect_hand_angle then searched a dark plate for LIGHT_HAND and found no
    hand. Several probes across the annulus, taking the modal colour, cannot land on any one glyph.
    """
    rgb = modal_pixel_rgb(width, pixels, rim_probe_points(width, height))
    return rgb is not None and nearest_rim_rgb(rgb) == DARK_RIM_RGB


def within_rgb_bounds(channels: tuple[int, int, int], bounds: tuple[tuple[int, int], ...]) -> bool:
    """Report whether every RGB channel falls inside its inclusive bound pair."""
    return all(low <= channel <= high for channel, (low, high) in zip(channels, bounds, strict=True))


def is_dark_hand_pixel(red: int, green: int, blue: int) -> bool:
    """Match DialStyle.HAND #F4E5B8 and its antialiased edge pixels on a dark plate."""
    return within_rgb_bounds((red, green, blue), DARK_HAND_RGB_BOUNDS) and red - blue >= DARK_HAND_MIN_RED_MINUS_BLUE


def is_light_hand_pixel(red: int, green: int, blue: int) -> bool:
    """Match DialStyle.LIGHT_HAND #4E341B and its antialiased edge pixels on a light plate."""
    return within_rgb_bounds((red, green, blue), LIGHT_HAND_RGB_BOUNDS) and red > green > blue


def collect_hand_points(width: int, height: int, pixels: bytes, *, is_dark: bool) -> list[tuple[float, float]]:
    """Collect the dial-centre offsets of every hand-ink pixel inside the dial region."""
    cx, cy = width / 2.0, height / 2.0
    max_radius = min(width, height) * HAND_SCAN_OUTER_FRACTION
    min_radius = min(width, height) * HAND_SCAN_INNER_FRACTION
    min_r_sq = min_radius * min_radius
    max_r_sq = max_radius * max_radius

    is_hand_pixel = is_dark_hand_pixel if is_dark else is_light_hand_pixel
    row_stride = width * PIXEL_STRIDE
    points: list[tuple[float, float]] = []
    for y in range(int(cy - max_radius), int(cy + max_radius), HAND_SCAN_STEP_PX):
        if y < 0 or y >= height:
            continue
        dy = y - cy
        row_offset = y * row_stride
        for x in range(int(cx - max_radius), int(cx + max_radius), HAND_SCAN_STEP_PX):
            if x < 0 or x >= width:
                continue
            dx = x - cx
            dist_sq = dx * dx + dy * dy
            if min_r_sq <= dist_sq <= max_r_sq:
                px = row_offset + x * PIXEL_STRIDE
                if is_hand_pixel(pixels[px], pixels[px + 1], pixels[px + 2]):
                    points.append((dx, dy))
    return points


def coarse_hand_angle(angles: Sequence[float]) -> float:
    """Return the centre of the 4-degree bin holding the most hand pixels."""
    bins = [0] * COARSE_BIN_COUNT
    for angle in angles:
        bins[int(angle // COARSE_BIN_WIDTH_DEG) % COARSE_BIN_COUNT] += 1
    return (bins.index(max(bins)) + 0.5) * COARSE_BIN_WIDTH_DEG


def refine_hand_angle(angles: Sequence[float], coarse_deg: float) -> float | None:
    """Circular mean of the angles within the refine wedge of the coarse estimate, or None."""
    sum_sin = 0.0
    sum_cos = 0.0
    kept = 0
    for angle in angles:
        delta = abs((angle - coarse_deg + HALF_TURN_DEG) % FULL_TURN_DEG - HALF_TURN_DEG)
        if delta <= REFINE_WEDGE_DEG:
            sum_sin += math.sin(math.radians(angle))
            sum_cos += math.cos(math.radians(angle))
            kept += 1
    if kept < HAND_MIN_SAMPLES:
        return None
    return math.degrees(math.atan2(sum_sin, sum_cos)) % FULL_TURN_DEG


def detect_hand_angle(width: int, height: int, pixels: bytes) -> float | None:
    """
    Locate the civil hand and return its clockwise angle from the top of the dial.

    Adapts the ink to the theme, matching DialStyle.HAND #F4E5B8 on the dark palette and
    DialStyle.LIGHT_HAND #4E341B on the light one. Returns None when too few pixels match.
    """
    points = collect_hand_points(width, height, pixels, is_dark=is_dark_palette(width, height, pixels))
    if len(points) < HAND_MIN_SAMPLES:
        return None
    angles = [math.degrees(math.atan2(dx, -dy)) % FULL_TURN_DEG for dx, dy in points]
    return refine_hand_angle(angles, coarse_hand_angle(angles))


def run_restore_command(serial: str, command: Sequence[str]) -> bool:
    """Run one restore command, reporting a failure without raising so the remaining ones still run."""
    try:
        run_adb(command, serial=serial, timeout=ADB_RESTORE_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: restore command failed: {error_detail(error)}", file=sys.stderr)
        return False
    return True


def restore_commands(size_override: str | None, initial_night_mode: str | None) -> list[list[str]]:
    """Build the ordered commands that put every mutated setting back."""
    commands = [
        ["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
        size_restore_command(size_override),
    ]
    if initial_night_mode is not None:
        commands.insert(2, ["shell", "cmd", "uimode", "night", initial_night_mode])
    commands.append(["shell", "input", "keyevent", "KEYCODE_HOME"])
    return commands


def confirm_virtual_clock_reset(serial: str) -> bool:
    """Reset the virtual clock and report whether the service log confirmed it."""
    try:
        confirmed = send_debug_clock_broadcast(serial, DEBUG_CLOCK_RESET_EXTRAS, DEBUG_CLOCK_RESET_MESSAGE)
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: virtual-clock reset failed: {error_detail(error)}", file=sys.stderr)
        return False
    if not confirmed:
        print("WARNING: virtual-clock reset was not confirmed", file=sys.stderr)
    return confirmed


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


def verify_restored_state(
    serial: str,
    size_override: str | None,
    *,
    initial_screen_was_on: bool | None,
    initial_night_mode: str | None,
) -> bool:
    """
    Re-read the mutated settings and report whether each returned to what the run found.

    An unreadable display readback is reported as unrestored rather than compared: with no override
    to compare, `read_display_size`'s `(None, None)` would otherwise equal a baseline of no override
    and pass, reporting a display that was never read as restored.
    """
    try:
        restored_physical_size, restored_size_override = read_display_size(serial)
        restored_night_mode = read_night_mode(serial) if initial_night_mode is not None else None
        restored_screen_on = read_screen_on(serial) if initial_screen_was_on is not None else None
        restored_keyguard_locked = read_keyguard_locked(serial)
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: could not verify restored device state: {error_detail(error)}", file=sys.stderr)
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
    serial: str,
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
        if not run_restore_command(serial, command):
            restored = False
    if not confirm_virtual_clock_reset(serial):
        restored = False
    if initial_screen_was_on is False and not sleep_screen(serial):
        restored = False
    if not verify_restored_state(
        serial,
        size_override,
        initial_screen_was_on=initial_screen_was_on,
        initial_night_mode=initial_night_mode,
    ):
        restored = False
    return restored


def phase_environment_setup(serial: str, failures: list[str]) -> bool:
    """
    Wake the device, dismiss the keyguard, show home, and confirm the debug-clock reset.

    Returns whether the reset was confirmed; `am broadcast` exits 0 even when nothing consumes it,
    so a caller that ran the dependent phases regardless could capture a baseline and measure time
    travel against a clock that was never actually reset, reporting a passing residual that proves
    nothing. The caller skips the dependent phases when this returns False.
    """
    print("\n--- Phase 0: Environment Wake & Unlocking ---")
    run_adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial)
    # A defensive no-op given read_device_baseline's unlocked precondition: it can only dismiss a
    # transient, non-secure keyguard, and never bypasses a secure lock the run already refused to
    # proceed past.
    run_adb(["shell", "wm", "dismiss-keyguard"], serial=serial)
    run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"], serial=serial)
    time.sleep(1.0)
    confirmed = send_debug_clock_broadcast(serial, DEBUG_CLOCK_RESET_EXTRAS, DEBUG_CLOCK_RESET_MESSAGE)
    if not confirmed:
        failures.append("Environment reset broadcast was not confirmed by the service log")
    time.sleep(0.5)
    return confirmed


def phase_baseline_capture(serial: str, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Capture one frame and locate the civil hand before any mutating phase."""
    w0, h0, px0 = capture_frame(serial=serial)
    palette_mode = "Dark" if is_dark_palette(w0, h0, px0) else "Light"
    angle0 = detect_hand_angle(w0, h0, px0)
    print(f"Detected dial palette: {palette_mode}")
    if angle0 is not None:
        print(f"Baseline hand angle t0: {angle0:.3f}°")
        results.append(("visible dial baseline", f"Dial rendered ({palette_mode} palette), hand at {angle0:.3f}°"))
    else:
        failures.append("Hand not found at baseline t0")


def phase_screen_off_wake(serial: str, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Sleep the screen, wake it, and confirm the hand is rendered again."""
    print("\n--- Phase 1: Screen-Off / Wake Navigation ---")
    run_adb(["shell", "input", "keyevent", "KEYCODE_SLEEP"], serial=serial)
    time.sleep(4.0)
    screen_went_off = read_screen_on(serial) is False

    # Wake device back up
    run_adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial)
    run_adb(["shell", "wm", "dismiss-keyguard"], serial=serial)
    run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"], serial=serial)
    time.sleep(1.0)

    screen_is_on = read_screen_on(serial) is True
    w_wake, h_wake, px_wake = capture_frame(serial=serial)
    angle_wake = detect_hand_angle(w_wake, h_wake, px_wake)

    if screen_went_off and screen_is_on and angle_wake is not None:
        print(f"Screen-off state observed; hand visible within 1s of wake at {angle_wake:.3f}°.")
        results.append(
            (
                "screen-off / wake navigation",
                (
                    f"device reported screen off for 4s; hand visible within 1s of wake at {angle_wake:.3f}°; "
                    "rendering while asleep was not measured"
                ),
            )
        )
    else:
        failures.append(
            f"Screen-off / wake navigation failed "
            f"(screen_off={screen_went_off}, screen_on_after_wake={screen_is_on}, hand={angle_wake})"
        )


def phase_preview_navigation(serial: str, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Open the live-wallpaper preview, back out, and confirm the home hand renders."""
    print("\n--- Phase 2: Preview Navigation ---")
    preview_start = run_adb(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.service.wallpaper.CHANGE_LIVE_WALLPAPER",
            "-W",
            "--ecn",
            "android.service.wallpaper.extra.LIVE_WALLPAPER_COMPONENT",
            SERVICE_NAME,
        ],
        serial=serial,
    ).decode("utf-8")
    preview_started = "Status: ok" in preview_start
    if not preview_started:
        failures.append("Wallpaper preview did not report a successful launch")
    time.sleep(2.0)

    # Back out of preview to home
    run_adb(["shell", "input", "keyevent", "KEYCODE_BACK"], serial=serial)
    time.sleep(0.8)
    run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"], serial=serial)
    time.sleep(1.0)

    w_preview_back, h_preview_back, px_preview_back = capture_frame(serial=serial)
    angle_post_preview = detect_hand_angle(w_preview_back, h_preview_back, px_preview_back)

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
    serial: str,
    size_override: str | None,
    recreate_size: str,
    results: list[tuple[str, str]],
    failures: list[str],
) -> None:
    """Override the display size and restore it, confirming the dial is redrawn."""
    print("\n--- Phase 3: Surface Recreation ---")
    run_adb(["shell", "wm", "size", recreate_size], serial=serial)
    time.sleep(0.5)
    _, active_override = read_display_size(serial)
    run_adb(size_restore_command(size_override), serial=serial)
    time.sleep(0.8)
    physical, restored_override = read_display_size(serial)
    if physical is None or restored_override != size_override:
        failures.append("Display size was not verified after surface recreation restore")
        return

    w_recreate, h_recreate, px_recreate = capture_frame(serial=serial)
    angle_recreate = detect_hand_angle(w_recreate, h_recreate, px_recreate)
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


def phase_process_rebind(serial: str, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Kill the wallpaper process as its own uid and observe a rebound PID with a drawn hand."""
    print("\n--- Phase 4: Process Recreation (kill -9 simulation) ---")
    pid_before = get_wallpaper_pid(serial)
    pid_after: int | None = None
    angle_post_kill: float | None = None
    if pid_before is None:
        failures.append("Wallpaper process was not running before the process-recreation check")
    else:
        run_adb(["shell", "run-as", PACKAGE_NAME, "kill", "-9", str(pid_before)], serial=serial)
        for _ in range(REBIND_POLL_COUNT):
            time.sleep(REBIND_POLL_SECONDS)
            curr_pid = get_wallpaper_pid(serial)
            if curr_pid is not None and curr_pid != pid_before:
                pid_after = curr_pid
                w_kill, h_kill, px_kill = capture_frame(serial=serial)
                angle_post_kill = detect_hand_angle(w_kill, h_kill, px_kill)
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


def measure_hand_advance(serial: str, offset_args: Sequence[str], expected_message: str) -> tuple[bool, float | None]:
    """Advance the virtual clock by one debug offset, returning (confirmed, hand angle after)."""
    if not send_debug_clock_broadcast(serial, offset_args, expected_message):
        return False, None
    time.sleep(0.3)
    w_after, h_after, px_after = capture_frame(serial=serial)
    return True, detect_hand_angle(w_after, h_after, px_after)


def phase_time_travel(serial: str, results: list[tuple[str, str]], failures: list[str]) -> None:
    """Advance the virtual clock +30m then +12h and compare the measured hand advance."""
    print("\n--- Phase 5: Virtual Time Travel (+30m, +12h) ---")
    if not send_debug_clock_broadcast(serial, DEBUG_CLOCK_RESET_EXTRAS, DEBUG_CLOCK_RESET_MESSAGE):
        failures.append("Time-travel baseline reset was not confirmed by the service log")
        return
    time.sleep(0.8)

    w_base, h_base, px_base = capture_frame(serial=serial)
    a_base = detect_hand_angle(w_base, h_base, px_base)

    # Advance +30 minutes
    confirmed_30m, a_30m = measure_hand_advance(
        serial, ["--el", "offset_minutes", "30"], "Debug clock offset set to 1800000ms"
    )
    if not confirmed_30m:
        failures.append("+30m time offset was not confirmed by the service log")

    # Advance +12 hours
    confirmed_12h, a_12h = measure_hand_advance(
        serial, ["--el", "offset_hours", "12"], "Debug clock offset set to 43200000ms"
    )
    if not confirmed_12h:
        failures.append("+12h time offset was not confirmed by the service log")

    if a_base is not None and a_30m is not None and a_12h is not None:
        delta_30m = (a_30m - a_base) % FULL_TURN_DEG
        res_30m = delta_30m - EXPECTED_ADVANCE_30M_DEG
        delta_12h = (a_12h - a_base) % FULL_TURN_DEG
        res_12h = delta_12h - EXPECTED_ADVANCE_12H_DEG

        print(f"+30m advance: {delta_30m:.3f}° (expected: {EXPECTED_ADVANCE_30M_DEG:.3f}°, residual: {res_30m:+.3f}°)")
        print(f"+12h advance: {delta_12h:.3f}° (expected: {EXPECTED_ADVANCE_12H_DEG:.3f}°, residual: {res_12h:+.3f}°)")

        if abs(res_30m) <= ANGLE_TOLERANCE_DEG and abs(res_12h) <= ANGLE_TOLERANCE_DEG:
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


def phase_total_pss_growth(
    serial: str, max_pss_growth_kb: int, results: list[tuple[str, str]], failures: list[str]
) -> None:
    """Sample total PSS ten seconds apart and compare the growth against the supplied budget."""
    print("\n--- Phase 6: Total PSS Growth ---")
    if not send_debug_clock_broadcast(serial, DEBUG_CLOCK_RESET_EXTRAS, DEBUG_CLOCK_RESET_MESSAGE):
        failures.append("PSS baseline reset was not confirmed by the service log")
        return
    time.sleep(0.5)

    mem_before = run_adb(["shell", "dumpsys", "meminfo", PACKAGE_NAME], serial=serial).decode("utf-8")
    time.sleep(PSS_SAMPLE_SECONDS)
    mem_after = run_adb(["shell", "dumpsys", "meminfo", PACKAGE_NAME], serial=serial).decode("utf-8")

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
    serial: str, run_start_marker: str, results: list[tuple[str, str]], failures: list[str]
) -> bool:
    """
    Scan the run's renderer log lines and append the outcome to the results.

    Return True only when the scan itself completed and matched nothing, which leaves the run
    reporting a conditional pass rather than a verified clean log. A scan that could not be
    collected at all is a failed check, not an inconclusive one: it is evidence the harness never
    examined anything, and recording it as inconclusive let an ADB error exit zero on a run whose
    last phase never ran.
    """
    print("\n--- Phase 7: Renderer Log Scan ---")
    log_scan_inconclusive = False
    try:
        log_check = run_adb(
            ["logcat", "-d", "-T", run_start_marker, "-s", f"{SERVICE_LOG_TAG}:W", "DialRenderer:W"], serial=serial
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
        failures.append(f"Renderer log scan could not be collected: {error_detail(error)}")
        results.append(("renderer log scan", f"Failed to collect: {error_detail(error)}"))
        print(f"Renderer log scan failed: {error_detail(error)}", file=sys.stderr)
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
    serial = select_target_serial(requested_serial)
    print(f"=== Starting Device Checks on target: {serial} ===")
    try:
        run_start_marker = run_adb(["shell", "date +'%m-%d %H:%M:%S.000'"], serial=serial).decode().strip()
        print(f"Run start timestamp: {run_start_marker}")
        physical_size, size_override = read_display_size(serial)
        screen_was_on = read_screen_on(serial)
        night_mode = read_night_mode(serial)
        keyguard_locked = read_keyguard_locked(serial)
        pid_start = get_wallpaper_pid(serial)
    except (subprocess.SubprocessError, OSError) as error:
        print(
            f"ERROR: Could not inspect the device before making changes: {error_detail(error)}",
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

    # Require a confirmed-unlocked device before any mutation: an already-locked or unreadable
    # keyguard state is refused rather than dismissed, so the run never bypasses a secure lock it did
    # not independently confirm was already open.
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
    recreate_size = choose_recreate_size(baseline.physical_size, baseline.size_override)
    serial = baseline.serial

    results: list[tuple[str, str]] = []
    failures: list[str] = []
    restored = True
    current_phase = "environment setup"

    try:
        # Phase 0: Ensure home screen is visible & dismissed keyguard
        current_phase = "environment setup"
        reset_confirmed = phase_environment_setup(serial, failures)

        if reset_confirmed:
            # Baseline capture
            current_phase = "baseline capture"
            phase_baseline_capture(serial, results, failures)

            # Phase 1: Screen-Off / Wake Navigation
            current_phase = "screen-off / wake navigation"
            phase_screen_off_wake(serial, results, failures)

            # Phase 2: Preview Navigation
            current_phase = "preview navigation"
            phase_preview_navigation(serial, results, failures)

            # Phase 3: Surface Recreation (wm size override)
            current_phase = "surface recreation"
            phase_surface_recreation(serial, baseline.size_override, recreate_size, results, failures)

            # Phase 4: Process Recreation (non-stopping SIGKILL, not an LMK run)
            current_phase = "process rebind"
            phase_process_rebind(serial, results, failures)

            # Phase 5: Accelerated Astronomical Time Travel (+30m, +12h)
            current_phase = "virtual time travel"
            phase_time_travel(serial, results, failures)

            # Phase 6: Total PSS growth against an explicitly supplied budget
            current_phase = "total PSS growth"
            phase_total_pss_growth(serial, args.max_pss_growth_kb, results, failures)
        else:
            # The environment reset was not confirmed by the service log, so every phase that
            # compares against a known debug-clock state would measure against an unknown clock and
            # report a meaningless pass. Skip straight to restore and the log scan, which do not
            # depend on the reset.
            print("Skipping phases 1-6: environment reset was not confirmed.", file=sys.stderr)

    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        failures.append(f"Run aborted during {current_phase}: {error_detail(error)}")
    finally:
        print("\n--- Restoring device state in finally block ---")
        restored = restore_device(
            serial,
            baseline.size_override,
            initial_screen_was_on=baseline.screen_was_on,
            initial_night_mode=baseline.night_mode,
        )
    if not restored:
        failures.append("Device restoration failed or could not be verified")

    # Phase 7: Logcat scan
    current_phase = "renderer log scan"
    log_scan_inconclusive = phase_renderer_log_scan(serial, baseline.run_start_marker, results, failures)

    finish_run(results, failures, log_scan_inconclusive=log_scan_inconclusive)


if __name__ == "__main__":
    main()
