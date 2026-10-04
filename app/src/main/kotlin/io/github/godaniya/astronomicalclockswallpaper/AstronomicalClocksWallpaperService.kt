package io.github.godaniya.astronomicalclockswallpaper

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.content.pm.ApplicationInfo
import android.graphics.Canvas
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.service.wallpaper.WallpaperService
import android.util.Log
import android.view.SurfaceHolder
import java.time.Clock
import java.time.DateTimeException
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeParseException
import java.util.Collections
import java.util.concurrent.ConcurrentHashMap

/** An animated astronomical clock wallpaper. */
class AstronomicalClocksWallpaperService : WallpaperService() {
    private val mutableDebugClock = MutableDebugClock()
    private val activeEngines = Collections.newSetFromMap(ConcurrentHashMap<ClockEngine, Boolean>())
    private var debugReceiver: BroadcastReceiver? = null

    internal val debugClock: MutableDebugClock
        get() = mutableDebugClock

    override fun onCreate() {
        super.onCreate()
        registerDebugReceiver()
    }

    override fun onDestroy() {
        unregisterDebugReceiver()
        super.onDestroy()
    }

    override fun onCreateEngine(): Engine {
        val dialRenderer = DialRenderer()
        val isDebuggable = applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
        val clock = if (isDebuggable) mutableDebugClock else Clock.systemUTC()
        return createEngine(
            draw = { canvas, state, geometry, layers ->
                dialRenderer.renderDial(canvas = canvas, state = state, geometry = geometry, layers = layers)
            },
            clock = clock,
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
        activeEngines.add(engine)
        return engine
    }

    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    private fun registerDebugReceiver() {
        val isDebuggable = applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
        if (!isDebuggable) return

        val receiver =
            object : BroadcastReceiver() {
                override fun onReceive(context: Context, intent: Intent) {
                    if (intent.action == ACTION_DEBUG_SET_TIME) {
                        handleDebugSetTime(intent)
                    }
                }
            }
        debugReceiver = receiver
        val filter = IntentFilter(ACTION_DEBUG_SET_TIME)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(receiver, filter, RECEIVER_EXPORTED)
        } else {
            registerReceiver(receiver, filter)
        }
    }

    private fun unregisterDebugReceiver() {
        debugReceiver?.let { receiver ->
            try {
                unregisterReceiver(receiver)
            } catch (e: IllegalArgumentException) {
                Log.w(TAG, "Debug receiver was not registered or already unregistered", e)
            }
            debugReceiver = null
        }
    }

    internal fun handleDebugSetTime(intent: Intent) {
        val isReset = intent.getBooleanExtra(EXTRA_RESET, false)
        if (isReset) {
            mutableDebugClock.reset()
            Log.i(TAG, "Debug clock reset to system UTC")
            triggerDebugTicks()
        } else {
            val instantStr = intent.getStringExtra(EXTRA_INSTANT)
            if (!instantStr.isNullOrBlank()) {
                try {
                    val instant = Instant.parse(instantStr)
                    // Instants past the long-millis range parse but overflow the epoch-millis
                    // conversion the tick loop performs, which would throw from scheduleNextTick
                    // outside runTick's catch. Reject them here, where the input arrives.
                    if (instant.isBefore(minSupportedInstant) || instant.isAfter(maxSupportedInstant)) {
                        Log.e(TAG, "Instant extra out of supported range: $instantStr")
                        return
                    }
                    mutableDebugClock.setInstant(instant)
                    Log.i(TAG, "Debug clock fixed to instant: $instant")
                    triggerDebugTicks()
                } catch (e: DateTimeParseException) {
                    Log.e(TAG, "Invalid instant extra: $instantStr", e)
                }
            } else {
                val totalMillis = combinedOffsetMillisOrNull(intent)
                if (totalMillis != null) {
                    mutableDebugClock.setOffset(Duration.ofMillis(totalMillis))
                    Log.i(TAG, "Debug clock offset set to ${totalMillis}ms")
                    triggerDebugTicks()
                }
            }
        }
    }

    // Each accepted change redraws every live engine immediately, so a broadcast repaints without
    // waiting for the next tick. Rejected input does not reach this, matching the instant path.
    private fun triggerDebugTicks() {
        for (engine in activeEngines) {
            engine.triggerDebugTick()
        }
    }

    // Sums the four offset extras, or returns null when the total is not one the clock can hold. The
    // extras are external input, so the sum uses checked arithmetic instead of wrapping to an
    // unrelated offset, and the instant it implies is then bounded to the epoch-millis range that
    // MutableDebugClock.millis converts within. That bound is measured from system time, not from
    // the debug clock's current value: setOffset replaces the offset and clears any fixed instant,
    // so the stored offset is added to the system base.
    private fun combinedOffsetMillisOrNull(intent: Intent): Long? {
        val offsetMillis = intent.getLongExtra(EXTRA_OFFSET_MILLIS, 0L)
        val offsetSeconds = intent.getLongExtra(EXTRA_OFFSET_SECONDS, 0L)
        val offsetMinutes = intent.getLongExtra(EXTRA_OFFSET_MINUTES, 0L)
        val offsetHours = intent.getLongExtra(EXTRA_OFFSET_HOURS, 0L)
        return try {
            val combinedMillis =
                Math.addExact(
                    Math.addExact(
                        Math.addExact(
                            offsetMillis,
                            Math.multiplyExact(offsetSeconds, MILLIS_PER_SECOND),
                        ),
                        Math.multiplyExact(offsetMinutes, SECONDS_PER_MINUTE * MILLIS_PER_SECOND),
                    ),
                    Math.multiplyExact(offsetHours, SECONDS_PER_HOUR * MILLIS_PER_SECOND),
                )
            val effectiveInstant = Instant.now().plus(Duration.ofMillis(combinedMillis))
            if (
                effectiveInstant.isBefore(minSupportedInstant) ||
                effectiveInstant.isAfter(maxSupportedInstant)
            ) {
                Log.e(TAG, "Offset extras out of supported range: ${combinedMillis}ms")
                null
            } else {
                combinedMillis
            }
        } catch (e: ArithmeticException) {
            Log.e(TAG, "Offset extras overflow the supported range", e)
            null
        } catch (e: DateTimeException) {
            Log.e(TAG, "Offset extras overflow the supported range", e)
            null
        }
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

        // onSurfaceDestroyed cancels the tick loop but leaves isEngineVisible true, so the debug
        // trigger must also check that a surface exists: runTick would otherwise draw onto a released
        // surface and its finally would re-arm the periodic loop across the surface gap. Set from
        // onSurfaceChanged, which the framework calls immediately after onSurfaceCreated.
        private var isSurfaceAvailable = false

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
            isSurfaceAvailable = true
            // Redraw for the new surface and restart the tick. The framework can destroy and
            // recreate the surface without a visibility change, and onSurfaceDestroyed cancels the
            // loop, so this is the only place that can resume it in that case.
            if (isEngineVisible) {
                runTick()
            }
        }

        override fun onSurfaceDestroyed(holder: SurfaceHolder) {
            isSurfaceAvailable = false
            stopTicking()
            super.onSurfaceDestroyed(holder)
        }

        override fun onDestroy() {
            activeEngines.remove(this)
            isDestroyed = true
            isEngineVisible = false
            locationStore.unregisterListener(settingsListener)
            dialSettingsStore.unregisterListener(settingsListener)
            stopTicking()
            super.onDestroy()
        }

        fun triggerDebugTick() {
            if (isDestroyed || !isEngineVisible || !isSurfaceAvailable) {
                // The skip is deliberate, but naming the failed guard keeps the common "broadcast
                // arrived yet the dial did not move" case diagnosable from the device harness.
                Log.d(
                    TAG,
                    "skipping debug tick: destroyed=$isDestroyed " +
                        "visible=$isEngineVisible surface=$isSurfaceAvailable",
                )
                return
            }
            runTick()
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
            val millisInSecond = Math.floorMod(clock.millis(), MILLIS_PER_SECOND)
            val isScheduled =
                handler.postDelayed(
                    Runnable { runTick() },
                    MILLIS_PER_SECOND - millisInSecond,
                )
            if (!isScheduled) {
                Log.w(
                    TAG,
                    "scheduleNextTick: postDelayed returned false; looper exiting or message queue shutting down",
                )
            }
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

    /** Debug broadcast intent action and extra constants. */
    internal companion object {
        /** Intent action to set or reset debug virtual time. */
        const val ACTION_DEBUG_SET_TIME = "io.github.godaniya.astronomicalclockswallpaper.DEBUG_SET_TIME"

        /** Long extra in milliseconds to add to the virtual time offset. */
        const val EXTRA_OFFSET_MILLIS = "offset_millis"

        /** Long extra in seconds to add to the virtual time offset. */
        const val EXTRA_OFFSET_SECONDS = "offset_seconds"

        /** Long extra in minutes to add to the virtual time offset. */
        const val EXTRA_OFFSET_MINUTES = "offset_minutes"

        /** Long extra in hours to add to the virtual time offset. */
        const val EXTRA_OFFSET_HOURS = "offset_hours"

        /** String extra with ISO-8601 instant string to fix virtual time to. */
        const val EXTRA_INSTANT = "instant"

        /** Boolean extra to reset virtual time back to system UTC. */
        const val EXTRA_RESET = "reset"

        private const val MILLIS_PER_SECOND = 1000L
        private const val SECONDS_PER_MINUTE = 60L
        private const val SECONDS_PER_HOUR = 3600L
        private val minSupportedInstant: Instant = Instant.ofEpochMilli(Long.MIN_VALUE)
        private val maxSupportedInstant: Instant = Instant.ofEpochMilli(Long.MAX_VALUE)
        private const val TAG = "AstronomicalClocksWallpaperService"
    }
}
