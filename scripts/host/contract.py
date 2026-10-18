# SPDX-License-Identifier: Apache-2.0
"""
Shared cross-language contract constants, dial geometry, and palette literals for host harnesses.

Mirrors the package/service identifiers, debug broadcast actions, log tags, civil-scale palette tones,
dial geometry ratios, and preference keys that the Android app and host harnesses share.
"""

from typing import Final

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
# The platform's own static wallpaper engine, read from this device's `dumpsys wallpaper` as both
# `mDefaultWallpaperComponent` and `mImageWallpaper`. It is the component every static baseline in
# `docs/battery-budget.md` is measured against, and naming it is what stops *another package's* live
# service from being admitted as a baseline: `verify_arm` refuses everything that is neither this
# app's service nor this engine. Which static *image* the engine carries is not decidable over ADB
# and is not claimed here; only that the bound component is the platform's own.
PLATFORM_STATIC_WALLPAPER: Final = "com.android.systemui/com.android.systemui.wallpapers.ImageWallpaper"
SERVICE_LOG_TAG: Final = "AstronomicalClocksWallpaperService"
DISPLAY_LOG_TAG: Final = "DialDisplay"

DEBUG_CLOCK_RESET_EXTRAS: Final = ["--ez", "reset", "true"]
DEBUG_CLOCK_RESET_MESSAGE: Final = "Debug clock reset to system UTC"

# DialStyle.kt pins the two civil-scale tones: RIM is 0x1C2C39 (dark) and LIGHT_RIM is 0xE8E2D2 (light).
DARK_RIM_RGB: Final = (0x1C, 0x2C, 0x39)
LIGHT_RIM_RGB: Final = (0xE8, 0xE2, 0xD2)

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

# Mirror of CivilDialConstants.kt and ClockState.kt, the source of truth: ClockState derives the
# 24-hour hand angle as (seconds_since_local_midnight / SECONDS_PER_DEGREE + MIDNIGHT_ANGLE_DEG) mod
# 360, clockwise from screen up, so midnight is 180°, noon 0°, 06:00 270°, and 18:00 90° (pinned by
# ClockStateTest.kt). detect_hand_angle returns that same convention, so no offset conversion is
# needed here. The names are prefixed to keep them distinct from the Kotlin-origin constants mirrored.
CIVIL_SECONDS_PER_DEGREE: Final = 240.0
CIVIL_MIDNIGHT_ANGLE_DEG: Final = 180.0

# The observing-site preference mirror. LocationStore.kt writes its record under this file and
# key, and AstronomicalClocksWallpaperService renders each instant through the saved site's zone,
# so the civil hand follows this zone and not the device's. Reading it lets a host harness derive
# the zone its rollover instants must be expressed in. The path targets the debug package because
# only a debuggable build can be read with `run-as`.
LOCATION_PREFS_NAME: Final = "observing_location"
LOCATION_PREFS_KEY: Final = "location"
LOCATION_PREFS_PATH: Final = f"/data/data/{PACKAGE_NAME}/shared_prefs/{LOCATION_PREFS_NAME}.xml"

# `run-as <pkg> cat <prefs>` exits non-zero both when the package or transport cannot be entered and
# when the prefs file has never been written. Only the latter confirms the store holds no saved site,
# so it is recognised by this strerror token naming that very path; an ENOENT about any other path is
# a probe error, not an absence. The token is Android/locale dependent, so a miss raises a probe error
# and fails the phase loudly — never a false "no saved site" that would silently fall back to the
# device timezone and probe the wrong midnight.
PREFS_FILE_ABSENT_MARKER: Final = "No such file or directory"

# Mirror of LocationStore.kt and ObservingLocation.kt record validation, the source of truth.
# LocationStore.load() ignores a record whose version is not the integral 1, whose latitude or
# longitude is not an in-range JSON number, or whose source is not one of these names; the service
# then renders the device zone. It renders the device zone for an absent or unbuildable zoneId as
# well, and only the absent case is mirrored here (an unbuildable id is the phase's to reject; see
# saved_record_zone_id). A harness that read a rejected record's zone would probe a civil midnight
# the service never renders, so parse_saved_site_zone applies the same checks.
LOCATION_RECORD_VERSION: Final = 1
LOCATION_SOURCES: Final = frozenset({"CURRENT_COARSE", "MANUAL"})
LOCATION_MAX_LATITUDE: Final = 90.0
LOCATION_MAX_LONGITUDE: Final = 180.0

MIN_BRIGHTNESS: Final = 80
MAX_BRIGHTNESS: Final = 100
