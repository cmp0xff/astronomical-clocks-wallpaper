package io.github.godaniya.astronomicalclockswallpaper

import android.content.SharedPreferences
import android.graphics.Canvas
import android.os.Handler
import android.os.Looper
import android.service.wallpaper.WallpaperService
import android.util.Log
import android.view.SurfaceHolder
import java.time.Clock
import java.time.Instant
import java.time.ZoneId

/** An animated astronomical clock wallpaper. */
class AstronomicalClocksWallpaperService : WallpaperService() {
    override fun onCreateEngine(): Engine {
        val dialRenderer = DialRenderer()
        return createEngine(
            draw = { canvas, state, geometry, layers ->
                dialRenderer.renderDial(canvas = canvas, state = state, geometry = geometry, layers = layers)
            },
        )
    }

    /** Creates an engine with a frame draw operation and optional controlled surface holder. */
    internal fun createEngine(
        draw: (Canvas, ClockState, DialGeometry?, DialLayers) -> Unit,
        holder: SurfaceHolder? = null,
        clock: Clock = Clock.systemUTC(),
        deviceZone: () -> ZoneId = ZoneId::systemDefault,
        calculator: AstronomyCalculator = AstronomyEngineCalculator(),
    ): Engine {
        val engine =
            ClockEngine(
                draw = draw,
                frameHolder = holder,
                clock = clock,
                deviceZone = deviceZone,
                calculator = calculator,
            )
        return engine
    }

    // Engine is a non-static Java inner class and requires the enclosing service instance.
    @Suppress("UnnecessaryInnerClass")
    private inner class ClockEngine(
        private val draw: (Canvas, ClockState, DialGeometry?, DialLayers) -> Unit,
        private val frameHolder: SurfaceHolder?,
        private val clock: Clock,
        private val deviceZone: () -> ZoneId,
        private val calculator: AstronomyCalculator,
    ) : Engine() {
        private val handler = Handler(Looper.getMainLooper())
        private val locationStore = LocationStore(applicationContext, deviceZone)
        private val dialSettingsStore = DialSettingsStore(applicationContext)
        private var settings = loadSettings()
        private var isDestroyed = false
        private val settingsListener =
            SharedPreferences.OnSharedPreferenceChangeListener { _, _ ->
                if (!isDestroyed) {
                    settings = loadSettings()
                }
            }

        // Mirrors the visibility the framework reports through onVisibilityChanged; kept here so
        // onSurfaceChanged can decide whether to resume ticking without a framework-only getter.
        private var isEngineVisible = false

        // Both of these faults recur once a second while they last, so they log the first occurrence
        // and a periodic summary rather than a stack trace per tick.
        private val renderFailureLog =
            RepeatedFailureLog(
                tag = TAG,
                message = "unexpected error in drawFrame; keeping tick loop alive",
            )
        private val geometryFailureLog =
            RepeatedFailureLog(
                tag = TAG,
                message = "geometry calculation failed; falling back to civil dial",
                level = Log.WARN,
            )

        private val wallpaperFrame = WallpaperFrame()

        init {
            // Keep a strong listener reference for this engine's lifetime. Updates only replace the
            // cached snapshot; hidden engines must not acquire a surface or schedule a tick.
            locationStore.registerListener(settingsListener)
            dialSettingsStore.registerListener(settingsListener)
        }

        private fun loadSettings(): WallpaperSettings =
            WallpaperSettings(location = locationStore.load(), layers = dialSettingsStore.load())

        override fun onVisibilityChanged(visible: Boolean) {
            if (isDestroyed) {
                return
            }
            isEngineVisible = visible
            if (visible) {
                runTick()
            } else {
                stopTicking()
            }
        }

        override fun onSurfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
            super.onSurfaceChanged(holder, format, width, height)
            // Redraw for the new surface and restart the tick. The framework can destroy and
            // recreate the surface without a visibility change, and onSurfaceDestroyed cancels the
            // loop, so this is the only place that can resume it in that case.
            if (isEngineVisible) {
                runTick()
            }
        }

        override fun onSurfaceDestroyed(holder: SurfaceHolder) {
            stopTicking()
            super.onSurfaceDestroyed(holder)
        }

        override fun onDestroy() {
            isDestroyed = true
            isEngineVisible = false
            locationStore.unregisterListener(settingsListener)
            dialSettingsStore.unregisterListener(settingsListener)
            stopTicking()
            super.onDestroy()
        }

        // Draws a frame and posts the next tick. Safe to call repeatedly: scheduleNextTick clears any
        // pending callback first, so the loop is never double-scheduled. Handled argument/state
        // failures return from drawFrame normally; unexpected exceptions are contained to preserve the
        // tick loop, while severe VM errors still propagate.
        @Suppress("TooGenericExceptionCaught")
        private fun runTick() {
            try {
                drawFrame()
                renderFailureLog.recordSuccess()
            } catch (e: Exception) {
                renderFailureLog.recordFailure(e)
            } finally {
                scheduleNextTick()
            }
        }

        // The handler is dedicated to ticks, so cancelling all messages stops the loop.
        private fun stopTicking() {
            handler.removeCallbacksAndMessages(null)
        }

        private fun scheduleNextTick() {
            handler.removeCallbacksAndMessages(null)
            val isScheduled =
                handler.postDelayed(
                    Runnable { runTick() },
                    millisUntilNextWholeSecond(),
                )
            if (!isScheduled) {
                Log.w(
                    TAG,
                    "scheduleNextTick: postDelayed returned false; looper exiting or message queue shutting down",
                )
            }
        }

        private fun millisUntilNextWholeSecond(): Long {
            val millisInSecond = Math.floorMod(clock.millis(), MILLIS_PER_SECOND)
            return MILLIS_PER_SECOND - millisInSecond
        }

        // Geometry failures degrade to the civil dial instead of blanking the frame. A fault that
        // lasts is logged on its first tick and summarised, not repeated on every tick.
        @Suppress("TooGenericExceptionCaught")
        private fun dialGeometryOrNull(instant: Instant, location: ObservingLocation?): DialGeometry? {
            if (location == null) {
                geometryFailureLog.recordSuccess()
                return null
            }
            return try {
                calculator.dialGeometry(instant, location).also { geometryFailureLog.recordSuccess() }
            } catch (e: RuntimeException) {
                geometryFailureLog.recordFailure(e)
                null
            }
        }

        private fun drawFrame() {
            wallpaperFrame.drawWallpaperFrame(frameHolder ?: surfaceHolder) { canvas ->
                val instant = clock.instant()
                val snapshot = settings
                val location = snapshot.location
                val civilTime = instant.atZone(location?.zoneId ?: deviceZone()).toLocalTime()
                val geometry = dialGeometryOrNull(instant, location)
                draw(canvas, clockState(civilTime), geometry, snapshot.layers)
            }
        }
    }

    private companion object {
        const val MILLIS_PER_SECOND = 1000L
        const val TAG = "AstronomicalClocksWallpaperService"
    }
}
