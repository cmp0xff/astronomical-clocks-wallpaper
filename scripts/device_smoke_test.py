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

# Allowed deviation of the measured hand advance from the expected value. The probe localises the
# hand to a fraction of a degree; a larger residual means the clock did not advance as commanded.
ANGLE_TOLERANCE_DEG: Final = 0.5

# The civil hand advances 7.5 degrees per 30 minutes on the 24-hour dial.
EXPECTED_ADVANCE_30M_DEG: Final = 7.500

# Action the debug build listens on to move or reset the wallpaper's virtual clock.
DEBUG_ACTION: Final = "io.github.godaniya.astronomicalclockswallpaper.DEBUG_SET_TIME"

# Log tag the wallpaper service writes under.
SERVICE_LOG_TAG: Final = "AstronomicalClocksWallpaperService"

# `am broadcast` exits 0 even when nothing consumes the intent, so the broadcast alone cannot show
# the clock went back. The service logs this line when it accepts the reset, and the restore path
# requires it to appear.
DEBUG_CLOCK_RESET_EXTRAS: Final = ["--ez", "reset", "true"]
DEBUG_CLOCK_RESET_MESSAGE: Final = "Debug clock reset to system UTC"

# Bound every ADB call: a stalled transport would otherwise block the run forever, after a mutating
# command, and prevent control from reaching the `finally` that restores the device.
ADB_TIMEOUT_SECONDS: Final = 30

# The restore path gets a longer budget than the calls it recovers from: it runs precisely when the
# transport is already slow or wedged, so it must not share the tight limit that triggered it.
ADB_RESTORE_TIMEOUT_SECONDS: Final = 60

# `wm size` prints the physical size first and the active override, if any, after it. Anchoring and
# taking the last match avoids matching a line whose text merely contains the words.
PHYSICAL_SIZE_PATTERN: Final = re.compile(r"^Physical size:\s*(\d+x\d+)$")
OVERRIDE_SIZE_PATTERN: Final = re.compile(r"^Override size:\s*(\d+x\d+)$")

# `dumpsys power` reports wakefulness directly; `dumpsys display` is the fallback for a build that
# does not expose it. Both forms were verified on the target device (API 36).
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

# A device line from `adb devices` needs at least this many whitespace-separated fields before the
# state token is present.
MIN_SPLIT_FIELDS: Final = 2


class ScreencapError(RuntimeError):
    """A screencap that could not be decoded into a frame."""


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
    broadcast_ms: float


@dataclass(frozen=True)
class SmokeOutcome:
    """The measured results of one run, assembled into both the failure list and the report."""

    initial_reset_confirmed: bool
    measurement: AdvanceMeasurement | None
    recreation: SurfaceRecreation
    warnings: Sequence[str]
    log_collection_error: str | None = None


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


def captured_text(match: re.Match[str], group: int) -> str:
    """
    Return one capture group of a successful match.

    typeshed declares `re.Match.group` as returning `Any`, which a checker that requires a concrete
    return type cannot accept. The requested groups capture text, including an
    empty keyguard assignment.
    """
    return str(match.group(group))


def list_devices() -> dict[str, str]:
    """
    Map every serial `adb devices` reports to its state, dropping the header line.

    Each line is split on whitespace and the state token compared exactly. A substring test for
    "device" would also match a serial, a model, or an "unauthorized"/"offline" state.
    """
    output = run_adb(["devices"]).decode("utf-8")
    devices = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= MIN_SPLIT_FIELDS:
            devices[parts[0]] = parts[1]
    return devices


def select_target_serial(requested: str | None) -> str:
    """
    Resolve the one device to operate on, refusing to guess.

    Every later step mutates the device (wake, resize, virtual clock) and the device is shared, so
    an ambiguous list must not silently resolve to its first entry. Exits non-zero on any ambiguity.
    """
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
    """
    Read display sizes; return (None, None) for missing, invalid, or conflicting fields.

    Both are kept because the recreate target must differ from the size actually in effect, which is
    the override when one is active and the physical size otherwise; and only an override needs
    restoring verbatim, as a bare `Physical size:` line is not a state to put back.
    """
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
    """
    Return a `wm size` target that differs from the size in effect, so the resize cannot be a no-op.

    The effective size is the override when active, else the physical size; the two candidate
    targets are opposite, so whichever is effective, the other is used. Checking only the override
    would pick a no-op on a device whose physical size is already the default target. If neither
    size could be parsed, fall back to keying off the override alone rather than crashing.
    """
    effective = override or physical
    return "1080x1800" if effective == "1080x2000" else "1080x2000"


def size_restore_command(override: str | None) -> list[str]:
    """Return the `wm size` invocation that puts the display back to the size the run found."""
    if override:
        return ["shell", "wm", "size", override]
    return ["shell", "wm", "size", "reset"]


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


def read_screen_on(serial: str) -> bool | None:
    """
    Read the screen state, returning None when neither wakefulness nor display state is readable.

    The run wakes the screen, so the state it found must be captured first to put it back. Wakefulness
    is the primary source; the display state is the fallback for a build that does not expose it. An
    unreadable state is reported as None; no sleep is then issued — guessing one could switch off a
    screen the run did not wake — and the restore is reported incomplete rather than assumed clean.
    """
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


def run_restore_command(serial: str, command: Sequence[str]) -> bool:
    """Run one restore command, reporting a failure without raising so the remaining ones still run."""
    try:
        run_adb(command, serial=serial, timeout=ADB_RESTORE_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: restore command failed: {error_detail(error)}", file=sys.stderr)
        return False
    return True


def count_service_log_messages(serial: str, message: str) -> int:
    """Count the current service-tagged logcat lines that contain the given message."""
    output = run_adb(
        ["logcat", "-d", "-s", f"{SERVICE_LOG_TAG}:I"],
        serial=serial,
    ).decode("utf-8")
    return sum(message in line for line in output.splitlines())


def send_debug_clock_broadcast(serial: str, extras: Sequence[str], expected_message: str) -> bool:
    """
    Send a debug-clock broadcast and report whether the service logged a new acceptance line.

    `am broadcast` exits 0 even when no component consumes the intent, so the exit status alone
    cannot show the clock actually moved; the caller must not trust it without this confirmation.
    """
    before = count_service_log_messages(serial, expected_message)
    run_adb(
        ["shell", "am", "broadcast", "-a", DEBUG_ACTION, *extras],
        serial=serial,
        timeout=ADB_RESTORE_TIMEOUT_SECONDS,
    )
    return count_service_log_messages(serial, expected_message) > before


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


def verify_restored_state(serial: str, size_override: str | None, *, screen_was_on: bool | None) -> bool:
    """
    Re-read the mutated settings and report whether each returned to what the run found.

    An unreadable display readback is reported as unrestored rather than compared: with no override
    to compare, `read_display_size`'s `(None, None)` would otherwise equal a baseline of no override
    and pass, reporting a display that was never read as restored.
    """
    try:
        restored_physical_size, restored_size_override = read_display_size(serial)
        restored_screen_on = read_screen_on(serial) if screen_was_on is not None else None
        keyguard_locked = read_keyguard_locked(serial)
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
    if restored_screen_on != screen_was_on:
        restored = False
        print("WARNING: screen state did not return to its initial state", file=sys.stderr)
    if keyguard_locked is not False:
        restored = False
        print("WARNING: device was not confirmed unlocked after the run", file=sys.stderr)
    return restored


def restore_device(serial: str, size_override: str | None, *, screen_was_on: bool | None) -> bool:
    """
    Best-effort reset of every setting the run changes, never raising.

    Called from a `finally`, so a failed step cannot leave the shared device resized, on virtual time,
    or awake when it was found asleep. The display is restored to the override the run found, not
    unconditionally reset, so a pre-existing override is not discarded. The screen is put back to
    sleep only when it was found off (False); a True state issues nothing because the device was
    already awake. An undetermined state also issues nothing — a guessed sleep could switch off a
    screen the run did not wake — but is reported as unrestored, so a run that never read the state
    cannot claim it put the screen back. The clock reset and the size and screen restores are then
    re-read rather than trusted from an exit status: a `wm size` or `am broadcast` that exits 0
    without taking effect is not a restore. A failing restore is reported without masking the
    original exception, which still propagates and keeps the run's non-zero exit code. Returns False
    if any restore failed or could not be verified, so a pass that leaked device state is not
    reported as clean.
    """
    restored = True
    commands = [size_restore_command(size_override)]
    if not confirm_virtual_clock_reset(serial):
        restored = False
    # Last, after the clock reset, so a failure among the earlier commands still attempts it.
    if screen_was_on is False and not sleep_screen(serial):
        restored = False
    for command in commands:
        if not run_restore_command(serial, command):
            restored = False
    # An unreadable initial state cannot be put back: issuing no sleep above may have left a screen
    # this run woke still awake, so report the restore incomplete rather than claiming a clean pass.
    if screen_was_on is None:
        restored = False
        print(
            "WARNING: initial screen state was unreadable; the screen cannot be reported as restored",
            file=sys.stderr,
        )
    if not verify_restored_state(serial, size_override, screen_was_on=screen_was_on):
        restored = False
    return restored


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


def step_reset_clock_and_show_home(serial: str) -> bool:
    """
    Wake the screen, dismiss the keyguard, show home, and confirm the debug clock reset.

    Returns whether the service log confirmed the reset. `am broadcast` exits 0 even when nothing
    consumes it, so a caller that proceeded to the baseline capture and time-travel check regardless
    could compare against a stale or wrong clock without noticing; the caller skips those steps when
    this returns False.
    """
    print("Waking the screen and showing the home screen...")
    run_adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial)
    # A defensive no-op given read_baseline's unlocked precondition: it can only dismiss a transient,
    # non-secure keyguard, and never bypasses a secure lock the run already refused to proceed past.
    run_adb(["shell", "wm", "dismiss-keyguard"], serial=serial)
    run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"], serial=serial)
    time.sleep(1.0)
    print("Resetting virtual clock...")
    confirmed = confirm_virtual_clock_reset(serial)
    time.sleep(0.5)
    return confirmed


def step_time_travel(serial: str) -> AdvanceMeasurement | None:
    """
    Capture the baseline hand, advance the debug clock by a confirmed 30 minutes, and measure it.

    Returns None when either frame fails to isolate the hand, or when the service log did not
    confirm the offset broadcast, so the caller reports an unmeasurable or unconfirmed advance as a
    failure rather than as a zero delta.
    """
    # Capture baseline frame t0
    print("Capturing baseline frame t0...")
    w0, h0, px0 = capture_frame(serial=serial)
    angle0 = detect_hand_angle(w0, h0, px0)
    if angle0 is not None:
        print(f"Baseline hand angle at t0: {angle0:.3f}°")
    else:
        print("Warning: Could not isolate hand pixels at t0 (wallpaper might be obstructed)")

    # Advance virtual time by +30 minutes
    print("Advancing virtual time +30 minutes via debug broadcast...")
    offset_message = "Debug clock offset set to 1800000ms"
    offset_before = count_service_log_messages(serial, offset_message)
    start_t = time.time()
    run_adb(["shell", "am", "broadcast", "-a", DEBUG_ACTION, "--el", "offset_minutes", "30"], serial=serial)
    broadcast_ms = (time.time() - start_t) * 1000.0
    print(f"Time travel completed in {broadcast_ms:.1f}ms")
    time.sleep(0.3)
    if count_service_log_messages(serial, offset_message) <= offset_before:
        print("ERROR: +30m time offset was not confirmed by the service log", file=sys.stderr)
        return None

    # Capture frame t1 at +30 minutes
    print("Capturing frame t1 at +30m...")
    w1, h1, px1 = capture_frame(serial=serial)
    angle1 = detect_hand_angle(w1, h1, px1)
    if angle1 is not None:
        print(f"Hand angle at t1 (+30m): {angle1:.3f}°")
    if angle0 is None or angle1 is None:
        return None

    delta = (angle1 - angle0) % FULL_TURN_DEG
    residual = delta - EXPECTED_ADVANCE_30M_DEG
    print(
        f"Observed angular advance: {delta:.3f}° "
        f"(expected: {EXPECTED_ADVANCE_30M_DEG:.3f}°, residual: {residual:+.3f}°)"
    )
    return AdvanceMeasurement(delta_deg=delta, residual_deg=residual, broadcast_ms=broadcast_ms)


def step_recreate_surface(serial: str, size_override: str | None, recreate_size: str) -> SurfaceRecreation:
    """
    Override the display size, restore it, and report whether it took effect and what was drawn.

    The override is read back while it is in force: a `wm size` that exits 0 without applying leaves
    the dial drawn exactly as before, so the hand alone cannot show that anything was recreated.
    """
    print("Testing surface recreation...")
    run_adb(["shell", "wm", "size", recreate_size], serial=serial)
    time.sleep(0.5)
    _, active_override = read_display_size(serial)
    run_adb(size_restore_command(size_override), serial=serial)
    time.sleep(0.5)
    physical, restored_override = read_display_size(serial)
    if physical is None or restored_override != size_override:
        return SurfaceRecreation(
            override_was_active=active_override == recreate_size,
            restore_was_verified=False,
            hand_angle=None,
        )
    w2, h2, px2 = capture_frame(serial=serial)
    return SurfaceRecreation(
        override_was_active=active_override == recreate_size,
        restore_was_verified=True,
        hand_angle=detect_hand_angle(w2, h2, px2),
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the harness's command-line interface."""
    parser = argparse.ArgumentParser(description="Automated live wallpaper device smoke test")
    parser.add_argument("-s", "--serial", help="ADB device serial", default=None)
    return parser


def read_baseline(requested_serial: str | None) -> SmokeBaseline:
    """Resolve the target and record the device state the run must put back afterwards."""
    target_serial = select_target_serial(requested_serial)
    print(f"Targeting ADB device: {target_serial}")

    # Device-time marker for the logcat filter below. Reading only entries newer than the run start
    # isolates this run's warnings without wiping the shared buffer, which `logcat -c` would do to
    # another session's evidence. The whole format string is one argv element because the device's
    # toybox `date` rejects splitting `+%m-%d` and `%H:%M:%S.000` into two arguments.
    log_start = run_adb(["shell", "date +'%m-%d %H:%M:%S.000'"], serial=target_serial).decode().strip()
    print(f"Logcat start marker: {log_start}")

    # Refuse rather than guess: the recreation target is chosen to differ from the size in effect, so
    # an unreadable size would pick a target that may already be active and turn the resize into a
    # no-op that passes without a recreation.

    # A pre-existing override is put back verbatim rather than reset, so the run leaves the display
    # as it found it. Report it: an override leaked by another session would otherwise be silently
    # reproduced and read as this run's own state.
    physical_size, size_override = read_display_size(target_serial)
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

    # Read the screen state before the first step wakes it, so the run can put back what it found. A
    # device that started asleep must not be left awake after an unattended run, so an unreadable
    # state refuses the run here rather than letting a later KEYCODE_WAKEUP wake a screen whose prior
    # state cannot be put back.
    screen_was_on = read_screen_on(target_serial)
    if screen_was_on is None:
        print(
            "ERROR: could not read the initial screen state; refusing to wake the device.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Require a confirmed-unlocked device before any mutation: an already-locked or unreadable
    # keyguard state is refused rather than dismissed, so the run never bypasses a secure lock it did
    # not independently confirm was already open.
    keyguard_locked = read_keyguard_locked(target_serial)
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


def scan_renderer_log(serial: str, log_start: str) -> list[str]:
    """
    Return this run's renderer warning records, isolated by the device-time marker.

    Both renderer tags are watched: the dial draw logs render failures under DialRenderer, so a
    filter on the service tag alone can miss them and pass a run whose dial threw. The service logs
    no successful tick or draw, so an empty result is expected on a healthy run and is not evidence
    that nothing was drawn; the caller reports that state as inconclusive rather than as a count of
    zero warnings.
    """
    logs = run_adb(
        ["logcat", "-d", "-T", log_start, "-s", f"{SERVICE_LOG_TAG}:W", "DialRenderer:W"],
        serial=serial,
    ).decode("utf-8")
    return [line for line in logs.splitlines() if line.strip() and not line.startswith("---------")]


def collect_failures(outcome: SmokeOutcome, *, restored: bool) -> list[str]:
    """Build the failure list from the measured outcome of the run."""
    failures = []
    if not outcome.initial_reset_confirmed:
        failures.append(
            "initial virtual-clock reset was not confirmed; time travel and surface recreation were skipped"
        )
    else:
        measurement = outcome.measurement
        recreation = outcome.recreation
        if measurement is None:
            failures.append("hand not found; wallpaper must be visible and unobstructed")
        elif abs(measurement.residual_deg) > ANGLE_TOLERANCE_DEG:
            failures.append(f"hand advance residual {measurement.residual_deg:+.3f}° exceeds ±{ANGLE_TOLERANCE_DEG}°")
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
        skip_note = "SKIPPED: initial virtual-clock reset was not confirmed by the service log"
        print(f"| {today} | virtual time travel (+30m) | {skip_note} |")
        print(f"| {today} | surface recreation | {skip_note} |")
    else:
        measurement = outcome.measurement
        recreation = outcome.recreation
        if measurement is not None:
            print(
                f"| {today} | virtual time travel (+30m) | "
                f"Hand advanced {measurement.delta_deg:.3f}° against {EXPECTED_ADVANCE_30M_DEG:.3f}° expected, "
                f"residual {measurement.residual_deg:+.3f}°; broadcast took {measurement.broadcast_ms:.0f}ms |"
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

    # Recreate the surface with a size that differs from the one in effect, so the resize is never a
    # no-op that passes without a recreation. Both sizes are consulted: the override when one is
    # active, else the physical size, so a device already at the default target is exercised too.
    recreate_size = choose_recreate_size(baseline.physical_size, baseline.size_override)

    measurement: AdvanceMeasurement | None = None
    recreation = SurfaceRecreation(override_was_active=False, restore_was_verified=False, hand_angle=None)
    restored = True
    initial_reset_confirmed = False
    # The steps mutate the device (it is woken, and its debug clock is reset) before the surface is
    # recreated, so they run inside the try: with a bounded ADB timeout a stall can raise from any of
    # them, and a raise here must still reach the finally that restores the device.
    try:
        # 1. Reset the clock and show home, confirming the reset before any dependent step runs.
        initial_reset_confirmed = step_reset_clock_and_show_home(baseline.serial)
        if initial_reset_confirmed:
            # 2-3. Advance 30 minutes and measure the advance
            measurement = step_time_travel(baseline.serial)

            # 4. Test surface recreation
            recreation = step_recreate_surface(baseline.serial, baseline.size_override, recreate_size)
        else:
            print(
                "ERROR: initial virtual-clock reset was not confirmed; "
                "skipping time travel and surface recreation checks.",
                file=sys.stderr,
            )
    finally:
        # Restore on both the pass and the failure path, before any exception propagates, so a
        # failed run cannot strand the shared device resized or on virtual time. This also covers
        # the collect-results path, so a pass leaves the device as it was found.
        print("Restoring virtual clock, display size, and screen state...")
        restored = restore_device(baseline.serial, baseline.size_override, screen_was_on=baseline.screen_was_on)

    log_collection_error = None
    try:
        warnings = scan_renderer_log(baseline.serial, baseline.log_start)
    except (subprocess.SubprocessError, OSError) as error:
        warnings = []
        log_collection_error = error_detail(error)
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
