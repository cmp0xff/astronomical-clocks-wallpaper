package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.graphics.SurfaceTexture
import android.os.Looper
import android.os.SystemClock
import android.service.wallpaper.WallpaperService
import android.util.Log
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog
import java.time.Duration

/** Exercises the engine's real frame operation with controlled canvas failures on both supported test SDKs. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class WallpaperFrameTest {
    private val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java)
    private lateinit var holder: FrameHolder
    private lateinit var engine: WallpaperService.Engine
    private val drawnCanvases = mutableListOf<Canvas>()
    private var drawFailure: RuntimeException? = null

    @Before
    fun createEngine() {
        controller.create()
        holder = FrameHolder(SurfaceView(controller.get()).holder)
        assertTrue("fault tests must reach canvas acquisition", holder.surface.isValid)
        engine =
            controller.get().createEngine(
                draw = { canvas, _, _, _ ->
                    drawnCanvases.add(canvas)
                    drawFailure?.let { throw it }
                },
                holder = holder,
            )
        ShadowLog.clear()
    }

    @After
    fun destroyEngine() {
        engine.onDestroy()
        holder.release()
        controller.destroy()
    }

    @Test
    fun successfulFramePostsOnce() {
        engine.onVisibilityChanged(true)
        assertEquals(1, holder.lockAttempts)
        assertEquals(listOf(holder.canvas), drawnCanvases)
        assertEquals(listOf(holder.canvas), holder.postedCanvases)
        assertTrue(ShadowLog.getLogsForTag(SERVICE_TAG).isEmpty())
        assertNextFrameSucceeds()
    }

    @Test
    fun nullCanvasStillSchedules() {
        holder.isCanvasAvailable = false
        engine.onVisibilityChanged(true)
        assertAcquisitionSkipped()
        assertLog(level = Log.WARN, tag = SERVICE_TAG, message = "skipping frame: lockCanvas returned null")
        holder.isCanvasAvailable = true
        assertNextFrameSucceeds()
    }

    @Test
    fun invalidSurfaceSkipsAndLogs() {
        holder.surface.release()
        engine.onVisibilityChanged(true)
        assertEquals(0, holder.lockAttempts)
        assertTrue(drawnCanvases.isEmpty())
        assertTrue(holder.postedCanvases.isEmpty())
        assertLog(level = Log.DEBUG, tag = SERVICE_TAG, message = "skipping frame: surface not ready")
        assertTrue(shadowOf(Looper.getMainLooper()).nextScheduledTaskTime > Duration.ZERO)
    }

    @Test
    fun lockArgumentFailureRecovers() {
        assertLockFailure(
            IllegalArgumentException("lock argument"),
            "skipping frame: lockCanvas failed (surface released)",
        )
    }

    @Test
    fun lockStateFailureRecovers() {
        assertLockFailure(
            IllegalStateException("lock state"),
            "skipping frame: lockCanvas failed (invalid surface state)",
        )
    }

    @Test
    fun drawArgumentFailureRecovers() {
        assertDrawFailure(
            IllegalArgumentException("draw argument"),
            "skipping frame: invalid render argument: draw argument",
        )
    }

    @Test
    fun drawStateFailureRecovers() {
        assertDrawFailure(
            IllegalStateException("draw state"),
            "skipping frame: canvas in an invalid state: draw state",
        )
    }

    @Test
    fun postArgumentFailureRecovers() {
        assertPostFailure(
            IllegalArgumentException("post argument"),
            Log.WARN,
            "unlockCanvasAndPost failed: surface already released",
        )
    }

    @Test
    fun postStateFailureRecovers() {
        assertPostFailure(
            IllegalStateException("post state"),
            Log.ERROR,
            "unlockCanvasAndPost failed: invalid surface state",
        )
    }

    @Test
    fun unrelatedDrawFailureRecovers() {
        val failure = UnsupportedOperationException("unhandled draw")
        drawFailure = failure
        engine.onVisibilityChanged(true)
        assertEquals(listOf(holder.canvas), drawnCanvases)
        assertEquals(listOf(holder.canvas), holder.postedCanvases)
        assertLog(
            level = Log.ERROR,
            tag = SERVICE_TAG,
            message = "unexpected error in drawFrame; keeping tick loop alive",
            failure = failure,
        )
        drawFailure = null
        assertNextFrameSucceeds()
    }

    @Test
    fun repeatedDrawFailureLogsOnce() {
        drawFailure = UnsupportedOperationException("persistent draw")
        engine.onVisibilityChanged(true)
        advanceTicks()
        // Six failed ticks in total; only the first carries a stack trace.
        assertEquals(6, drawnCanvases.size)
        val errors = ShadowLog.getLogsForTag(SERVICE_TAG).filter { it.type == Log.ERROR }
        assertEquals(1, errors.size)
        assertEquals("unexpected error in drawFrame; keeping tick loop alive", errors.single().msg)
    }

    @Test
    fun repeatedNullCanvasLogsOnce() {
        holder.isCanvasAvailable = false
        engine.onVisibilityChanged(true)
        advanceTicks()
        assertEquals(6, holder.lockAttempts)
        val warnings = ShadowLog.getLogsForTag(SERVICE_TAG).filter { it.type == Log.WARN }
        assertEquals(1, warnings.size)
        assertEquals("skipping frame: lockCanvas returned null", warnings.single().msg)
    }

    @Test
    fun repeatedInvalidSurfaceLogsOnce() {
        holder.surface.release()
        engine.onVisibilityChanged(true)
        advanceTicks()
        val debugs = ShadowLog.getLogsForTag(SERVICE_TAG).filter { it.type == Log.DEBUG }
        assertEquals(1, debugs.size)
        assertEquals("skipping frame: surface not ready", debugs.single().msg)
    }

    @Test
    fun repeatedLockFailureLogsOnce() {
        holder.lockFailure = IllegalArgumentException("persistent lock")
        engine.onVisibilityChanged(true)
        advanceTicks()
        assertEquals(6, holder.lockAttempts)
        val warnings = ShadowLog.getLogsForTag(SERVICE_TAG).filter { it.type == Log.WARN }
        assertEquals(1, warnings.size)
        assertEquals("skipping frame: lockCanvas failed (surface released)", warnings.single().msg)
    }

    @Test
    fun repeatedDrawFaultLogsOnce() {
        drawFailure = IllegalArgumentException("persistent draw arg")
        engine.onVisibilityChanged(true)
        advanceTicks()
        assertEquals(6, drawnCanvases.size)
        val errors = ShadowLog.getLogsForTag(RENDER_TAG).filter { it.type == Log.ERROR }
        assertEquals(1, errors.size)
        assertEquals("skipping frame: invalid render argument: persistent draw arg", errors.single().msg)
    }

    @Test
    fun repeatedPostFailureLogsOnce() {
        holder.postFailure = IllegalStateException("persistent post state")
        engine.onVisibilityChanged(true)
        advanceTicks()
        val errors = ShadowLog.getLogsForTag(SERVICE_TAG).filter { it.type == Log.ERROR }
        assertEquals(1, errors.size)
        assertEquals("unlockCanvasAndPost failed: invalid surface state", errors.single().msg)
    }

    @Test
    fun burstRecoveryLogsRecovery() {
        holder.isCanvasAvailable = false
        engine.onVisibilityChanged(true)
        advanceTicks(count = 1)
        holder.isCanvasAvailable = true
        ShadowLog.clear()
        advanceTicks(count = 1)
        val infos = ShadowLog.getLogsForTag(SERVICE_TAG).filter { it.type == Log.INFO }
        assertEquals(1, infos.size)
        assertTrue(infos.single().msg.contains("recovered after 2 consecutive failures"))
    }

    @Test
    fun hiddenEngineStopsDrawing() {
        engine.onVisibilityChanged(true)
        engine.onVisibilityChanged(false)
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(2))
        assertEquals(1, drawnCanvases.size)
        assertEquals(Duration.ZERO, shadowOf(Looper.getMainLooper()).nextScheduledTaskTime)
    }

    private fun assertLockFailure(failure: RuntimeException, message: String) {
        holder.lockFailure = failure
        engine.onVisibilityChanged(true)
        assertAcquisitionSkipped()
        assertLog(level = Log.WARN, tag = SERVICE_TAG, message = message, failure = failure)
        holder.lockFailure = null
        assertNextFrameSucceeds()
    }

    private fun assertDrawFailure(failure: RuntimeException, message: String) {
        drawFailure = failure
        engine.onVisibilityChanged(true)
        assertEquals(listOf(holder.canvas), drawnCanvases)
        assertEquals(listOf(holder.canvas), holder.postedCanvases)
        assertLog(level = Log.ERROR, tag = RENDER_TAG, message = message, failure = failure)
        drawFailure = null
        assertNextFrameSucceeds()
    }

    private fun assertPostFailure(failure: RuntimeException, level: Int, message: String) {
        holder.postFailure = failure
        engine.onVisibilityChanged(true)
        assertEquals(listOf(holder.canvas), drawnCanvases)
        assertEquals(listOf(holder.canvas), holder.postedCanvases)
        assertLog(level = level, tag = SERVICE_TAG, message = message, failure = failure)
        holder.postFailure = null
        assertNextFrameSucceeds()
    }

    private fun assertAcquisitionSkipped() {
        assertEquals(1, holder.lockAttempts)
        assertTrue(drawnCanvases.isEmpty())
        assertTrue(holder.postedCanvases.isEmpty())
    }

    private fun assertLog(level: Int, tag: String, message: String, failure: Throwable? = null) {
        val entry = ShadowLog.getLogsForTag(tag).single()
        assertEquals(level, entry.type)
        assertEquals(message, entry.msg)
        assertSame(failure, entry.throwable)
    }

    private fun advanceTicks(count: Int = 5) {
        val looper = shadowOf(Looper.getMainLooper())
        repeat(count) {
            val delay = looper.nextScheduledTaskTime.toMillis() - SystemClock.uptimeMillis()
            looper.idleFor(Duration.ofMillis(delay))
        }
    }

    private fun assertNextFrameSucceeds() {
        val looper = shadowOf(Looper.getMainLooper())
        val delay = looper.nextScheduledTaskTime.toMillis() - SystemClock.uptimeMillis()
        assertTrue("a later tick must remain scheduled", delay in 1L..1000L)
        val previousLocks = holder.lockAttempts
        val previousDraws = drawnCanvases.size
        val previousPosts = holder.postedCanvases.size
        ShadowLog.clear()
        looper.idleFor(Duration.ofMillis(delay))
        assertEquals(previousLocks + 1, holder.lockAttempts)
        assertEquals(previousDraws + 1, drawnCanvases.size)
        assertSame(holder.canvas, drawnCanvases.last())
        assertEquals(previousPosts + 1, holder.postedCanvases.size)
        assertSame(holder.canvas, holder.postedCanvases.last())
        assertTrue(ShadowLog.getLogsForTag(SERVICE_TAG).isEmpty())
        assertTrue(ShadowLog.getLogsForTag(RENDER_TAG).isEmpty())
        assertTrue(looper.nextScheduledTaskTime.toMillis() > SystemClock.uptimeMillis())
    }

    private class FrameHolder(delegate: SurfaceHolder) : SurfaceHolder by delegate {
        val canvas = Canvas()
        val postedCanvases = mutableListOf<Canvas>()
        var isCanvasAvailable = true
        var lockFailure: RuntimeException? = null
        var postFailure: RuntimeException? = null
        var lockAttempts = 0
            private set
        private val surfaceTexture = SurfaceTexture(0)
        private val readySurface = Surface(surfaceTexture)

        fun release() {
            readySurface.release()
            surfaceTexture.release()
        }

        override fun getSurface(): Surface = readySurface

        override fun lockCanvas(): Canvas? {
            lockAttempts++
            lockFailure?.let { throw it }
            return if (isCanvasAvailable) canvas else null
        }

        override fun unlockCanvasAndPost(canvas: Canvas) {
            postedCanvases.add(canvas)
            postFailure?.let { throw it }
        }
    }

    private companion object {
        const val SERVICE_TAG = "AstronomicalClocksWallpaperService"
        const val RENDER_TAG = "DialRenderer"
    }
}
