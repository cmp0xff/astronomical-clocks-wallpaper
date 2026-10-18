# SPDX-License-Identifier: Apache-2.0
"""Screencap decoding, dial layout verification, palette classification, and civil-hand vision."""

import math
import re
import struct
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Final, Self
from uuid import uuid4

from host import contract
from host.device import probes

SCREENCAP_HEADER_BYTES: Final = 16

SIZE_RESTORE_PREFIX: Final = ("shell", "wm", "size")


class ScreencapError(RuntimeError):
    """A screencap that could not be decoded into a frame."""


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
    return DialLayout(width / 2.0, height / 2.0, contract.HAND_SCAN_OUTER_FRACTION * min(width, height))


def undim_rgb(rgb: tuple[int, int, int], brightness: int) -> tuple[int, int, int]:
    """Reverse the renderer's quantized black overlay for ink classification."""
    if brightness == contract.MAX_BRIGHTNESS:
        return rgb
    alpha = int((contract.MAX_BRIGHTNESS - brightness) * 255 / contract.MAX_BRIGHTNESS)
    red, green, blue = (min(255, round(channel * 255 / (255 - alpha))) for channel in rgb)
    return red, green, blue


def parse_dial_layout(output: str, token: str, width: int, height: int) -> DialLayout:
    """Require one fresh, usable engine report matching the captured screen size."""
    lines = [line for line in output.splitlines() if f"{contract.DISPLAY_LOG_TAG}: DialLayout token={token} " in line]
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
    extent = layout.radius * contract.STROKED_EXTENT
    if (
        reported_size != (width, height)
        or not all(math.isfinite(value) for value in (layout.cx, layout.cy, layout.radius))
        or layout.radius <= 0
        or not contract.MIN_BRIGHTNESS <= layout.brightness <= contract.MAX_BRIGHTNESS
        or layout.cx - extent < -contract.EXTENT_TOLERANCE_PX
        or layout.cy - extent < -contract.EXTENT_TOLERANCE_PX
        or layout.cx + extent > width + contract.EXTENT_TOLERANCE_PX
        or layout.cy + extent > height + contract.EXTENT_TOLERANCE_PX
    ):
        message = "Dial layout does not fit the captured screen"
        raise ScreencapError(message)
    return layout


def read_dial_layout(serial: str | None, width: int, height: int) -> DialLayout:
    """Request diagnostics without changing virtual time; reject ambiguity rather than guessing."""
    token = uuid4().hex
    probes.run_adb(
        ["shell", "am", "broadcast", "-a", contract.DEBUG_ACTION, "--es", "diagnostics", token],
        serial=serial,
    )
    output = ""
    for attempt in range(probes.LOG_CONFIRM_ATTEMPTS):
        output = probes.run_adb(["logcat", "-d"], serial=serial).decode("utf-8", errors="replace")
        if f"DialLayout token={token} " in output:
            break
        if attempt + 1 < probes.LOG_CONFIRM_ATTEMPTS:
            time.sleep(probes.LOG_CONFIRM_INTERVAL_SECONDS)
    return parse_dial_layout(output, token, width, height)


def rim_probe_points(width: int, height: int, layout: DialLayout | None = None) -> Iterator[tuple[int, int]]:
    """Sample points in the civil-scale annulus, in the dial's own bearing convention."""
    resolved = layout or DialLayout(width / 2.0, height / 2.0, contract.HAND_SCAN_OUTER_FRACTION * min(width, height))
    cx, cy = resolved.cx, resolved.cy
    radius = resolved.radius * contract.RIM_PROBE_RADIUS_FRACTION / contract.HAND_SCAN_OUTER_FRACTION
    for bearing in contract.RIM_PROBE_BEARINGS_DEG:
        radians = math.radians(bearing)
        x = round(cx + radius * math.sin(radians))
        y = round(cy - radius * math.cos(radians))
        if 0 <= x < width and 0 <= y < height:
            yield x, y


def modal_pixel_rgb(width: int, pixels: bytes, points: Iterable[tuple[int, int]]) -> tuple[int, int, int] | None:
    """Return the RGB triple occurring most often among points, or None when there are none."""
    counts = Counter(
        (pixels[offset], pixels[offset + 1], pixels[offset + 2])
        for offset in ((y * width + x) * contract.PIXEL_STRIDE for x, y in points)
    )
    if not counts:
        return None
    return max(counts, key=counts.__getitem__)


def nearest_rim_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Return whichever pinned civil-scale literal is geometrically closer to the sampled colour."""
    dark = sum((channel - target) ** 2 for channel, target in zip(rgb, contract.DARK_RIM_RGB, strict=True))
    light = sum((channel - target) ** 2 for channel, target in zip(rgb, contract.LIGHT_RIM_RGB, strict=True))
    return contract.DARK_RIM_RGB if dark <= light else contract.LIGHT_RIM_RGB


def is_dark_palette(width: int, height: int, pixels: bytes) -> bool:
    """Classify the visible palette from the civil scale's own rim tone."""
    layout = frame_layout(width, height, pixels)
    rgb = modal_pixel_rgb(width, pixels, rim_probe_points(width, height, layout))
    return rgb is not None and nearest_rim_rgb(undim_rgb(rgb, layout.brightness)) == contract.DARK_RIM_RGB


def within_rgb_bounds(channels: tuple[int, int, int], bounds: tuple[tuple[int, int], ...]) -> bool:
    """Report whether every RGB channel falls inside its inclusive bound pair."""
    return all(low <= channel <= high for channel, (low, high) in zip(channels, bounds, strict=True))


def is_dark_hand_pixel(red: int, green: int, blue: int) -> bool:
    """Match DialStyle.HAND #F4E5B8 and its antialiased edge pixels on a dark plate."""
    return (
        within_rgb_bounds((red, green, blue), contract.DARK_HAND_RGB_BOUNDS)
        and red - blue >= contract.DARK_HAND_MIN_RED_MINUS_BLUE
    )


def is_light_hand_pixel(red: int, green: int, blue: int) -> bool:
    """Match DialStyle.LIGHT_HAND #4E341B and its antialiased edge pixels on a light plate."""
    return within_rgb_bounds((red, green, blue), contract.LIGHT_HAND_RGB_BOUNDS) and red > green > blue


def collect_hand_points(width: int, height: int, pixels: bytes, *, is_dark: bool) -> list[tuple[float, float]]:
    """Collect the dial-centre offsets of every hand-ink pixel inside the dial region."""
    layout = frame_layout(width, height, pixels)
    cx, cy = layout.cx, layout.cy
    max_radius = layout.radius
    min_radius = layout.radius * contract.HAND_SCAN_INNER_FRACTION / contract.HAND_SCAN_OUTER_FRACTION
    min_r_sq = min_radius**2
    max_r_sq = max_radius**2

    is_hand_pixel = is_dark_hand_pixel if is_dark else is_light_hand_pixel

    def is_hand_at(x: int, y: int) -> bool:
        offset = (y * width + x) * contract.PIXEL_STRIDE
        rgb = undim_rgb((pixels[offset], pixels[offset + 1], pixels[offset + 2]), layout.brightness)
        return is_hand_pixel(*rgb)

    return [
        (x - cx, y - cy)
        for y in range(int(cy - max_radius), int(cy + max_radius), contract.HAND_SCAN_STEP_PX)
        if 0 <= y < height
        for x in range(int(cx - max_radius), int(cx + max_radius), contract.HAND_SCAN_STEP_PX)
        if 0 <= x < width and min_r_sq <= (x - cx) ** 2 + (y - cy) ** 2 <= max_r_sq and is_hand_at(x, y)
    ]


def civil_hand_angle_deg(seconds_since_local_midnight: float) -> float:
    """Return the absolute civil-hand angle for a local time, clockwise from the top of the dial."""
    return (
        seconds_since_local_midnight / contract.CIVIL_SECONDS_PER_DEGREE + contract.CIVIL_MIDNIGHT_ANGLE_DEG
    ) % contract.FULL_TURN_DEG


def signed_circular_difference_deg(first: float, second: float) -> float:
    """Return first - second as the shortest signed angular difference, in the half-open [-180, 180)."""
    return (first - second + contract.HALF_TURN_DEG) % contract.FULL_TURN_DEG - contract.HALF_TURN_DEG


def coarse_hand_angle(angles: Sequence[float]) -> float:
    """Return the centre of the 4-degree bin holding the most hand pixels."""
    bins = [0] * contract.COARSE_BIN_COUNT
    for angle in angles:
        bins[int(angle // contract.COARSE_BIN_WIDTH_DEG) % contract.COARSE_BIN_COUNT] += 1
    return (bins.index(max(bins)) + 0.5) * contract.COARSE_BIN_WIDTH_DEG


def refine_hand_angle(angles: Sequence[float], coarse_deg: float) -> float | None:
    """Circular mean of the angles within the refine wedge of the coarse estimate, or None."""
    sum_sin = 0.0
    sum_cos = 0.0
    kept = 0
    for angle in angles:
        delta = abs((angle - coarse_deg + contract.HALF_TURN_DEG) % contract.FULL_TURN_DEG - contract.HALF_TURN_DEG)
        if delta <= contract.REFINE_WEDGE_DEG:
            sum_sin += math.sin(math.radians(angle))
            sum_cos += math.cos(math.radians(angle))
            kept += 1
    if kept < contract.HAND_MIN_SAMPLES:
        return None
    return math.degrees(math.atan2(sum_sin, sum_cos)) % contract.FULL_TURN_DEG


def detect_hand_angle(width: int, height: int, pixels: bytes) -> float | None:
    """Locate the civil hand and return its clockwise angle from the top of the dial."""
    points = collect_hand_points(width, height, pixels, is_dark=is_dark_palette(width, height, pixels))
    if len(points) < contract.HAND_MIN_SAMPLES:
        return None
    angles = [math.degrees(math.atan2(dx, -dy)) % contract.FULL_TURN_DEG for dx, dy in points]
    return refine_hand_angle(angles, coarse_hand_angle(angles))


def choose_recreate_size(physical: str | None, override: str | None) -> str:
    """Return a `wm size` target that differs from the size in effect, so the resize cannot be a no-op."""
    effective = override or physical
    return "1080x1800" if effective == "1080x2000" else "1080x2000"


def size_restore_command(override: str | None) -> list[str]:
    """Return the `wm size` invocation that puts the display back to the size the run found."""
    return [*SIZE_RESTORE_PREFIX, override or "reset"]


def decode_frame(raw: bytes) -> tuple[int, int, bytes]:
    """Validate a screencap header and RGBA payload before using any pixels."""
    if len(raw) < SCREENCAP_HEADER_BYTES:
        message = "Failed to capture screencap; payload too small"
        raise ScreencapError(message)
    width, height, _, _ = struct.unpack("<IIII", raw[:SCREENCAP_HEADER_BYTES])
    pixels = raw[SCREENCAP_HEADER_BYTES:]
    expected_bytes = width * height * contract.PIXEL_STRIDE
    if width <= 0 or height <= 0 or len(pixels) != expected_bytes:
        message = f"Unexpected screencap payload size: expected {expected_bytes} pixel bytes, got {len(pixels)}"
        raise ScreencapError(message)
    return width, height, pixels


def capture_frame(serial: str | None = None) -> tuple[int, int, bytes]:
    """Bracket the capture with fresh layout reports, rejecting a layout/surface change."""
    width, height, _ = decode_frame(probes.run_adb(["exec-out", "screencap"], serial=serial))
    layout = read_dial_layout(serial, width, height)
    actual_width, actual_height, pixels = decode_frame(probes.run_adb(["exec-out", "screencap"], serial=serial))
    confirmed = read_dial_layout(serial, actual_width, actual_height)
    if (actual_width, actual_height) != (width, height) or confirmed != layout:
        message = "Dial layout changed during capture; retry on a stable visible surface"
        raise ScreencapError(message)
    return width, height, FramePixels(pixels, layout)
