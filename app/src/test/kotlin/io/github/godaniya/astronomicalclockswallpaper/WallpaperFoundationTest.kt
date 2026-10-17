package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.os.Looper
import android.os.SystemClock
import android.service.wallpaper.WallpaperService
import android.util.Log
import android.view.SurfaceView
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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

/** Checks frame consistency and live layer updates through the engine's production calculation seam. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class WallpaperFoundationTest {
    private val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java)
    private val engines = mutableListOf<WallpaperService.Engine>()
    private val frames = mutableListOf<TestFrame>()
    private val calculator = RecordingCalculator()
    private val instant = Instant.parse("2026-01-15T12:34:56Z")
    private lateinit var holder: ReadyFrameHolder

    @Before
    fun createService() {
        controller.create()
        holder = ReadyFrameHolder(SurfaceView(controller.get()).holder)
    }

    @After
    fun destroyService() {
        engines.forEach { it.onDestroy() }
        holder.release()
        controller.destroy()
    }

    @Test
    fun civilAndSkyShareOneInstant() {
        val location = SITE
        LocationStore(controller.get()).save(location)
        var reads = 0L
        val clock =
            object : Clock() {
                override fun getZone(): ZoneId = ZoneOffset.UTC

                override fun withZone(zone: ZoneId): Clock = Clock.fixed(instant, zone)

                override fun millis(): Long = instant.toEpochMilli()

                override fun instant(): Instant = instant.plusSeconds(reads++)
            }
        engine(clock).onVisibilityChanged(true)
        assertEquals(1L, reads)
        assertEquals(listOf(instant to location), calculator.calls)
        assertEquals(clockState(LocalTime.of(13, 34, 56)), frames.single().clock)
        assertEquals(calculator.geometry, frames.single().geometry)
    }

    @Test
    fun noSiteSkipsGeometry() {
        engine().onVisibilityChanged(true)
        assertNull(frames.single().geometry)
        assertTrue(calculator.calls.isEmpty())
        assertEquals(clockState(LocalTime.of(12, 34, 56)), frames.single().clock)
    }

    @Test
    fun layersUpdateOnNextTick() {
        LocationStore(controller.get()).save(SITE)
        val store = DialSettingsStore(controller.get())
        val first = engine()
        first.onVisibilityChanged(true)
        engine().onVisibilityChanged(true)
        assertEquals(listOf(DialLayers(), DialLayers()), frames.map { it.layers })
        val layers = DialLayers(isZodiacRingEnabled = false, isSunEnabled = false)
        store.save(layers)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(2, frames.size)
        tick()
        assertEquals(listOf(layers, layers), frames.takeLast(2).map { it.layers })
        assertEquals(calculator.geometry, frames.last().geometry)
        first.onDestroy()
        engines.remove(first)
        val changed = DialLayers(isZodiacRingEnabled = true, isSunEnabled = false)
        store.save(changed)
        tick()
        assertEquals(5, frames.size)
        assertEquals(changed, frames.last().layers)
    }

    @Test
    fun hiddenLayersWaitForVisibility() {
        val engine = engine()
        engine.onVisibilityChanged(true)
        engine.onVisibilityChanged(false)
        val layers = DialLayers(isZodiacRingEnabled = false, isSunEnabled = true)
        DialSettingsStore(controller.get()).save(layers)
        tick()
        assertEquals(1, frames.size)
        assertEquals(Duration.ZERO, shadowOf(Looper.getMainLooper()).nextScheduledTaskTime)
        engine.onVisibilityChanged(true)
        assertEquals(layers, frames.last().layers)
    }

    @Test
    fun destroyDetachesLayerListener() {
        val engine = engine()
        engine.onVisibilityChanged(true)
        engine.onDestroy()
        engines.remove(engine)
        ShadowLog.clear()
        controller
            .get()
            .getSharedPreferences("dial_settings", Context.MODE_PRIVATE)
            .edit()
            .putString("zodiac_ring", "bad")
            .apply()
        tick()
        assertEquals(1, frames.size)
        assertTrue(ShadowLog.getLogsForTag("DialSettingsStore").isEmpty())
    }

    @Test
    fun geometryFailureDegrades() {
        LocationStore(controller.get()).save(SITE)
        calculator.failure = ArithmeticException("out of range")
        ShadowLog.clear()
        engine().onVisibilityChanged(true)
        val frame = frames.single()
        assertNull(frame.geometry)
        assertEquals(clockState(LocalTime.of(13, 34, 56)), frame.clock)
        val logs = ShadowLog.getLogsForTag("AstronomicalClocksWallpaperService")
        assertTrue(logs.any { it.type == Log.WARN && it.msg.contains("geometry calculation failed") })
    }

    @Test
    fun drawingErrorPreservesTicks() {
        var isFailureTriggered = false
        val engine =
            controller.get().createEngine(
                draw = { _, _, _, _ ->
                    if (isFailureTriggered) {
                        isFailureTriggered = false
                        throw ArithmeticException("calculation overflow")
                    }
                },
                holder = holder,
                clock = Clock.fixed(instant, ZoneOffset.UTC),
                deviceZone = { ZoneOffset.UTC },
                calculator = calculator,
            )
        engines.add(engine)
        engine.onVisibilityChanged(true)
        isFailureTriggered = true
        ShadowLog.clear()
        tick()
        val looper = shadowOf(Looper.getMainLooper())
        assertTrue(
            "Next tick must still be scheduled after drawing error",
            looper.nextScheduledTaskTime > Duration.ZERO,
        )
        val logs = ShadowLog.getLogsForTag("AstronomicalClocksWallpaperService")
        assertTrue(logs.any { it.type == Log.ERROR && it.msg.contains("unexpected error in drawFrame") })
    }

    @Test
    fun initialDrawErrorPreservesTicks() {
        val engine =
            controller.get().createEngine(
                draw = { _, _, _, _ -> throw ArithmeticException("initial draw overflow") },
                holder = holder,
                clock = Clock.fixed(instant, ZoneOffset.UTC),
                deviceZone = { ZoneOffset.UTC },
                calculator = calculator,
            )
        engines.add(engine)
        ShadowLog.clear()
        engine.onVisibilityChanged(true)
        val looper = shadowOf(Looper.getMainLooper())
        assertTrue(
            "Next tick must be scheduled after initial draw error",
            looper.nextScheduledTaskTime > Duration.ZERO,
        )
        val logs = ShadowLog.getLogsForTag("AstronomicalClocksWallpaperService")
        assertTrue(logs.any { it.type == Log.ERROR && it.msg.contains("unexpected error in drawFrame") })
    }

    private fun engine(clock: Clock = Clock.fixed(instant, ZoneOffset.UTC)): WallpaperService.Engine {
        val engine =
            controller.get().createEngine(
                draw = { _, state, geometry, layers -> frames.add(TestFrame(state, geometry, layers)) },
                holder = holder,
                clock = clock,
                deviceZone = { ZoneOffset.UTC },
                calculator = calculator,
            )
        engines.add(engine)
        return engine
    }

    private fun tick() {
        val looper = shadowOf(Looper.getMainLooper())
        val delay = (looper.nextScheduledTaskTime.toMillis() - SystemClock.uptimeMillis()).coerceAtLeast(1L)
        looper.idleFor(Duration.ofMillis(delay))
    }

    private data class TestFrame(val clock: ClockState, val geometry: DialGeometry?, val layers: DialLayers)

    private class RecordingCalculator : AstronomyCalculator {
        val calls = mutableListOf<Pair<Instant, ObservingLocation>>()
        val geometry = DialGeometry(localSiderealAngleDeg = 90.0, trueObliquityDeg = 23.4, latitudeDeg = 50.0)
        var failure: RuntimeException? = null

        override fun sky(time: Instant, location: ObservingLocation): Sky = error("unexpected full sky call")

        override fun dialGeometry(time: Instant, location: ObservingLocation): DialGeometry {
            calls.add(time to location)
            failure?.let { throw it }
            return geometry
        }
    }

    private companion object {
        val SITE =
            ObservingLocation(
                latitude = 50.0,
                longitude = 14.0,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.of("Europe/Prague"),
            )
    }
}
