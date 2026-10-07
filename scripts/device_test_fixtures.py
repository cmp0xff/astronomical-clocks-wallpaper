# SPDX-License-Identifier: Apache-2.0
"""Synthetic RGBA frames shared by the host-only harness tests."""

import math
from collections.abc import Sequence
from typing import Final

FRAME_WIDTH: Final = 1080

FRAME_HEIGHT: Final = 2408

BYTES_PER_PIXEL: Final = 4

DARK_GOLD_RGB: Final = (0xD8, 0xB6, 0x6A)

HALF_TURN_DEG: Final = 180.0

FULL_TURN_DEG: Final = 360.0

HAND_FRAME_LENGTH_PX: Final = 300


def palette_frame(
    plate_rgb: tuple[int, int, int],
    glyph_points: Sequence[tuple[int, int]],
    glyph_rgb: tuple[int, int, int] = DARK_GOLD_RGB,
) -> bytes:
    """Build a screencap-shaped RGBA buffer: one plate colour, with glyph_rgb at glyph_points."""
    pixels = bytearray(bytes((*plate_rgb, 255)) * (FRAME_WIDTH * FRAME_HEIGHT))
    for x, y in glyph_points:
        offset = (y * FRAME_WIDTH + x) * BYTES_PER_PIXEL
        pixels[offset : offset + 3] = bytes(glyph_rgb)
    return bytes(pixels)


def hand_frame(plate_rgb: tuple[int, int, int], hand_rgb: tuple[int, int, int], bearing_deg: float) -> bytes:
    """
    Build an RGBA frame of one plate colour with a hand-ink stroke along the given bearing.

    The stroke is three pixels wide and symmetric about the ray, so the hand scan's two-pixel grid
    samples both sides equally and the circular mean lands on the ray instead of being biased to
    whichever side happens to fall on the grid.
    """
    pixels = bytearray(bytes((*plate_rgb, 255)) * (FRAME_WIDTH * FRAME_HEIGHT))
    cx, cy = FRAME_WIDTH / 2.0, FRAME_HEIGHT / 2.0
    radians = math.radians(bearing_deg)
    along = (math.sin(radians), -math.cos(radians))
    across = (math.cos(radians), math.sin(radians))
    for step in range(HAND_FRAME_LENGTH_PX):
        for offset in (-1, 0, 1):
            x = round(cx + step * along[0] + offset * across[0])
            y = round(cy + step * along[1] + offset * across[1])
            if 0 <= x < FRAME_WIDTH and 0 <= y < FRAME_HEIGHT:
                index = (y * FRAME_WIDTH + x) * BYTES_PER_PIXEL
                pixels[index : index + 3] = bytes(hand_rgb)
    return bytes(pixels)


def circular_difference_deg(first: float, second: float) -> float:
    """Return the unsigned angular distance between two bearings, which are circular quantities."""
    return abs((first - second + HALF_TURN_DEG) % FULL_TURN_DEG - HALF_TURN_DEG)
