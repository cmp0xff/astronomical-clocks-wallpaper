package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Insets
import android.graphics.Rect
import android.os.Build
import android.view.WindowInsets
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.util.ReflectionHelpers
import org.robolectric.util.ReflectionHelpers.ClassParameter
import java.time.LocalTime

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class DialDisplayRenderTest {
    @Test
    fun nativeCornerPlacement() {
        val viewport = DialViewport(left = 23f, top = 41f, right = 600f, bottom = 720f)
        for (size in listOf(50, 100, 115)) {
            val centred = requireNotNull(viewport.resolve(DialDisplaySettings(size = size)))
            assertEquals((viewport.left + viewport.right) / 2f, centred.centerX, PLACEMENT_TOLERANCE)
            assertEquals((viewport.top + viewport.bottom) / 2f, centred.centerY, PLACEMENT_TOLERANCE)
            for (position in listOf(0 to 0, 0 to 100, 100 to 0, 100 to 100)) {
                val settings = DialDisplaySettings(size = size, horizontal = position.first, vertical = position.second)
                val placement = requireNotNull(viewport.resolve(settings))
                val extent = placement.radius * DialViewport.STROKED_EXTENT
                assertTrue(placement.centerX - extent >= viewport.left - 0.001f)
                assertTrue(placement.centerY - extent >= viewport.top - 0.001f)
                assertTrue(placement.centerX + extent <= viewport.right + 0.001f)
                assertTrue(placement.centerY + extent <= viewport.bottom + 0.001f)
                // Rebuilt from the viewport instead of read back off the placement, so this pins the dial
                // flush against the requested edge. resolve's own centre formula would still satisfy a
                // position-blind implementation, so it is deliberately not reused here.
                val pinned = pinnedPlacement(viewport, settings)
                assertEquals(pinned.centerX, placement.centerX, PLACEMENT_TOLERANCE)
                assertEquals(pinned.centerY, placement.centerY, PLACEMENT_TOLERANCE)
                assertEquals(pinned.radius, placement.radius, PLACEMENT_TOLERANCE)
                for (palette in listOf(DialStyle.DARK_PALETTE, DialStyle.LIGHT_PALETTE)) {
                    val bitmap = render(settings, palette, viewport)
                    val skyRadius = placement.radius / DialRenderer.OUTER_RADIUS
                    assertEquals(palette.rim, bitmap.getPixel(placement.centerX.toInt(), placement.centerY.toInt()))
                    assertEquals(
                        palette.rim,
                        bitmap
                            .getPixel(placement.centerX.toInt(), (placement.centerY + skyRadius * 1.15f).toInt()),
                    )
                    assertEquals(palette.background, bitmap.getPixel(10, 10))
                    bitmap.recycle()
                }
            }
        }
    }

    @Test
    fun offsetsStayWithinTheSurface() {
        // A launcher that hands over a surface no larger than the display cannot scroll it, so its
        // reported offset must not shrink the window: the dial would leave the centre and lose size.
        val unscrollable = WallpaperViewport()
        unscrollable.offsets(xPixels = -664, yPixels = 0)
        assertEquals(
            DialViewport.full(width = 1080, height = 2408),
            unscrollable.resolve(surfaceWidth = 1080, surfaceHeight = 2408, displayWidth = 1080, displayHeight = 2408),
        )
        assertEquals(0f, unscrollable.windowOriginX, PLACEMENT_TOLERANCE)
        val wide = WallpaperViewport()
        wide.offsets(xPixels = -500, yPixels = 0)
        assertEquals(
            DialViewport(left = 500f, top = 0f, right = 1580f, bottom = 1000f),
            wide.resolve(surfaceWidth = 2000, surfaceHeight = 1000, displayWidth = 1080, displayHeight = 1000),
        )
        assertEquals(500f, wide.windowOriginX, PLACEMENT_TOLERANCE)
        val pastTheEnd = WallpaperViewport()
        pastTheEnd.offsets(xPixels = -900, yPixels = 0)
        assertEquals(
            DialViewport(left = 420f, top = 0f, right = 1500f, bottom = 1000f),
            pastTheEnd.resolve(surfaceWidth = 1500, surfaceHeight = 1000, displayWidth = 1080, displayHeight = 1000),
        )
        // The origin is the scroll the framework can actually perform, so it stops at the slack.
        assertEquals(420f, pastTheEnd.windowOriginX, PLACEMENT_TOLERANCE)
        val backwards = WallpaperViewport()
        backwards.offsets(xPixels = 250, yPixels = 0)
        assertEquals(
            DialViewport.full(width = 1080, height = 1000),
            backwards.resolve(surfaceWidth = 2000, surfaceHeight = 1000, displayWidth = 1080, displayHeight = 1000),
        )
        assertEquals(0f, backwards.windowOriginX, PLACEMENT_TOLERANCE)
    }

    @Test
    fun wholeWallpaperDimming() {
        val viewport = DialViewport.full(width = WIDTH, height = HEIGHT)
        for (palette in listOf(DialStyle.DARK_PALETTE, DialStyle.LIGHT_PALETTE)) {
            val original = render(DialDisplaySettings(), palette, viewport)
            for (brightness in DialDisplaySettings.BRIGHTNESS_RANGE) {
                val dimmed = render(DialDisplaySettings(brightness = brightness), palette, viewport)
                val alpha = ((100 - brightness) * 255f / 100).toInt()
                for ((x, y) in listOf(10 to 10, 320 to 380, 320 to 110, 320 to 650)) {
                    val before = original.getPixel(x, y)
                    val after = dimmed.getPixel(x, y)
                    for (channel in listOf(
                        Color.red(before) to Color.red(after),
                        Color.green(before) to Color.green(after),
                        Color.blue(before) to Color.blue(after),
                    )) {
                        assertEquals(channel.first * (255 - alpha) / 255.0, channel.second.toDouble(), 1.0)
                    }
                }
                if (brightness == 100) assertTrue(original.sameAs(dimmed))
                dimmed.recycle()
            }
            original.recycle()
        }
    }

    @Test
    fun cropAndOrientation() {
        val crop = WallpaperViewport()
        crop.offsets(xPixels = -160, yPixels = -80)
        val viewport = crop.resolve(surfaceWidth = 1000, surfaceHeight = 1000, displayWidth = 640, displayHeight = 760)
        assertEquals(DialViewport(left = 160f, top = 80f, right = 800f, bottom = 840f), viewport)
        val placement = requireNotNull(viewport.resolve(DialDisplaySettings(size = 115)))
        val cropped =
            render(
                settings = DialDisplaySettings(size = 115),
                palette = DialStyle.LIGHT_PALETTE,
                viewport = viewport,
                dimensions = 1000 to 1000,
            )
        assertEquals(
            DialStyle.LIGHT_PALETTE.rim,
            cropped.getPixel(placement.centerX.toInt(), placement.centerY.toInt()),
        )
        cropped.recycle()
        crop.insets(insets(Rect(23, 41, 11, 17)))
        // Insets shrink the usable rectangle without moving the scroll, so the origin is unchanged.
        assertEquals(160f, crop.windowOriginX, PLACEMENT_TOLERANCE)
        assertEquals(80f, crop.windowOriginY, PLACEMENT_TOLERANCE)
        assertEquals(
            DialViewport(left = 183f, top = 121f, right = 789f, bottom = 823f),
            crop.resolve(surfaceWidth = 1000, surfaceHeight = 1000, displayWidth = 640, displayHeight = 760),
        )
        crop.insets(insets(Rect()))
        crop.offsets(xPixels = 0, yPixels = 0)
        assertEquals(
            DialViewport.full(width = 760, height = 640),
            crop
                .resolve(surfaceWidth = 760, surfaceHeight = 640, displayWidth = 760, displayHeight = 640),
        )
        val landscape = DialViewport.full(width = 760, height = 640)
        val horizontalFrame =
            render(
                settings = DialDisplaySettings(size = 50, horizontal = 100, vertical = 0),
                palette = DialStyle.DARK_PALETTE,
                viewport = landscape,
                dimensions = 760 to 640,
            )
        val horizontalPlacement =
            requireNotNull(
                landscape
                    .resolve(DialDisplaySettings(size = 50, horizontal = 100, vertical = 0)),
            )
        val pinned = pinnedPlacement(landscape, DialDisplaySettings(size = 50, horizontal = 100, vertical = 0))
        assertEquals(pinned.centerX, horizontalPlacement.centerX, PLACEMENT_TOLERANCE)
        assertEquals(pinned.centerY, horizontalPlacement.centerY, PLACEMENT_TOLERANCE)
        assertEquals(
            DialStyle.DARK_PALETTE.rim,
            horizontalFrame
                .getPixel(horizontalPlacement.centerX.toInt(), horizontalPlacement.centerY.toInt()),
        )
        horizontalFrame.recycle()
        assertNull(DialViewport.full(width = 0, height = 100).resolve(DialDisplaySettings()))
        assertNull(DialViewport.full(width = 30, height = 30).resolve(DialDisplaySettings(size = 50)))
    }

    private fun strokedExtent(viewport: DialViewport, size: Int): Float =
        minOf(a = viewport.right - viewport.left, b = viewport.bottom - viewport.top) *
            DialViewport.RADIUS_FRACTION *
            (size / DialViewport.PERCENT) *
            DialViewport.STROKED_EXTENT

    private fun pinnedPlacement(viewport: DialViewport, settings: DialDisplaySettings): DialPlacement {
        val extent = strokedExtent(viewport, settings.size)
        return DialPlacement(
            centerX = if (settings.horizontal == 0) viewport.left + extent else viewport.right - extent,
            centerY = if (settings.vertical == 0) viewport.top + extent else viewport.bottom - extent,
            radius = extent / DialViewport.STROKED_EXTENT,
        )
    }

    private fun insets(rect: Rect): WindowInsets {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val builder = WindowInsets.Builder()
            builder.setInsetsIgnoringVisibility(
                WindowInsets.Type.systemBars(),
                Insets.of(rect.left, rect.top, rect.right, rect.bottom),
            )
            return builder.build()
        }
        return ReflectionHelpers.callConstructor(WindowInsets::class.java, ClassParameter.from(Rect::class.java, rect))
    }

    private fun render(
        settings: DialDisplaySettings,
        palette: DialPalette,
        viewport: DialViewport,
        dimensions: Pair<Int, Int> = WIDTH to HEIGHT,
    ): Bitmap {
        val bitmap = Bitmap.createBitmap(dimensions.first, dimensions.second, Bitmap.Config.ARGB_8888)
        DialRenderer().renderDisplay(
            canvas = Canvas(bitmap),
            state = clockState(LocalTime.NOON),
            geometry = null,
            layers = DialLayers(),
            style = DialRenderStyle(palette = palette, display = settings, viewport = viewport),
        )
        return bitmap
    }

    private companion object {
        const val WIDTH = 640
        const val HEIGHT = 760
        const val PLACEMENT_TOLERANCE = 0.01f
    }
}
