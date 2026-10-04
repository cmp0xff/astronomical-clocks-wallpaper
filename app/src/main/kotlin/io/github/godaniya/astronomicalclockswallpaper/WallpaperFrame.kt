package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.util.Log
import android.view.SurfaceHolder

/**
 * Acquires, draws, and posts wallpaper frames while bounding repeated surface and rendering log noise.
 * Instances are confined to the engine's tick thread.
 */
internal class WallpaperFrame {
    private val renderFailures = RenderFailureContainment()
    private val surfaceNotReadyLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping frame: surface not ready",
            level = Log.DEBUG,
        )
    private val nullCanvasLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping frame: lockCanvas returned null",
            level = Log.WARN,
        )
    private val lockSurfaceReleasedLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping frame: lockCanvas failed (surface released)",
            level = Log.WARN,
        )
    private val lockInvalidStateLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping frame: lockCanvas failed (invalid surface state)",
            level = Log.WARN,
        )
    private val unlockSurfaceReleasedLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "unlockCanvasAndPost failed: surface already released",
            level = Log.WARN,
        )
    private val unlockInvalidStateLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "unlockCanvasAndPost failed: invalid surface state",
            level = Log.ERROR,
        )

    /** Acquires, draws, and posts one frame, logging handled surface and rendering failures. */
    fun drawWallpaperFrame(holder: SurfaceHolder, draw: (Canvas) -> Unit) {
        val surface = holder.surface
        if (surface == null || !surface.isValid) {
            // A missing surface can be expected during lifecycle changes; debug level distinguishes
            // that temporary state from an acquisition or rendering failure.
            surfaceNotReadyLog.recordFailure()
            return
        }
        surfaceNotReadyLog.recordSuccess()
        val canvas = lockCanvasOrNull(holder) ?: return
        try {
            renderFailures.containRenderFailure { draw(canvas) }
        } finally {
            unlockCanvasAndPost(holder, canvas)
        }
    }

    private fun lockCanvasOrNull(holder: SurfaceHolder): Canvas? {
        try {
            val canvas = holder.lockCanvas()
            if (canvas == null) {
                nullCanvasLog.recordFailure()
            } else {
                nullCanvasLog.recordSuccess()
                lockSurfaceReleasedLog.recordSuccess()
                lockInvalidStateLog.recordSuccess()
            }
            return canvas
        } catch (e: IllegalArgumentException) {
            lockSurfaceReleasedLog.recordFailure(e)
        } catch (e: IllegalStateException) {
            lockInvalidStateLog.recordFailure(e)
        }
        return null
    }

    private fun unlockCanvasAndPost(holder: SurfaceHolder, canvas: Canvas) {
        try {
            holder.unlockCanvasAndPost(canvas)
            unlockSurfaceReleasedLog.recordSuccess()
            unlockInvalidStateLog.recordSuccess()
        } catch (e: IllegalArgumentException) {
            unlockSurfaceReleasedLog.recordFailure(e)
        } catch (e: IllegalStateException) {
            unlockInvalidStateLog.recordFailure(e)
        }
    }

    private companion object {
        const val TAG = "AstronomicalClocksWallpaperService"
    }
}
