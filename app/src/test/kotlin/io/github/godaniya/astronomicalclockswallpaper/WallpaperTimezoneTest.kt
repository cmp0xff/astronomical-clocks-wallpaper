package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.os.Looper
import android.os.SystemClock
import android.service.wallpaper.WallpaperService
import android.view.SurfaceView
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZoneOffset

/** Exercises saved civil time and preference propagation through real engine ticks. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class WallpaperTimezoneTest {
    private val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java)
    private val engines = mutableListOf<WallpaperService.Engine>()
    private lateinit var holder: ReadyFrameHolder
    private lateinit var store: LocationStore
    private var deviceZone = ZoneId.of("Asia/Tokyo")
    private var instant = Instant.parse("2026-01-15T12:34:56Z")

    @Before
    fun createService() {
        controller.create()
        holder = ReadyFrameHolder(SurfaceView(controller.get()).holder)
        store = LocationStore(controller.get())
    }

    @After
    fun destroyService() {
        engines.forEach { it.onDestroy() }
        holder.release()
        controller.destroy()
    }

    @Test
    fun savedZoneIgnoresPhoneChanges() {
        store.save(location("Europe/Prague"))
        val frames = mutableListOf<ClockState>()
        val engine = engine(frames)
        engine.onVisibilityChanged(true)
        assertEquals(clockState(LocalTime.of(13, 34, 56)), frames.last())

        deviceZone = ZoneId.of("America/Los_Angeles")
        tick()
        assertEquals(clockState(LocalTime.of(13, 34, 56)), frames.last())
        assertEquals(location("Europe/Prague"), store.load())
    }

    @Test
    fun noLocationFollowsPhone() {
        val frames = mutableListOf<ClockState>()
        engine(frames).onVisibilityChanged(true)
        assertEquals(clockState(LocalTime.of(21, 34, 56)), frames.last())

        deviceZone = ZoneId.of("America/Los_Angeles")
        tick()
        assertEquals(clockState(LocalTime.of(4, 34, 56)), frames.last())
    }

    @Test
    fun siteFollowsDstTransitions() {
        store.save(location("Europe/Prague"))
        val frames = mutableListOf<ClockState>()
        instant = Instant.parse("2026-03-29T00:59:59Z")
        engine(frames).onVisibilityChanged(true)
        assertEquals(clockState(LocalTime.of(1, 59, 59)), frames.last())
        instant = Instant.parse("2026-03-29T01:00:00Z")
        tick()
        assertEquals(clockState(LocalTime.of(3, 0, 0)), frames.last())

        instant = Instant.parse("2026-10-25T00:59:59Z")
        tick()
        assertEquals(clockState(LocalTime.of(2, 59, 59)), frames.last())
        instant = Instant.parse("2026-10-25T01:00:00Z")
        tick()
        assertEquals(clockState(LocalTime.of(2, 0, 0)), frames.last())
    }

    @Test
    fun enginesRefreshOnNextTick() {
        store.save(location("Europe/Prague"))
        val firstFrames = mutableListOf<ClockState>()
        val secondFrames = mutableListOf<ClockState>()
        val first = engine(firstFrames)
        first.onVisibilityChanged(true)
        engine(secondFrames).onVisibilityChanged(true)
        store.save(location("Asia/Tokyo"))
        shadowOf(Looper.getMainLooper()).idle()
        // Preference callbacks replace the snapshots but must not render an extra frame.
        assertEquals(1, firstFrames.size)
        assertEquals(1, secondFrames.size)
        tick()
        val expected = clockState(LocalTime.of(21, 34, 56))
        assertEquals(expected, firstFrames.last())
        assertEquals(expected, secondFrames.last())

        first.onDestroy()
        engines.remove(first)
        store.save(location("Europe/Prague"))
        tick()
        assertEquals(2, firstFrames.size)
        assertEquals(clockState(LocalTime.of(13, 34, 56)), secondFrames.last())
    }

    @Test
    fun hiddenUpdateDoesNotStartTicks() {
        store.save(location("Europe/Prague"))
        val frames = mutableListOf<ClockState>()
        val engine = engine(frames)
        engine.onVisibilityChanged(true)
        engine.onVisibilityChanged(false)
        store.save(location("Asia/Tokyo"))
        tick()
        assertEquals(1, frames.size)
        assertEquals(Duration.ZERO, shadowOf(Looper.getMainLooper()).nextScheduledTaskTime)

        engine.onVisibilityChanged(true)
        assertEquals(clockState(LocalTime.of(21, 34, 56)), frames.last())
    }

    @Test
    fun destroyUnregistersListener() {
        val frames = mutableListOf<ClockState>()
        val engine = engine(frames)
        engine.onVisibilityChanged(true)
        engine.onDestroy()
        engines.remove(engine)
        ShadowLog.clear()
        controller
            .get()
            .getSharedPreferences("observing_location", Context.MODE_PRIVATE)
            .edit()
            .putString("location", "invalid JSON")
            .apply()
        tick()
        assertEquals(1, frames.size)
        // A leaked listener would load the corrupt record and produce a LocationStore diagnostic.
        assertTrue(ShadowLog.getLogsForTag("LocationStore").isEmpty())
        assertEquals(Duration.ZERO, shadowOf(Looper.getMainLooper()).nextScheduledTaskTime)
    }

    @Test
    fun ticksUseCachedLocation() {
        controller
            .get()
            .getSharedPreferences("observing_location", Context.MODE_PRIVATE)
            .edit()
            .putString("location", "invalid JSON")
            .apply()
        val frames = mutableListOf<ClockState>()
        ShadowLog.clear()
        val engine = engine(frames)
        assertEquals(1, ShadowLog.getLogsForTag("LocationStore").size)
        ShadowLog.clear()

        engine.onVisibilityChanged(true)
        tick()
        assertEquals(2, frames.size)
        assertTrue(ShadowLog.getLogsForTag("LocationStore").isEmpty())
    }

    @Test
    fun recreationLoadsSavedZone() {
        store.save(location("Pacific/Chatham"))
        val firstFrames = mutableListOf<ClockState>()
        val first = engine(firstFrames)
        first.onVisibilityChanged(true)
        first.onDestroy()
        engines.remove(first)
        deviceZone = ZoneId.of("America/Los_Angeles")
        val secondFrames = mutableListOf<ClockState>()
        engine(secondFrames).onVisibilityChanged(true)
        assertEquals(clockState(LocalTime.of(2, 19, 56)), secondFrames.single())
    }

    private fun engine(frames: MutableList<ClockState>): WallpaperService.Engine {
        val timeSource =
            object : Clock() {
                override fun getZone(): ZoneId = ZoneOffset.UTC

                override fun withZone(zone: ZoneId): Clock = Clock.fixed(instant, zone)

                override fun instant(): Instant = instant
            }
        val engine =
            controller.get().createEngine(
                draw = { _, state, _, _ -> frames.add(state) },
                holder = holder,
                clock = timeSource,
                deviceZone = { deviceZone },
            )
        engines.add(engine)
        return engine
    }

    private fun tick() {
        val looper = shadowOf(Looper.getMainLooper())
        val delay = (looper.nextScheduledTaskTime.toMillis() - SystemClock.uptimeMillis()).coerceAtLeast(1L)
        looper.idleFor(Duration.ofMillis(delay))
    }

    private fun location(zone: String): ObservingLocation = SAVED_SITE.copy(zoneId = ZoneId.of(zone))

    private companion object {
        val SAVED_SITE =
            ObservingLocation(
                latitude = 50.0,
                longitude = 14.0,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.of("UTC"),
            )
    }
}
