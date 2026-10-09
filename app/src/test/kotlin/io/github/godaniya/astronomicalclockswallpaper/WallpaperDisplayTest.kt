package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.os.Looper
import android.service.wallpaper.WallpaperService
import android.view.SurfaceView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.util.ReflectionHelpers
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class WallpaperDisplayTest {
    @Test
    fun independentViewportsAndTicks() {
        val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java).create()
        val service = controller.get()
        val firstHolder = ReadyFrameHolder(delegate = SurfaceView(service).holder, width = 200, height = 300)
        val secondHolder = ReadyFrameHolder(delegate = SurfaceView(service).holder, width = 300, height = 200)
        val firstFrames = mutableListOf<DialRenderStyle>()
        val secondFrames = mutableListOf<DialRenderStyle>()

        fun create(holder: ReadyFrameHolder, frames: MutableList<DialRenderStyle>): WallpaperService.Engine =
            service.createEngine(
                draw = { _, _, _, _, palette, display, viewport ->
                    frames.add(DialRenderStyle(palette = palette, display = display, viewport = viewport))
                },
                holder = holder,
                clock = Clock.fixed(Instant.parse("2026-03-21T12:00:00Z"), ZoneOffset.UTC),
            )
        val preferences = service.getSharedPreferences("dial_display", Context.MODE_PRIVATE)
        val listeners = ReflectionHelpers.getField<Map<*, *>>(preferences, "mListeners")
        val first = create(firstHolder, firstFrames)
        val second = create(secondHolder, secondFrames)
        try {
            assertEquals(2, listeners.size)
            first.onVisibilityChanged(true)
            second.onVisibilityChanged(true)
            assertNotEquals(firstFrames.last().viewport, secondFrames.last().viewport)
            val initialFirst = firstFrames.size
            val initialSecond = secondFrames.size
            second.onVisibilityChanged(false)
            val changed = DialDisplaySettings(size = 50, horizontal = 0, vertical = 100, brightness = 80)
            DialDisplayStore(service).save(changed)
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(1))
            assertEquals(initialFirst + 1, firstFrames.size)
            assertEquals(changed, firstFrames.last().display)
            assertEquals(initialSecond, secondFrames.size)
            second.onVisibilityChanged(true)
            assertEquals(changed, secondFrames.last().display)
            first.onSurfaceDestroyed(firstHolder)
            first.onVisibilityChanged(true)
            val stopped = firstFrames.size
            DialDisplayStore(service).save(DialDisplaySettings())
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(1))
            assertEquals(stopped, firstFrames.size)
            first.onSurfaceChanged(firstHolder, 1, 200, 300)
            assertEquals(DialDisplaySettings(), firstFrames.last().display)
            first.onDestroy()
            assertEquals(1, listeners.size)
            val destroyedCount = firstFrames.size
            DialDisplayStore(service).save(changed)
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(1))
            assertEquals(destroyedCount, firstFrames.size)
        } finally {
            first.onDestroy()
            second.onDestroy()
            assertEquals(0, listeners.size)
            firstHolder.release()
            secondHolder.release()
            controller.destroy()
        }
    }
}
