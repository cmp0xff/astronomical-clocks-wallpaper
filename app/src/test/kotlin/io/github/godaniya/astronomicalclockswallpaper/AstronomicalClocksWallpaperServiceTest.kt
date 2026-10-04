package io.github.godaniya.astronomicalclockswallpaper

import android.annotation.SuppressLint
import android.app.WallpaperInfo
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Looper
import android.service.wallpaper.WallpaperService
import android.view.SurfaceView
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration
import java.time.Instant

/** Verifies Android discovery, binding protection, metadata, service teardown, and tick scheduling. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class AstronomicalClocksWallpaperServiceTest {
    private val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java)

    @Before
    fun createService() {
        controller.create()
    }

    @After
    fun destroyService() {
        controller.destroy()
    }

    // This test queries only its own package, which is always visible.
    @SuppressLint("QueryPermissionsNeeded")
    @Test
    fun wallpaperDeclaration() {
        val service = controller.get()
        val intent = Intent(WallpaperService.SERVICE_INTERFACE).setPackage(service.packageName)
        val matches = service.packageManager.queryIntentServices(intent, PackageManager.GET_META_DATA)
        assertEquals(1, matches.size)
        val info = matches.single()
        assertTrue(info.serviceInfo.exported)
        assertEquals("android.permission.BIND_WALLPAPER", info.serviceInfo.permission)
        val wallpaper = WallpaperInfo(service, info)
        assertEquals(ComponentName(service, AstronomicalClocksWallpaperService::class.java), wallpaper.component)
        assertEquals(SettingsActivity::class.java.name, wallpaper.settingsActivity)
    }

    @Test
    fun independentEngineLifecycle() {
        val service = controller.get()
        val first = service.onCreateEngine()
        val second = service.onCreateEngine()
        assertNotSame(first, second)
        first.onVisibilityChanged(false)
        first.onDestroy()
        second.onVisibilityChanged(false)
        second.onDestroy()
    }

    @Test
    fun destroyCancelsScheduledRedraw() {
        val engine = controller.get().onCreateEngine()
        val looper = shadowOf(Looper.getMainLooper())

        engine.onVisibilityChanged(true)
        assertTrue(looper.nextScheduledTaskTime > Duration.ZERO)

        engine.onDestroy()
        assertEquals(Duration.ZERO, looper.nextScheduledTaskTime)
    }

    // A destroyed-then-recreated surface must resume ticking: onSurfaceDestroyed cancels the loop,
    // and only onSurfaceChanged can restart it when visibility never changes.
    @Test
    fun surfaceRecreationResumesTicks() {
        val engine = controller.get().onCreateEngine()
        val looper = shadowOf(Looper.getMainLooper())
        val holder = engine.surfaceHolder

        engine.onVisibilityChanged(true)
        assertTrue(looper.nextScheduledTaskTime > Duration.ZERO)

        engine.onSurfaceDestroyed(holder)
        assertEquals(Duration.ZERO, looper.nextScheduledTaskTime)

        engine.onSurfaceChanged(holder, SURFACE_FORMAT, SURFACE_WIDTH, SURFACE_HEIGHT)
        assertTrue(looper.nextScheduledTaskTime > Duration.ZERO)
    }

    @Test
    fun hiddenSurfaceStaysCancelled() {
        val engine = controller.get().onCreateEngine()
        val looper = shadowOf(Looper.getMainLooper())
        val holder = engine.surfaceHolder

        engine.onVisibilityChanged(false)
        engine.onSurfaceChanged(holder, SURFACE_FORMAT, SURFACE_WIDTH, SURFACE_HEIGHT)
        assertEquals(Duration.ZERO, looper.nextScheduledTaskTime)
    }

    // onSurfaceDestroyed cancels the tick loop but leaves the engine visible, so a debug broadcast
    // arriving in the gap must not restart it: runTick's finally would re-arm the periodic loop onto
    // a released surface. The same broadcast still redraws once a surface exists again.
    @Test
    fun debugTickSkippedWithoutSurface() {
        val service = controller.get()
        val holder = ReadyFrameHolder(SurfaceView(service).holder)
        var draws = 0
        val engine =
            service.createEngine(
                draw = { _, _, _, _ -> draws++ },
                holder = holder,
            )
        val offsetIntent =
            Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                putExtra(AstronomicalClocksWallpaperService.EXTRA_OFFSET_MINUTES, 30L)
            }

        engine.onSurfaceChanged(holder, SURFACE_FORMAT, SURFACE_WIDTH, SURFACE_HEIGHT)
        engine.onVisibilityChanged(true)
        assertEquals(1, draws)

        engine.onSurfaceDestroyed(holder)
        service.handleDebugSetTime(offsetIntent)
        assertEquals(1, draws)

        // Surface recreation resumes the loop, and the debug broadcast then ticks again on top of it.
        engine.onSurfaceChanged(holder, SURFACE_FORMAT, SURFACE_WIDTH, SURFACE_HEIGHT)
        service.handleDebugSetTime(offsetIntent)
        assertEquals(3, draws)

        engine.onDestroy()
        holder.release()
    }

    // onDestroy sets isDestroyed so a late visibility callback cannot restart the tick loop on a
    // handler the service no longer owns.
    @Test
    fun postDestroyVisibilityIgnored() {
        val engine = controller.get().onCreateEngine()
        val looper = shadowOf(Looper.getMainLooper())

        engine.onDestroy()
        engine.onVisibilityChanged(true)
        assertEquals(Duration.ZERO, looper.nextScheduledTaskTime)
    }

    @Test
    fun debugBroadcastSetsOffset() {
        val service = controller.get()
        val intent =
            Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                putExtra(AstronomicalClocksWallpaperService.EXTRA_OFFSET_MINUTES, 30L)
            }
        service.handleDebugSetTime(intent)

        assertEquals(Duration.ofMinutes(30), service.debugClock.currentOffset)
    }

    @Test
    fun debugBroadcastSetsInstant() {
        val service = controller.get()
        val fixedInstant = "2026-06-21T00:00:00Z"
        val intent =
            Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                putExtra(AstronomicalClocksWallpaperService.EXTRA_INSTANT, fixedInstant)
            }
        service.handleDebugSetTime(intent)

        assertEquals(Instant.parse(fixedInstant), service.debugClock.currentFixedInstant)
    }

    // The instant extra is external input. A malformed string must leave the clock untouched, and an
    // instant that parses but exceeds the epoch-millis range must be rejected here rather than
    // throwing ArithmeticException out of the tick loop's scheduleNextTick.
    @Test
    fun debugBroadcastRejectsInstant() {
        val service = controller.get()
        val rejected = listOf("not-an-instant", "+1000000000-12-31T23:59:59Z")

        for (bad in rejected) {
            service.handleDebugSetTime(
                Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                    putExtra(AstronomicalClocksWallpaperService.EXTRA_INSTANT, bad)
                },
            )
        }

        assertEquals(Duration.ZERO, service.debugClock.currentOffset)
        assertNull(service.debugClock.currentFixedInstant)
    }

    // The offset extras are external input. An hours value that overflows the checked
    // multiplication, and a millis value that leaves the epoch-millis range once added to system
    // time, must both be rejected rather than wrapping to an unrelated virtual time. A valid
    // offset is seeded first so the assertion can tell rejection from "no offset was ever set".
    @Test
    fun debugBroadcastRejectsOverflow() {
        val service = controller.get()
        val validOffset =
            Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                putExtra(AstronomicalClocksWallpaperService.EXTRA_OFFSET_HOURS, 5L)
            }
        service.handleDebugSetTime(validOffset)
        assertEquals(Duration.ofHours(5), service.debugClock.currentOffset)

        val overflowing =
            listOf(
                Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                    putExtra(AstronomicalClocksWallpaperService.EXTRA_OFFSET_HOURS, Long.MAX_VALUE)
                },
                Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                    putExtra(AstronomicalClocksWallpaperService.EXTRA_OFFSET_MILLIS, Long.MAX_VALUE)
                },
            )
        for (intent in overflowing) {
            service.handleDebugSetTime(intent)
        }

        assertEquals(Duration.ofHours(5), service.debugClock.currentOffset)
    }

    @Test
    fun debugBroadcastResetsTime() {
        val service = controller.get()
        val offsetIntent =
            Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                putExtra(AstronomicalClocksWallpaperService.EXTRA_OFFSET_HOURS, 2L)
            }
        service.handleDebugSetTime(offsetIntent)
        assertEquals(Duration.ofHours(2), service.debugClock.currentOffset)

        val resetIntent =
            Intent(AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME).apply {
                putExtra(AstronomicalClocksWallpaperService.EXTRA_RESET, true)
            }
        service.handleDebugSetTime(resetIntent)
        assertEquals(Duration.ZERO, service.debugClock.currentOffset)
        assertNull(service.debugClock.currentFixedInstant)
    }

    private companion object {
        const val SURFACE_FORMAT = 1
        const val SURFACE_WIDTH = 200
        const val SURFACE_HEIGHT = 200
    }
}
