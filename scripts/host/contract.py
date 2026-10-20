# SPDX-License-Identifier: Apache-2.0
"""
Cross-language Kotlin↔host contract loaded from `scripts/host_contract.json`.

`HostContractTest` builds that JSON from Kotlin production constants, so this module exposes the
canonical names host harnesses use and validates the file at import. Host-only tolerances that
Kotlin never reads stay literal here.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict, cast

CONTRACT_PATH: Final = Path(__file__).resolve().parent.parent / "host_contract.json"
RGB_CHANNEL_COUNT: Final = 3
RGB_CHANNEL_MAX: Final = 255
SECONDS_IN_30_MINUTES: Final = 30 * 60
SECONDS_IN_12_HOURS: Final = 12 * 3600


class RawDebugExtras(TypedDict):
    """Raw debug broadcast extra names."""

    diagnostics: str
    instant: str
    offset_hours: str
    offset_millis: str
    offset_minutes: str
    offset_seconds: str
    reset: str


class RawLogs(TypedDict):
    """Raw logcat tags, message fragments, and diagnostic prefixes."""

    debug_clock_fixed_instant_prefix: str
    debug_clock_offset_prefix: str
    debug_clock_offset_suffix: str
    debug_clock_reset_message: str
    dial_layout_prefix: str
    display_tag: str
    renderer_tag: str
    service_tag: str


class RawGeometry(TypedDict):
    """Raw dial geometry and civil-scale ratios."""

    civil_midnight_angle_deg: float
    civil_seconds_per_degree: float
    outer_radius: float
    radius_fraction: float
    rim_inset: float
    rim_width: float


class RawDisplaySettings(TypedDict):
    """Raw display brightness bounds."""

    max_brightness: int
    min_brightness: int


class RawPalette(TypedDict):
    """Raw palette tones as RGB channel lists."""

    dark_gold_rgb: list[int]
    dark_hand_rgb: list[int]
    dark_rim_rgb: list[int]
    light_hand_rgb: list[int]
    light_rim_rgb: list[int]


class RawLocationFields(TypedDict):
    """Raw names of the keys in a stored location record."""

    latitude: str
    longitude: str
    source: str
    version: str
    zone_id: str


class RawLocationStore(TypedDict):
    """Raw observing-site preference names and record validation bounds."""

    fields: RawLocationFields
    max_latitude: float
    max_longitude: float
    preferences_name: str
    record_key: str
    record_version: int
    sources: list[str]


class RawHostContract(TypedDict):
    """Raw shape of the checked-in contract file."""

    debug_action: str
    debug_extras: RawDebugExtras
    debug_package_name: str
    display_settings: RawDisplaySettings
    geometry: RawGeometry
    location_store: RawLocationStore
    logs: RawLogs
    palette: RawPalette
    service_class_name: str
    service_name: str


@dataclass(frozen=True)
class DebugExtras:
    """Validated debug broadcast extra names."""

    reset: str
    instant: str
    offset_millis: str
    offset_seconds: str
    offset_minutes: str
    offset_hours: str
    diagnostics: str


@dataclass(frozen=True)
class Logs:
    """Validated logcat tags, message fragments, and diagnostic prefixes."""

    service_tag: str
    display_tag: str
    renderer_tag: str
    dial_layout_prefix: str
    debug_clock_reset_message: str
    debug_clock_fixed_instant_prefix: str
    debug_clock_offset_prefix: str
    debug_clock_offset_suffix: str


@dataclass(frozen=True)
class Geometry:
    """Validated dial geometry and civil-scale ratios."""

    outer_radius: float
    rim_width: float
    rim_inset: float
    radius_fraction: float
    civil_seconds_per_degree: float
    civil_midnight_angle_deg: float


@dataclass(frozen=True)
class DisplaySettings:
    """Validated display brightness bounds."""

    min_brightness: int
    max_brightness: int


@dataclass(frozen=True)
class Palette:
    """Validated palette tones as RGB tuples."""

    dark_rim_rgb: tuple[int, int, int]
    light_rim_rgb: tuple[int, int, int]
    dark_hand_rgb: tuple[int, int, int]
    light_hand_rgb: tuple[int, int, int]
    dark_gold_rgb: tuple[int, int, int]


@dataclass(frozen=True)
class LocationFields:
    """Validated names of the keys in a stored location record."""

    version: str
    latitude: str
    longitude: str
    source: str
    zone_id: str


# LocationStore.load() ignores a record whose version is not the integral 1, whose latitude or
# longitude is not an in-range JSON number, or whose source is not a known name; the service then
# renders the device zone. A harness that read a rejected record's zone would probe a civil midnight
# the service never renders, so probes.parse_saved_site_zone applies the same checks.
@dataclass(frozen=True)
class LocationStore:
    """Validated observing-site preference names and record validation bounds."""

    preferences_name: str
    record_key: str
    record_version: int
    sources: frozenset[str]
    max_latitude: float
    max_longitude: float
    fields: LocationFields


@dataclass(frozen=True)
class HostContract:
    """Validated Kotlin↔host contract."""

    debug_package_name: str
    service_class_name: str
    service_name: str
    debug_action: str
    debug_extras: DebugExtras
    logs: Logs
    geometry: Geometry
    display_settings: DisplaySettings
    palette: Palette
    location_store: LocationStore


def _require_mapping(container: Mapping[str, object], key: str) -> dict[str, object]:
    """Return a non-empty string-keyed mapping entry from a parsed contract object, or raise ValueError."""
    value = container.get(key)
    if not isinstance(value, dict) or not value or not all(isinstance(entry_key, str) for entry_key in value):
        message = f"host contract field {key!r} must be a non-empty JSON object"
        raise ValueError(message)
    return {entry_key: entry_value for entry_key, entry_value in value.items() if isinstance(entry_key, str)}


def _require_str(container: Mapping[str, object], key: str) -> str:
    """Return a non-empty string entry from a parsed contract object, or raise ValueError."""
    value = container.get(key)
    if not isinstance(value, str) or not value:
        message = f"host contract field {key!r} must be a non-empty string"
        raise ValueError(message)
    return value


def _require_int(container: Mapping[str, object], key: str) -> int:
    """Return an integral entry (excluding booleans) from a parsed contract object, or raise TypeError."""
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"host contract field {key!r} must be an integer"
        raise TypeError(message)
    return value


def _require_float(container: Mapping[str, object], key: str) -> float:
    """Return a finite numeric entry (excluding booleans) from a parsed contract object, or raise ValueError."""
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        message = f"host contract field {key!r} must be a finite number"
        raise ValueError(message)
    return float(value)


def _require_rgb(container: Mapping[str, object], key: str) -> tuple[int, int, int]:
    """Return a 3-channel 0..255 RGB tuple from a parsed contract object, or raise ValueError."""
    value = container.get(key)
    if not isinstance(value, list) or len(value) != RGB_CHANNEL_COUNT:
        message = f"host contract field {key!r} must be a 3-element RGB list"
        raise ValueError(message)
    channels: list[int] = []
    for channel in value:
        if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= RGB_CHANNEL_MAX:
            message = f"host contract field {key!r} must hold integers in 0..255"
            raise ValueError(message)
        channels.append(channel)
    return channels[0], channels[1], channels[2]


def _require_str_set(container: Mapping[str, object], key: str) -> frozenset[str]:
    """Return a non-empty frozenset of non-empty strings from a parsed contract object, or raise ValueError."""
    value = container.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        message = f"host contract field {key!r} must be a non-empty list of strings"
        raise ValueError(message)
    return frozenset(item for item in value if isinstance(item, str))


def _load_debug_extras(raw: Mapping[str, object]) -> DebugExtras:
    section = cast("RawDebugExtras", _require_mapping(raw, "debug_extras"))
    return DebugExtras(
        reset=_require_str(section, "reset"),
        instant=_require_str(section, "instant"),
        offset_millis=_require_str(section, "offset_millis"),
        offset_seconds=_require_str(section, "offset_seconds"),
        offset_minutes=_require_str(section, "offset_minutes"),
        offset_hours=_require_str(section, "offset_hours"),
        diagnostics=_require_str(section, "diagnostics"),
    )


def _load_logs(raw: Mapping[str, object]) -> Logs:
    section = cast("RawLogs", _require_mapping(raw, "logs"))
    return Logs(
        service_tag=_require_str(section, "service_tag"),
        display_tag=_require_str(section, "display_tag"),
        renderer_tag=_require_str(section, "renderer_tag"),
        dial_layout_prefix=_require_str(section, "dial_layout_prefix"),
        debug_clock_reset_message=_require_str(section, "debug_clock_reset_message"),
        debug_clock_fixed_instant_prefix=_require_str(section, "debug_clock_fixed_instant_prefix"),
        debug_clock_offset_prefix=_require_str(section, "debug_clock_offset_prefix"),
        debug_clock_offset_suffix=_require_str(section, "debug_clock_offset_suffix"),
    )


def _load_geometry(raw: Mapping[str, object]) -> Geometry:
    section = cast("RawGeometry", _require_mapping(raw, "geometry"))
    return Geometry(
        outer_radius=_require_float(section, "outer_radius"),
        rim_width=_require_float(section, "rim_width"),
        rim_inset=_require_float(section, "rim_inset"),
        radius_fraction=_require_float(section, "radius_fraction"),
        civil_seconds_per_degree=_require_float(section, "civil_seconds_per_degree"),
        civil_midnight_angle_deg=_require_float(section, "civil_midnight_angle_deg"),
    )


def _load_display_settings(raw: Mapping[str, object]) -> DisplaySettings:
    section = cast("RawDisplaySettings", _require_mapping(raw, "display_settings"))
    return DisplaySettings(
        min_brightness=_require_int(section, "min_brightness"),
        max_brightness=_require_int(section, "max_brightness"),
    )


def _load_palette(raw: Mapping[str, object]) -> Palette:
    section = cast("RawPalette", _require_mapping(raw, "palette"))
    return Palette(
        dark_rim_rgb=_require_rgb(section, "dark_rim_rgb"),
        light_rim_rgb=_require_rgb(section, "light_rim_rgb"),
        dark_hand_rgb=_require_rgb(section, "dark_hand_rgb"),
        light_hand_rgb=_require_rgb(section, "light_hand_rgb"),
        dark_gold_rgb=_require_rgb(section, "dark_gold_rgb"),
    )


def _load_location_store(raw: Mapping[str, object]) -> LocationStore:
    section = cast("RawLocationStore", _require_mapping(raw, "location_store"))
    fields = cast("RawLocationFields", _require_mapping(section, "fields"))
    return LocationStore(
        preferences_name=_require_str(section, "preferences_name"),
        record_key=_require_str(section, "record_key"),
        record_version=_require_int(section, "record_version"),
        sources=_require_str_set(section, "sources"),
        max_latitude=_require_float(section, "max_latitude"),
        max_longitude=_require_float(section, "max_longitude"),
        fields=LocationFields(
            version=_require_str(fields, "version"),
            latitude=_require_str(fields, "latitude"),
            longitude=_require_str(fields, "longitude"),
            source=_require_str(fields, "source"),
            zone_id=_require_str(fields, "zone_id"),
        ),
    )


def load_host_contract(path: Path = CONTRACT_PATH) -> HostContract:
    """Load and validate the cross-language Kotlin-to-host contract JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        message = "host contract root must be a JSON object"
        raise TypeError(message)
    contract = cast("RawHostContract", raw)
    debug_package_name = _require_str(contract, "debug_package_name")
    service_class_name = _require_str(contract, "service_class_name")
    service_name = _require_str(contract, "service_name")
    if service_name != f"{debug_package_name}/{service_class_name}":
        message = f"host contract field 'service_name' must be {debug_package_name}/{service_class_name}"
        raise ValueError(message)
    return HostContract(
        debug_package_name=debug_package_name,
        service_class_name=service_class_name,
        service_name=service_name,
        debug_action=_require_str(contract, "debug_action"),
        debug_extras=_load_debug_extras(contract),
        logs=_load_logs(contract),
        geometry=_load_geometry(contract),
        display_settings=_load_display_settings(contract),
        palette=_load_palette(contract),
        location_store=_load_location_store(contract),
    )


CONTRACT: Final = load_host_contract()

# Allowed deviation of the measured hand advance from the expected value.
ANGLE_TOLERANCE_DEG: Final = 0.5

# Expected civil-hand advance for the two virtual-time steps, in degrees on the 24-hour dial.
EXPECTED_ADVANCE_30M_DEG: Final = SECONDS_IN_30_MINUTES / CONTRACT.geometry.civil_seconds_per_degree
EXPECTED_ADVANCE_12H_DEG: Final = SECONDS_IN_12_HOURS / CONTRACT.geometry.civil_seconds_per_degree

DEBUG_ACTION: Final = CONTRACT.debug_action
PACKAGE_NAME: Final = CONTRACT.debug_package_name
SERVICE_NAME: Final = CONTRACT.service_name
# The platform's own static wallpaper engine, read from this device's `dumpsys wallpaper` as both
# `mDefaultWallpaperComponent` and `mImageWallpaper`. It is the component every static baseline in
# `docs/battery-budget.md` is measured against, and naming it is what stops *another package's* live
# service from being admitted as a baseline: `verify_arm` refuses everything that is neither this
# app's service nor this engine. Which static *image* the engine carries is not decidable over ADB
# and is not claimed here; only that the bound component is the platform's own.
PLATFORM_STATIC_WALLPAPER: Final = "com.android.systemui/com.android.systemui.wallpapers.ImageWallpaper"
SERVICE_LOG_TAG: Final = CONTRACT.logs.service_tag
DISPLAY_LOG_TAG: Final = CONTRACT.logs.display_tag

DEBUG_CLOCK_RESET_EXTRAS: Final = ["--ez", CONTRACT.debug_extras.reset, "true"]
DEBUG_CLOCK_RESET_MESSAGE: Final = CONTRACT.logs.debug_clock_reset_message

DARK_RIM_RGB: Final = CONTRACT.palette.dark_rim_rgb
LIGHT_RIM_RGB: Final = CONTRACT.palette.light_rim_rgb

RIM_PROBE_RADIUS_FRACTION: Final = 0.40
RIM_PROBE_BEARINGS_DEG: Final = tuple(range(15, 360, 30))

# Mirror of DialRenderer.kt, the source of truth: that file's RIM_WIDTH (0.008f) and OUTER_RADIUS
# (1.37f) give DialViewport.STROKED_EXTENT = 1 + RIM_WIDTH / 2 / OUTER_RADIUS. The outer rim is
# stroked half a RIM_WIDTH beyond the reported dial radius, so the drawn disc reaches
# radius * STROKED_EXTENT, not radius. A report can satisfy cx - radius >= 0 while that half stroke is
# clipped - about 1.6 px at Size 115% on a 1080 px display.
RIM_WIDTH: Final = CONTRACT.geometry.rim_width
OUTER_RADIUS: Final = CONTRACT.geometry.outer_radius
STROKED_EXTENT: Final = 1 + RIM_WIDTH / 2 / OUTER_RADIUS

# The diagnostic prints Float values, so an extent recomputed from the printed radius and the printed
# centre differ by a few hundred-thousandths of a pixel. This absorbs that without hiding the
# stroke-sized clipping the extent check exists to catch.
EXTENT_TOLERANCE_PX: Final = 0.01

HAND_SCAN_OUTER_FRACTION: Final = CONTRACT.geometry.radius_fraction
HAND_SCAN_INNER_FRACTION: Final = 0.08
HAND_SCAN_STEP_PX: Final = 2
PIXEL_STRIDE: Final = 4

# Host-only antialiasing bounds around the contract's exact hand colors.
DARK_HAND_RGB_BOUNDS: Final = ((225, 255), (210, 255), (165, 215))
LIGHT_HAND_RGB_BOUNDS: Final = ((55, 105), (35, 80), (15, 50))
DARK_HAND_MIN_RED_MINUS_BLUE: Final = 20

HAND_MIN_SAMPLES: Final = 20
COARSE_BIN_COUNT: Final = 90
COARSE_BIN_WIDTH_DEG: Final = 4.0
REFINE_WEDGE_DEG: Final = 6.0
FULL_TURN_DEG: Final = 360.0
HALF_TURN_DEG: Final = FULL_TURN_DEG / 2

# Mirror of CivilDialConstants.kt and ClockState.kt, the source of truth: ClockState derives the
# 24-hour hand angle as (seconds_since_local_midnight / SECONDS_PER_DEGREE + MIDNIGHT_ANGLE_DEG) mod
# 360, clockwise from screen up, so midnight is 180°, noon 0°, 06:00 270°, and 18:00 90° (pinned by
# ClockStateTest.kt). detect_hand_angle returns that same convention, so no offset conversion is
# needed here.
CIVIL_SECONDS_PER_DEGREE: Final = CONTRACT.geometry.civil_seconds_per_degree
CIVIL_MIDNIGHT_ANGLE_DEG: Final = CONTRACT.geometry.civil_midnight_angle_deg

# LocationStore.kt writes its record under this file and key, and the wallpaper renders each instant
# through the saved site's zone, so the civil hand follows that zone and not the device's. The path
# targets the debug package because only a debuggable build can be read with `run-as`.
LOCATION_PREFS_PATH: Final = f"/data/data/{PACKAGE_NAME}/shared_prefs/{CONTRACT.location_store.preferences_name}.xml"

# `run-as <pkg> cat <prefs>` exits non-zero both when the package or transport cannot be entered and
# when the prefs file has never been written. Only the latter confirms the store holds no saved site,
# so it is recognised by this strerror token naming that very path; an ENOENT about any other path is
# a probe error, not an absence. The token is Android/locale dependent, so a miss raises a probe error
# and fails the phase loudly — never a false "no saved site" that would silently fall back to the
# device timezone and probe the wrong midnight.
PREFS_FILE_ABSENT_MARKER: Final = "No such file or directory"

MIN_BRIGHTNESS: Final = CONTRACT.display_settings.min_brightness
MAX_BRIGHTNESS: Final = CONTRACT.display_settings.max_brightness


def debug_offset_message(offset_millis: int) -> str:
    """Format the debug clock offset confirmation message from the shared host contract."""
    return f"{CONTRACT.logs.debug_clock_offset_prefix}{offset_millis}{CONTRACT.logs.debug_clock_offset_suffix}"


def debug_fixed_instant_message(instant_str: str) -> str:
    """Format the debug clock fixed-instant confirmation message from the shared host contract."""
    return f"{CONTRACT.logs.debug_clock_fixed_instant_prefix}{instant_str}"
