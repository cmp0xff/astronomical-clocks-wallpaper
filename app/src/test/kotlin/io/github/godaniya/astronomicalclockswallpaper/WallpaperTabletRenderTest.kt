package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Bitmap
import android.graphics.Canvas
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneOffset
import kotlin.math.roundToInt

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class WallpaperTabletRenderTest {
    @Test
    fun surfacesHaveZeroRimClipping() {
        for (surface in TabletSurfaces.ALL) {
            val bitmap = renderDialBitmap(surface = surface, geometry = PRAGUE_GEOMETRY, layers = ALL_LAYERS)
            try {
                assertZeroRimClipping(bitmap = bitmap, surface = surface)
            } finally {
                bitmap.recycle()
            }
        }
    }

    @Test
    fun layersRenderIndependently() {
        for (surface in TabletSurfaces.ALL) {
            val viewport = DialViewport.full(width = surface.width, height = surface.height)
            val placement = requireNotNull(viewport.resolve(DialDisplaySettings()))

            assertZodiacRingRenders(surface = surface, placement = placement)
            assertMoonMarkerRenders(surface = surface, placement = placement)
            assertSunMarkerRenders(surface = surface, placement = placement)
        }
    }

    private fun assertZodiacRingRenders(surface: TabletSurface, placement: DialPlacement) {
        val base = renderDialBitmap(surface = surface, geometry = PRAGUE_GEOMETRY, layers = NO_LAYERS)
        val zodiac = renderDialBitmap(surface = surface, geometry = PRAGUE_GEOMETRY, layers = ZODIAC_ONLY)
        try {
            assertFalse("Zodiac ring must produce pixel changes on ${surface.name}", zodiac.sameAs(base))
            val point = OrlojProjection(PRAGUE_GEOMETRY).eclipticPoint(0.0)
            val changed = changedPixelsNear(first = zodiac, second = base, point = point, placement = placement)
            assertTrue("Zodiac ring must render pixels near ecliptic 0° on ${surface.name}", changed > 0)
        } finally {
            base.recycle()
            zodiac.recycle()
        }
    }

    private fun assertMoonMarkerRenders(surface: TabletSurface, placement: DialPlacement) {
        val base = renderDialBitmap(surface = surface, geometry = PRAGUE_GEOMETRY, layers = NO_LAYERS)
        val moon = renderDialBitmap(surface = surface, geometry = PRAGUE_GEOMETRY, layers = MOON_ONLY)
        try {
            assertFalse("Moon marker must produce pixel changes on ${surface.name}", moon.sameAs(base))
            val moonPoint = requireNotNull(OrlojProjection(PRAGUE_GEOMETRY).moonPoint)
            val changed = changedPixelsNear(first = moon, second = base, point = moonPoint, placement = placement)
            assertTrue("Moon marker must render pixels near moon position on ${surface.name}", changed > 0)
        } finally {
            base.recycle()
            moon.recycle()
        }
    }

    private fun assertSunMarkerRenders(surface: TabletSurface, placement: DialPlacement) {
        val sunA = PRAGUE_GEOMETRY.copy(sunLongitudeDeg = 0.0)
        val sunB = PRAGUE_GEOMETRY.copy(sunLongitudeDeg = 180.0)
        val bitmapA = renderDialBitmap(surface = surface, geometry = sunA, layers = SUN_ONLY)
        val bitmapB = renderDialBitmap(surface = surface, geometry = sunB, layers = SUN_ONLY)
        try {
            assertFalse(
                "Sun marker position change must produce pixel changes on ${surface.name}",
                bitmapA.sameAs(bitmapB),
            )
            val projA = OrlojProjection(sunA)
            val projB = OrlojProjection(sunB)
            val changedA =
                changedPixelsNear(
                    first = bitmapA,
                    second = bitmapB,
                    point = projA.eclipticPoint(0.0),
                    placement = placement,
                )
            val changedB =
                changedPixelsNear(
                    first = bitmapA,
                    second = bitmapB,
                    point = projB.eclipticPoint(180.0),
                    placement = placement,
                )
            assertTrue("Sun marker must render pixels at 0° position on ${surface.name}", changedA > 0)
            assertTrue("Sun marker must render pixels at 180° position on ${surface.name}", changedB > 0)
        } finally {
            bitmapA.recycle()
            bitmapB.recycle()
        }
    }

    private fun assertZeroRimClipping(bitmap: Bitmap, surface: TabletSurface) {
        val bg = DialStyle.DARK_PALETTE.background
        val w = surface.width
        val h = surface.height

        val cx = w / 2
        val cy = h / 2
        assertEquals("Top edge midpoint must be background", bg, bitmap.getPixel(cx, 0))
        assertEquals("Bottom edge midpoint must be background", bg, bitmap.getPixel(cx, h - 1))
        assertEquals("Left edge midpoint must be background", bg, bitmap.getPixel(0, cy))
        assertEquals("Right edge midpoint must be background", bg, bitmap.getPixel(w - 1, cy))

        for (x in 0 until w) {
            assertEquals("Top edge at ($x, 0) clipped dial", bg, bitmap.getPixel(x, 0))
            assertEquals("Bottom edge at ($x, ${h - 1}) clipped dial", bg, bitmap.getPixel(x, h - 1))
        }
        for (y in 0 until h) {
            assertEquals("Left edge at (0, $y) clipped dial", bg, bitmap.getPixel(0, y))
            assertEquals("Right edge at (${w - 1}, $y) clipped dial", bg, bitmap.getPixel(w - 1, y))
        }
    }

    private fun changedPixelsNear(first: Bitmap, second: Bitmap, point: DialPoint, placement: DialPlacement): Int {
        val scale = placement.radius / DialRenderer.OUTER_RADIUS
        val cx = (placement.centerX + point.x * scale).roundToInt()
        val cy = (placement.centerY + point.y * scale).roundToInt()
        var changed = 0
        for (dx in -PROBE_RADIUS..PROBE_RADIUS) {
            for (dy in -PROBE_RADIUS..PROBE_RADIUS) {
                val x = cx + dx
                val y = cy + dy
                if (x in 0 until first.width &&
                    y in 0 until first.height &&
                    first.getPixel(x, y) != second.getPixel(x, y)
                ) {
                    changed++
                }
            }
        }
        return changed
    }

    private fun renderDialBitmap(surface: TabletSurface, geometry: DialGeometry?, layers: DialLayers): Bitmap {
        val bitmap = Bitmap.createBitmap(surface.width, surface.height, Bitmap.Config.ARGB_8888)
        val viewport = DialViewport.full(width = surface.width, height = surface.height)
        val style =
            DialRenderStyle(
                palette = DialStyle.DARK_PALETTE,
                display = DialDisplaySettings(),
                viewport = viewport,
            )
        DialRenderer().renderDisplay(
            canvas = Canvas(bitmap),
            state = clockState(LocalTime.of(12, 0)),
            geometry = geometry,
            layers = layers,
            style = style,
        )
        return bitmap
    }

    private companion object {
        const val PROBE_RADIUS = 12

        val PRAGUE_LOCATION =
            ObservingLocation(
                latitude = 50.0875,
                longitude = 14.4213,
                zoneId = ZoneOffset.ofHours(1),
                source = ObservingLocation.Source.MANUAL,
            )

        val PRAGUE_GEOMETRY: DialGeometry =
            AstronomyEngineCalculator().dialGeometry(
                time = Instant.parse("2026-06-21T12:00:00Z"),
                location = PRAGUE_LOCATION,
            )

        val ALL_LAYERS = DialLayers(isZodiacRingEnabled = true, isSunEnabled = true, isMoonEnabled = true)
        val NO_LAYERS = DialLayers(isZodiacRingEnabled = false, isSunEnabled = false, isMoonEnabled = false)
        val ZODIAC_ONLY = DialLayers(isZodiacRingEnabled = true, isSunEnabled = false, isMoonEnabled = false)
        val MOON_ONLY = DialLayers(isZodiacRingEnabled = false, isSunEnabled = false, isMoonEnabled = true)
        val SUN_ONLY = DialLayers(isZodiacRingEnabled = false, isSunEnabled = true, isMoonEnabled = false)
    }
}
