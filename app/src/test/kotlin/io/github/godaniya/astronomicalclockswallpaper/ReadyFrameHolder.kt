package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.SurfaceTexture
import android.view.Surface
import android.view.SurfaceHolder

/** A valid simulated surface for testing the engine's actual frame callback. */
internal class ReadyFrameHolder(delegate: SurfaceHolder, width: Int = 0, height: Int = 0) : SurfaceHolder by delegate {
    private val texture = SurfaceTexture(0)
    private val readySurface = Surface(texture)
    private val bitmap =
        if (width > 0 && height > 0) {
            Bitmap.createBitmap(
                width,
                height,
                Bitmap.Config.ARGB_8888,
            )
        } else {
            null
        }
    private val canvas = bitmap?.let(::Canvas) ?: Canvas()

    override fun getSurface(): Surface = readySurface

    override fun lockCanvas(): Canvas = canvas

    override fun unlockCanvasAndPost(canvas: Canvas) = Unit

    fun release() {
        bitmap?.recycle()
        readySurface.release()
        texture.release()
    }
}
