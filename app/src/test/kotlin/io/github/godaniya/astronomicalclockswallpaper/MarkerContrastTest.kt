package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Bitmap
import android.graphics.Canvas
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.LocalTime
import kotlin.math.roundToInt

/** Differential native Canvas probes distinguish marker boundaries from underlying plate ink. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class MarkerContrastTest {
    @Test
    fun fullMoonBoundaryOnBand() {
        for (palette in PALETTES) {
            val geometry = BASE_GEOMETRY.copy(moonPhaseLongitudeDeg = 180.0)
            val point = OrlojProjection(geometry).moonPoint!!
            val withMoon = render(geometry, palette, isRingEnabled = true)
            val bandOnly = render(geometry.copy(moonLongitudeDeg = null), palette, isRingEnabled = true)
            assertEquals(palette.moonIlluminated, pixelAt(withMoon, point))
            assertTrue(
                "Full Moon needs a drawn gold boundary on the band",
                changedInkPixels(
                    marker = withMoon,
                    base = bandOnly,
                    point = point,
                    ink = palette.gold,
                    surface = palette.zodiacBand,
                ) >= MIN_BOUNDARY_PIXELS,
            )
        }
    }

    @Test
    fun ringDisabledMarkerBoundaries() {
        for (palette in PALETTES) {
            for ((siderealAngle, surface) in listOf(240.0 to palette.night, 180.0 to palette.twilight)) {
                val geometry = BASE_GEOMETRY.copy(localSiderealAngleDeg = siderealAngle)
                val point = OrlojProjection(geometry).moonPoint!!
                val plateOnly = render(geometry.copy(moonLongitudeDeg = null), palette, isRingEnabled = false)
                assertEquals("Probe must lie in the intended plate region", surface, pixelAt(plateOnly, point))
                val withMoon = render(geometry, palette, isRingEnabled = false)
                assertBoundary(
                    marker = withMoon,
                    plate = plateOnly,
                    point = point,
                    palette = palette,
                    surface = surface,
                )
                val withSun =
                    render(
                        geometry.copy(sunLongitudeDeg = 60.0, moonLongitudeDeg = null),
                        palette,
                        isRingEnabled = false,
                    )
                assertBoundary(marker = withSun, plate = plateOnly, point = point, palette = palette, surface = surface)
            }
        }
    }

    private fun assertBoundary(marker: Bitmap, plate: Bitmap, point: DialPoint, palette: DialPalette, surface: Int) {
        for (ink in listOf(palette.casing, palette.hand)) {
            assertTrue(
                "Ring-disabled marker must draw outline ink $ink over $surface",
                changedInkPixels(
                    marker = marker,
                    base = plate,
                    point = point,
                    ink = ink,
                    surface = surface,
                ) >= MIN_BOUNDARY_PIXELS,
            )
        }
    }

    private fun render(geometry: DialGeometry, palette: DialPalette, isRingEnabled: Boolean): Bitmap {
        val bitmap = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        DialRenderer().renderDial(
            canvas = Canvas(bitmap),
            state = clockState(LocalTime.NOON),
            geometry = geometry,
            layers = DialLayers(isZodiacRingEnabled = isRingEnabled),
            palette = palette,
        )
        return bitmap
    }

    private fun pixelAt(bitmap: Bitmap, point: DialPoint): Int =
        bitmap.getPixel((CENTER + point.x * SKY_RADIUS).roundToInt(), (CENTER + point.y * SKY_RADIUS).roundToInt())

    private fun changedInkPixels(marker: Bitmap, base: Bitmap, point: DialPoint, ink: Int, surface: Int): Int {
        val x = (CENTER + point.x * SKY_RADIUS).roundToInt()
        val y = (CENTER + point.y * SKY_RADIUS).roundToInt()
        var count = 0
        for (dx in -PROBE_RADIUS..PROBE_RADIUS) {
            for (dy in -PROBE_RADIUS..PROBE_RADIUS) {
                if (base.getPixel(x + dx, y + dy) == surface && marker.getPixel(x + dx, y + dy) == ink) {
                    count++
                }
            }
        }
        return count
    }

    private companion object {
        const val SIZE = 1600
        const val CENTER = SIZE / 2.0
        const val SKY_RADIUS = SIZE * 0.43 / 1.37
        const val PROBE_RADIUS = 28
        const val MIN_BOUNDARY_PIXELS = 8
        val BASE_GEOMETRY =
            DialGeometry(
                localSiderealAngleDeg = 0.0,
                trueObliquityDeg = 23.44,
                latitudeDeg = 50.08,
                moonLongitudeDeg = 60.0,
                moonPhaseLongitudeDeg = 0.0,
            )
        val PALETTES = listOf(DialStyle.LIGHT_PALETTE, DialStyle.DARK_PALETTE)
    }
}
