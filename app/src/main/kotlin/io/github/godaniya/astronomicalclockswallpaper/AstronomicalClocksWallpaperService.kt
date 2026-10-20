package io.github.godaniya.astronomicalclockswallpaper

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.content.pm.ApplicationInfo
import android.content.res.Configuration
import android.graphics.Canvas
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.service.wallpaper.WallpaperService
import android.util.Log
import android.view.SurfaceHolder
import android.view.WindowInsets
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
            draw = { canvas, state, geometry, layers, palette, display, viewport ->
                dialRenderer.renderDisplay(
                    canvas = canvas,
                    state = state,
                    geometry = geometry,
                    layers = layers,
                    style = DialRenderStyle(palette = palette, display = display, viewport = viewport),
                )
            },
            clock = clock,
        )
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        for (engine in activeEngines) {
            engine.onConfigurationChanged(newConfig)
        }
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
            createEngine(
                draw = { canvas, state, geometry, layers, _ -> draw(canvas, state, geometry, layers) },
                holder = holder,
                clock = clock,
                deviceZone = deviceZone,
                calculator = calculator,
            )
        return engine
    }

    /** Creates an engine whose renderer receives display settings and its resolved viewport. */
    internal fun createEngine(
        draw: (Canvas, ClockState, DialGeometry?, DialLayers, DialPalette, DialDisplaySettings, DialViewport) -> Unit,
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
                        val token = intent.getStringExtra(EXTRA_DIAGNOSTICS)
                        if (token == null) {
                            handleDebugSetTime(intent)
                        } else {
                            for (engine in activeEngines) engine.triggerDebugTick(token)
                        }
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
            Log.i(TAG, DEBUG_CLOCK_RESET_MESSAGE)
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
                    Log.i(TAG, "$DEBUG_CLOCK_FIXED_INSTANT_PREFIX$instant")
                    triggerDebugTicks()
                } catch (e: DateTimeParseException) {
                    Log.e(TAG, "Invalid instant extra: $instantStr", e)
                }
            } else {
                val totalMillis = combinedOffsetMillisOrNull(intent)
                if (totalMillis != null) {
                    mutableDebugClock.setOffset(Duration.ofMillis(totalMillis))
                    Log.i(TAG, "$DEBUG_CLOCK_OFFSET_PREFIX$totalMillis$DEBUG_CLOCK_OFFSET_SUFFIX")
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

    // Engine needs its enclosing service because its Java superclass is a non-static inner class.
    // Keep the platform lifecycle, display callbacks, diagnostics and tick loop together: splitting
    // them solely for the function budget would separate rendering from its lifecycle guards.
    @Suppress("UnnecessaryInnerClass", "TooManyFunctions")
    private inner class ClockEngine(
        private val draw: (
            Canvas,
            ClockState,
            DialGeometry?,
            DialLayers,
            DialPalette,
            DialDisplaySettings,
            DialViewport,
        ) -> Unit,
        private val frameHolder: SurfaceHolder?,
        private val clock: Clock,
        private val deviceZone: () -> ZoneId,
        private val calculator: AstronomyCalculator,
    ) : Engine() {
        private val engineResources
            get() =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    displayContext?.resources ?: resources
                } else {
                    resources
                }
        private val handler = Handler(Looper.getMainLooper())
        private val locationStore = LocationStore(applicationContext, deviceZone)
        private val dialSettingsStore = DialSettingsStore(applicationContext)
        private val displayStore = DialDisplayStore(applicationContext)
        private val viewport = WallpaperViewport()
        private var diagnosedLayout: String? = null
        private var diagnosticToken: String = "layout"
        private val appearanceStore = AppearanceStore(applicationContext)
        private var settings = loadSettings()
        private var isDestroyed = false
        private var isPreviewEngine = false
        private var currentConfig: Configuration? = null
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
        // onSurfaceChanged, which the framework calls immediately after onSurfaceCreated. Injected
        // ready holders also mark it available for the internal frame-test seam.
        private var isSurfaceAvailable = frameHolder?.surface?.isValid == true

        // Both of these faults recur on every tick while they last, so they log the first occurrence
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

        // Stable runnables so a queued tick and a queued viewport redraw can be cancelled
        // individually: rescheduling the tick must not drop a redraw a viewport change requested.
        // Assigned in init rather than inline because a construction-initializer lambda would be
        // analysed as reading the properties runTick touches before they are initialized.
        private val tickRunnable: Runnable
        private val viewportRedrawRunnable: Runnable

        init {
            tickRunnable = Runnable { runTick() }
            viewportRedrawRunnable = Runnable { redrawForViewport() }
            // Keep a strong listener reference for this engine's lifetime. Updates only replace the
            // cached snapshot; hidden engines must not acquire a surface or schedule a tick. The
            // engine itself is registered with activeEngines by createEngine, which owns every
            // construction site.
            locationStore.registerListener(settingsListener)
            dialSettingsStore.registerListener(settingsListener)
            appearanceStore.registerListener(settingsListener)
            displayStore.registerListener(settingsListener)
        }

        private fun loadSettings(): WallpaperSettings {
            val settings =
                WallpaperSettings(
                    location = locationStore.load(),
                    layers = dialSettingsStore.load(),
                    appearance = appearanceStore.load(),
                    display = displayStore.load(),
                )
            return settings
        }

        fun onConfigurationChanged(newConfig: Configuration) {
            currentConfig = newConfig
            if (settings.appearance != DialAppearance.SYSTEM) {
                // A fixed theme cannot change with night mode, so the palette is unaffected; the skip
                // is logged so a device harness does not read the missing repaint as a failure.
                Log.d(TAG, "skipping appearance redraw: fixed appearance=${settings.appearance}")
                return
            }
            // onSurfaceDestroyed cancels the loop but leaves isEngineVisible true, so a night-mode
            // change in that gap must not runTick onto the released surface: its finally would
            // re-arm the periodic loop across the whole surface gap. Same guard as triggerDebugTick.
            if (isDestroyed || !isEngineVisible || !isSurfaceAvailable) {
                Log.d(
                    TAG,
                    "skipping appearance redraw: destroyed=$isDestroyed " +
                        "visible=$isEngineVisible surface=$isSurfaceAvailable",
                )
                return
            }
            runTick()
        }

        override fun onCreate(surfaceHolder: SurfaceHolder) {
            super.onCreate(surfaceHolder)
            isPreviewEngine = isPreview
        }

        override fun onVisibilityChanged(visible: Boolean) {
            if (isDestroyed) {
                return
            }
            isEngineVisible = visible
            if (visible && isSurfaceAvailable) {
                runTick()
            } else {
                stopTicking()
            }
        }

        override fun onSurfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
            super.onSurfaceChanged(holder, format, width, height)
            if (isDestroyed) return
            isSurfaceAvailable = true
            // Redraw for the new surface and restart the tick. The framework can destroy and
            // recreate the surface without a visibility change, and onSurfaceDestroyed cancels the
            // loop, so this is the only place that can resume it in that case.
            if (isEngineVisible) {
                runTick()
            }
        }

        override fun onApplyWindowInsets(insets: WindowInsets) {
            super.onApplyWindowInsets(insets)
            viewport.insets(insets)
            requestViewportRedraw()
        }

        override fun onOffsetsChanged(
            xOffset: Float,
            yOffset: Float,
            xOffsetStep: Float,
            yOffsetStep: Float,
            xPixelOffset: Int,
            yPixelOffset: Int,
        ) {
            viewport.offsets(xPixels = xPixelOffset, yPixels = yPixelOffset)
            requestViewportRedraw()
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
            appearanceStore.unregisterListener(settingsListener)
            displayStore.unregisterListener(settingsListener)
            stopTicking()
            super.onDestroy()
        }

        fun triggerDebugTick(token: String = "clock") {
            diagnosticToken = token
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
            diagnosedLayout = null
            runTick()
        }

        // Draws a frame and posts the next tick. Safe to call repeatedly: scheduleNextTick clears any
        // pending tick first, so the loop is never double-scheduled. A queued viewport redraw is
        // dropped because this frame already resolves the latest viewport.
        private fun runTick() {
            if (!canDraw()) return
            handler.removeCallbacks(viewportRedrawRunnable)
            try {
                drawFrameSafely()
            } finally {
                scheduleNextTick()
            }
        }

        // Viewport callbacks can arrive in bursts (a scrolling launcher reports offsets on every
        // movement) and run on the framework's callback thread path. Coalesce them into one posted
        // draw so the dial re-centres promptly instead of waiting for the next minute boundary,
        // without drawing synchronously from the callback.
        private fun requestViewportRedraw() {
            if (!canDraw()) return
            handler.removeCallbacks(viewportRedrawRunnable)
            if (!handler.post(viewportRedrawRunnable)) {
                Log.w(
                    TAG,
                    "requestViewportRedraw: post returned false; looper exiting or message queue shutting down",
                )
            }
        }

        // Draws only the frame a viewport change affects; the periodic loop stays owned by runTick.
        private fun redrawForViewport() {
            if (!canDraw()) return
            drawFrameSafely()
        }

        // Handled argument/state failures return from drawFrame normally; unexpected exceptions are
        // contained to preserve the tick loop, while severe VM errors still propagate.
        @Suppress("TooGenericExceptionCaught")
        private fun drawFrameSafely() {
            try {
                drawFrame()
                renderFailureLog.recordSuccess()
            } catch (e: Exception) {
                renderFailureLog.recordFailure(e)
            }
        }

        private fun canDraw(): Boolean = !isDestroyed && isEngineVisible && isSurfaceAvailable

        // The handler carries both the tick and any queued viewport redraw, so cancelling all
        // messages stops everything this engine scheduled.
        private fun stopTicking() {
            handler.removeCallbacksAndMessages(null)
        }

        private fun scheduleNextTick() {
            handler.removeCallbacks(tickRunnable)
            val millisInMinute = Math.floorMod(clock.millis(), MILLIS_PER_MINUTE)
            val delayMillis = (MILLIS_PER_MINUTE - millisInMinute).coerceAtLeast(1L)
            val isScheduled =
                handler.postDelayed(
                    tickRunnable,
                    delayMillis,
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

        private fun diagnoseLayout(usable: DialViewport, display: DialDisplaySettings, palette: DialPalette) {
            if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE == 0) return
            val placement = usable.resolve(display)
            val layout =
                if (placement == null) {
                    "$DIAL_LAYOUT_PREFIX token=$diagnosticToken engine=${System.identityHashCode(
                        this,
                    )} preview=$isPreviewEngine unusable"
                } else {
                    "$DIAL_LAYOUT_PREFIX token=$diagnosticToken engine=${System.identityHashCode(
                        this,
                    )} preview=$isPreviewEngine " +
                        // The engine draws in surface coordinates; the framework scrolls the surface so
                        // the clamped window origin resolved for this frame lands at screen 0. The
                        // harnesses probe captured screen pixels, so subtract that origin - the scroll,
                        // not the raw reported offset, and not the inset-adjusted usable rectangle.
                        "cx=${placement.centerX - viewport.windowOriginX} " +
                        "cy=${placement.centerY - viewport.windowOriginY} " +
                        "radius=${placement.radius} brightness=${display.brightness} " +
                        "dark=${palette == DialStyle.DARK_PALETTE} " +
                        "width=${engineResources.displayMetrics.widthPixels} height=${engineResources.displayMetrics.heightPixels}"
                }
            if (layout != diagnosedLayout) {
                Log.d(DISPLAY_LOG_TAG, layout)
                diagnosedLayout = layout
            }
        }

        private fun drawFrame() {
            wallpaperFrame.drawWallpaperFrame(frameHolder ?: surfaceHolder) { canvas ->
                val instant = clock.instant()
                val snapshot = settings
                val location = snapshot.location
                val civilTime = instant.atZone(location?.zoneId ?: deviceZone()).toLocalTime()
                val geometry = dialGeometryOrNull(instant, location)
                val config = currentConfig ?: engineResources.configuration
                val isSystemNight =
                    config.uiMode and Configuration.UI_MODE_NIGHT_MASK ==
                        Configuration.UI_MODE_NIGHT_YES
                val palette = DialStyle.paletteFor(snapshot.appearance, isSystemNight)
                val metrics = engineResources.displayMetrics
                val usable =
                    viewport
                        .resolve(
                            surfaceWidth = canvas.width,
                            surfaceHeight = canvas.height,
                            displayWidth = metrics.widthPixels,
                            displayHeight = metrics.heightPixels,
                        )
                diagnoseLayout(usable, snapshot.display, palette)
                draw(canvas, clockState(civilTime), geometry, snapshot.layers, palette, snapshot.display, usable)
            }
        }
    }

    /** Debug broadcast intent action, extra names, and log contract constants. */
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

        /** String extra carrying a diagnostic token for layout readback without changing virtual time. */
        const val EXTRA_DIAGNOSTICS = "diagnostics"

        /** Logcat tag emitted by the wallpaper service. */
        const val TAG = "AstronomicalClocksWallpaperService"

        /** Logcat tag emitted for resolved dial display layout diagnostics. */
        const val DISPLAY_LOG_TAG = "DialDisplay"

        /** Leading token of a resolved dial layout diagnostic line. */
        const val DIAL_LAYOUT_PREFIX = "DialLayout"

        /** Log message emitted when the debug clock resets to system UTC. */
        const val DEBUG_CLOCK_RESET_MESSAGE = "Debug clock reset to system UTC"

        /** Log message prefix emitted when the debug clock is fixed to an ISO instant. */
        const val DEBUG_CLOCK_FIXED_INSTANT_PREFIX = "Debug clock fixed to instant: "

        /** Log message prefix emitted when the debug clock offset is updated. */
        const val DEBUG_CLOCK_OFFSET_PREFIX = "Debug clock offset set to "

        /** Log message unit suffix emitted when the debug clock offset is updated. */
        const val DEBUG_CLOCK_OFFSET_SUFFIX = "ms"

        private const val MILLIS_PER_SECOND = 1000L
        private const val MILLIS_PER_MINUTE = 60_000L
        private const val SECONDS_PER_MINUTE = 60L
        private const val SECONDS_PER_HOUR = 3600L
        private val minSupportedInstant: Instant = Instant.ofEpochMilli(Long.MIN_VALUE)
        private val maxSupportedInstant: Instant = Instant.ofEpochMilli(Long.MAX_VALUE)
    }
}
