# SPDX-License-Identifier: Apache-2.0
"""
Shared ADB device layer, dial geometry, and frame analysis for host harnesses.

Provides an object-oriented device abstraction (AdbDevice), dial inspection helpers,
and shared constants used across host-side testing harnesses.
"""

import json
import math
import re
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Final, Self
from uuid import uuid4

# Allowed deviation of the measured hand advance from the expected value.
ANGLE_TOLERANCE_DEG: Final = 0.5

# Expected civil-hand advance for the two virtual-time steps, in degrees on the 24-hour dial.
EXPECTED_ADVANCE_30M_DEG: Final = 7.500
EXPECTED_ADVANCE_12H_DEG: Final = 180.000

# Action the debug build listens on to move or reset the wallpaper's virtual clock.
DEBUG_ACTION: Final = "io.github.godaniya.astronomicalclockswallpaper.DEBUG_SET_TIME"
PACKAGE_NAME: Final = "io.github.godaniya.astronomicalclockswallpaper.debug"
SERVICE_NAME: Final = (
    f"{PACKAGE_NAME}/io.github.godaniya.astronomicalclockswallpaper.AstronomicalClocksWallpaperService"
)
SERVICE_LOG_TAG: Final = "AstronomicalClocksWallpaperService"
DISPLAY_LOG_TAG: Final = "DialDisplay"

DEBUG_CLOCK_RESET_EXTRAS: Final = ["--ez", "reset", "true"]
DEBUG_CLOCK_RESET_MESSAGE: Final = "Debug clock reset to system UTC"

# Bound every ADB call: a stalled transport would otherwise block the run forever.
ADB_TIMEOUT_SECONDS: Final = 30
ADB_RESTORE_TIMEOUT_SECONDS: Final = 60

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

# DialStyle.kt pins the two civil-scale tones: RIM is 0x1C2C39 (dark) and LIGHT_RIM is 0xE8E2D2 (light).
DARK_RIM_RGB: Final = (0x1C, 0x2C, 0x39)
LIGHT_RIM_RGB: Final = (0xE8, 0xE2, 0xD2)

# In /proc/<pid>/stat, fields after the closing parenthesis of comm:
# index 11 is field 14 (utime) and index 12 is field 15 (stime).
PROC_STAT_UTIME_INDEX: Final = 11
PROC_STAT_STIME_INDEX: Final = 12
PROC_STAT_MIN_FIELDS: Final = 13

RIM_PROBE_RADIUS_FRACTION: Final = 0.40
RIM_PROBE_BEARINGS_DEG: Final = tuple(range(15, 360, 30))

# Mirror of DialRenderer.kt, the source of truth: that file's RIM_WIDTH (0.008f) and OUTER_RADIUS
# (1.37f) give DialViewport.STROKED_EXTENT = 1 + RIM_WIDTH / 2 / OUTER_RADIUS. The outer rim is
# stroked half a RIM_WIDTH beyond the reported dial radius, so the drawn disc reaches
# radius * STROKED_EXTENT, not radius. A report can satisfy cx - radius >= 0 while that half stroke is
# clipped - about 1.6 px at Size 115% on a 1080 px display.
RIM_WIDTH: Final = 0.008
OUTER_RADIUS: Final = 1.37
STROKED_EXTENT: Final = 1 + RIM_WIDTH / 2 / OUTER_RADIUS

# The diagnostic prints Float values, so an extent recomputed from the printed radius and the printed
# centre differ by a few hundred-thousandths of a pixel. This absorbs that without hiding the
# stroke-sized clipping the extent check exists to catch.
EXTENT_TOLERANCE_PX: Final = 0.01

HAND_SCAN_OUTER_FRACTION: Final = 0.43
HAND_SCAN_INNER_FRACTION: Final = 0.08
HAND_SCAN_STEP_PX: Final = 2
PIXEL_STRIDE: Final = 4

# Hand-ink bounds in RGB space.
DARK_HAND_RGB_BOUNDS: Final = ((225, 255), (210, 255), (165, 215))
LIGHT_HAND_RGB_BOUNDS: Final = ((55, 105), (35, 80), (15, 50))
DARK_HAND_MIN_RED_MINUS_BLUE: Final = 20

HAND_MIN_SAMPLES: Final = 20
COARSE_BIN_COUNT: Final = 90
COARSE_BIN_WIDTH_DEG: Final = 4.0
REFINE_WEDGE_DEG: Final = 6.0
FULL_TURN_DEG: Final = 360.0
HALF_TURN_DEG: Final = FULL_TURN_DEG / 2

# The observing-site preference mirror. LocationStore.kt writes its record under this file and
# key, and AstronomicalClocksWallpaperService renders each instant through the saved site's zone,
# so the civil hand follows this zone and not the device's. Reading it lets a host harness derive
# the zone its rollover instants must be expressed in. The path targets the debug package because
# only a debuggable build can be read with `run-as`.
LOCATION_PREFS_NAME: Final = "observing_location"
LOCATION_PREFS_KEY: Final = "location"
LOCATION_PREFS_PATH: Final = f"/data/data/{PACKAGE_NAME}/shared_prefs/{LOCATION_PREFS_NAME}.xml"

MIN_BRIGHTNESS: Final = 80
MAX_BRIGHTNESS: Final = 100

SCREENCAP_HEADER_BYTES: Final = 16
MIN_SPLIT_FIELDS: Final = 2


class ScreencapError(RuntimeError):
    """A screencap that could not be decoded into a frame."""


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
    devices: dict[str, str] = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= MIN_SPLIT_FIELDS:
            devices[parts[0]] = parts[1]
    return devices


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


@dataclass(frozen=True)
class DialLayout:
    """Resolved screen-space geometry and dimming from one visible debug engine."""

    cx: float
    cy: float
    radius: float
    brightness: int = 100


class FramePixels(bytes):
    """Captured pixels carrying the fresh diagnostic layout used for their analysis."""

    layout: DialLayout

    def __new__(cls, pixels: bytes, layout: DialLayout) -> Self:
        """Associate captured bytes with their resolved placement."""
        instance = super().__new__(cls, pixels)
        instance.layout = layout
        return instance


def frame_layout(width: int, height: int, pixels: bytes) -> DialLayout:
    """Use attached device diagnostics; plain bytes are centered host-only fixtures."""
    if isinstance(pixels, FramePixels):
        return pixels.layout
    return DialLayout(width / 2.0, height / 2.0, HAND_SCAN_OUTER_FRACTION * min(width, height))


def undim_rgb(rgb: tuple[int, int, int], brightness: int) -> tuple[int, int, int]:
    """Reverse the renderer's quantized black overlay for ink classification."""
    if brightness == MAX_BRIGHTNESS:
        return rgb
    alpha = int((MAX_BRIGHTNESS - brightness) * 255 / MAX_BRIGHTNESS)
    red, green, blue = (min(255, round(channel * 255 / (255 - alpha))) for channel in rgb)
    return red, green, blue


def parse_dial_layout(output: str, token: str, width: int, height: int) -> DialLayout:
    """Require one fresh, usable engine report matching the captured screen size."""
    lines = [line for line in output.splitlines() if f"{DISPLAY_LOG_TAG}: DialLayout token={token} " in line]
    if len(lines) != 1:
        message = f"Expected one fresh visible dial layout, found {len(lines)}"
        raise ScreencapError(message)
    fields = dict(re.findall(r"(\w+)=([^ ]+)", lines[0]))
    try:
        layout = DialLayout(
            float(fields["cx"]), float(fields["cy"]), float(fields["radius"]), int(fields["brightness"])
        )
        reported_size = (int(fields["width"]), int(fields["height"]))
    except (KeyError, ValueError) as error:
        message = "Unreadable or unusable dial layout"
        raise ScreencapError(message) from error
    extent = layout.radius * STROKED_EXTENT
    if (
        reported_size != (width, height)
        or not all(math.isfinite(value) for value in (layout.cx, layout.cy, layout.radius))
        or layout.radius <= 0
        or not MIN_BRIGHTNESS <= layout.brightness <= MAX_BRIGHTNESS
        or layout.cx - extent < -EXTENT_TOLERANCE_PX
        or layout.cy - extent < -EXTENT_TOLERANCE_PX
        or layout.cx + extent > width + EXTENT_TOLERANCE_PX
        or layout.cy + extent > height + EXTENT_TOLERANCE_PX
    ):
        message = "Dial layout does not fit the captured screen"
        raise ScreencapError(message)
    return layout


def read_dial_layout(serial: str | None, width: int, height: int) -> DialLayout:
    """Request diagnostics without changing virtual time; reject ambiguity rather than guessing."""
    token = uuid4().hex
    run_adb(["shell", "am", "broadcast", "-a", DEBUG_ACTION, "--es", "diagnostics", token], serial=serial)
    output = ""
    for attempt in range(LOG_CONFIRM_ATTEMPTS):
        output = run_adb(["logcat", "-d"], serial=serial).decode("utf-8", errors="replace")
        if f"DialLayout token={token} " in output:
            break
        if attempt + 1 < LOG_CONFIRM_ATTEMPTS:
            time.sleep(LOG_CONFIRM_INTERVAL_SECONDS)
    return parse_dial_layout(output, token, width, height)


def rim_probe_points(width: int, height: int, layout: DialLayout | None = None) -> Iterator[tuple[int, int]]:
    """Sample points in the civil-scale annulus, in the dial's own bearing convention."""
    resolved = layout or DialLayout(width / 2.0, height / 2.0, HAND_SCAN_OUTER_FRACTION * min(width, height))
    cx, cy = resolved.cx, resolved.cy
    radius = resolved.radius * RIM_PROBE_RADIUS_FRACTION / HAND_SCAN_OUTER_FRACTION
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
    """Classify the visible palette from the civil scale's own rim tone."""
    layout = frame_layout(width, height, pixels)
    rgb = modal_pixel_rgb(width, pixels, rim_probe_points(width, height, layout))
    return rgb is not None and nearest_rim_rgb(undim_rgb(rgb, layout.brightness)) == DARK_RIM_RGB


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
    layout = frame_layout(width, height, pixels)
    cx, cy = layout.cx, layout.cy
    max_radius = layout.radius
    min_radius = layout.radius * HAND_SCAN_INNER_FRACTION / HAND_SCAN_OUTER_FRACTION
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
                rgb = undim_rgb((pixels[px], pixels[px + 1], pixels[px + 2]), layout.brightness)
                if is_hand_pixel(*rgb):
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
    """Locate the civil hand and return its clockwise angle from the top of the dial."""
    points = collect_hand_points(width, height, pixels, is_dark=is_dark_palette(width, height, pixels))
    if len(points) < HAND_MIN_SAMPLES:
        return None
    angles = [math.degrees(math.atan2(dx, -dy)) % FULL_TURN_DEG for dx, dy in points]
    return refine_hand_angle(angles, coarse_hand_angle(angles))


def choose_recreate_size(physical: str | None, override: str | None) -> str:
    """Return a `wm size` target that differs from the size in effect, so the resize cannot be a no-op."""
    effective = override or physical
    return "1080x1800" if effective == "1080x2000" else "1080x2000"


def size_restore_command(override: str | None) -> list[str]:
    """Return the `wm size` invocation that puts the display back to the size the run found."""
    if override:
        return ["shell", "wm", "size", override]
    return ["shell", "wm", "size", "reset"]


def decode_frame(raw: bytes) -> tuple[int, int, bytes]:
    """Validate a screencap header and RGBA payload before using any pixels."""
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


def capture_frame(serial: str | None = None) -> tuple[int, int, bytes]:
    """Bracket the capture with fresh layout reports, rejecting a layout/surface change."""
    width, height, _ = decode_frame(run_adb(["exec-out", "screencap"], serial=serial))
    layout = read_dial_layout(serial, width, height)
    actual_width, actual_height, pixels = decode_frame(run_adb(["exec-out", "screencap"], serial=serial))
    confirmed = read_dial_layout(serial, actual_width, actual_height)
    if (actual_width, actual_height) != (width, height) or confirmed != layout:
        message = "Dial layout changed during capture; retry on a stable visible surface"
        raise ScreencapError(message)
    return width, height, FramePixels(pixels, layout)


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
    output = run_adb(["shell", "dumpsys", "window"], serial=serial).decode("utf-8")
    readings = [captured_text(match, 1) for match in KEYGUARD_SHOWING_PATTERN.finditer(output)]
    if "true" in readings:
        return True
    if readings and all(reading == "false" for reading in readings):
        return False
    return None


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
        output = run_adb(["shell", "pidof", PACKAGE_NAME], serial=serial).decode().strip()
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


def parse_saved_site_zone(prefs_xml: str) -> str | None:
    """Return the saved observing site's zoneId from the prefs XML, or None when it is unusable."""
    # S314: the XML is the app's own SharedPreferences file, read back from the debug package that
    # wrote it over an authenticated ADB channel; there is no attacker-supplied document here, and
    # the stdlib-only constraint rules out defusedxml.
    try:
        root = ET.fromstring(prefs_xml)  # noqa: S314
    except ET.ParseError:
        return None
    for element in root.iter("string"):
        if element.get("name") != LOCATION_PREFS_KEY:
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
        zone_id = record.get("zoneId")
        return zone_id if isinstance(zone_id, str) and zone_id else None
    return None


def read_saved_site_zone_id(serial: str) -> str | None:
    """Read the saved observing-site zone from the debug package's prefs, or None when absent."""
    try:
        output = run_adb(["shell", "run-as", PACKAGE_NAME, "cat", LOCATION_PREFS_PATH], serial=serial).decode(
            "utf-8", errors="replace"
        )
    except (subprocess.SubprocessError, OSError) as error:
        print(f"WARNING: saved-site prefs probe failed: {error_detail(error)}", file=sys.stderr)
        return None
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
            ["shell", "dumpsys", "activity", "service", SERVICE_NAME],
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


def run_restore_command(serial: str, command: Sequence[str]) -> bool:
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
    return sum(message in line and f"{SERVICE_LOG_TAG}: " in line for line in output.splitlines())


def send_debug_clock_broadcast(serial: str, extras: Sequence[str], expected_message: str) -> bool:
    """Send a debug-clock broadcast and report whether the service logged a new acceptance line."""
    before = count_service_log_messages(serial, expected_message)
    run_adb(
        ["shell", "am", "broadcast", "-a", DEBUG_ACTION, *extras],
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
        confirmed = send_debug_clock_broadcast(serial, DEBUG_CLOCK_RESET_EXTRAS, DEBUG_CLOCK_RESET_MESSAGE)
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

    def run_adb(self, args: Sequence[str], timeout: int = ADB_TIMEOUT_SECONDS) -> bytes:
        """Run an adb invocation targeting this device serial."""
        return run_adb(args, serial=self.serial, timeout=timeout)

    def capture_frame(self) -> tuple[int, int, bytes]:
        """Capture one screencap frame from this device."""
        return capture_frame(serial=self.serial)

    def read_display_size(self) -> tuple[str | None, str | None]:
        """Read physical and override display sizes from `wm size`."""
        return read_display_size(self.serial)

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
        """Read the saved observing-site zone id from this device's debug prefs, or None."""
        return read_saved_site_zone_id(self.serial)

    def read_device_timezone(self) -> str | None:
        """Read this device's system timezone, or None."""
        return read_device_timezone(self.serial)

    def read_wallpaper_visible(self) -> bool | None:
        """Read whether the wallpaper engine is reported visible on this device."""
        return read_wallpaper_visible(self.serial)

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
        """Send KEYCODE_HOME."""
        self.run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"])

    def run_restore_command(self, command: Sequence[str]) -> bool:
        """Run one restore command on this device."""
        return run_restore_command(self.serial, command)

    def count_service_log_messages(self, message: str) -> int:
        """Count service-tagged logcat lines containing the given message."""
        return count_service_log_messages(self.serial, message)

    def send_debug_clock_broadcast(self, extras: Sequence[str], expected_message: str) -> bool:
        """Send a debug-clock broadcast and verify the expected log message."""
        return send_debug_clock_broadcast(self.serial, extras, expected_message)

    def confirm_virtual_clock_reset(self) -> bool:
        """Reset the virtual clock and verify confirmation."""
        return confirm_virtual_clock_reset(self.serial)
