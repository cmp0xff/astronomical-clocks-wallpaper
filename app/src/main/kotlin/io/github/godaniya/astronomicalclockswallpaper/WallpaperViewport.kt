package io.github.godaniya.astronomicalclockswallpaper

import android.os.Build
import android.view.WindowInsets

/** Engine-local crop and inset reports; never resizes the platform's surface. */
internal class WallpaperViewport {
    private var leftInset = 0
    private var topInset = 0
    private var rightInset = 0
    private var bottomInset = 0
    var offsetX: Int = 0
        private set
    var offsetY: Int = 0
        private set

    // The visible window's origin inside the surface, in surface coordinates, as of the last resolve.
    // The framework scrolls the surface so this point lands at screen 0, so subtracting it from a drawn
    // surface point gives the screen point a capture shows. Pre-inset: reported insets move the usable
    // rectangle, not the scroll.
    var windowOriginX: Float = 0f
        private set
    var windowOriginY: Float = 0f
        private set

    fun offsets(xPixels: Int, yPixels: Int) {
        offsetX = xPixels
        offsetY = yPixels
    }

    // API 26–29 only exposes the legacy system-window inset accessors.
    @Suppress("DEPRECATION")
    fun insets(insets: WindowInsets) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val system =
                insets
                    .getInsetsIgnoringVisibility(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            leftInset = system.left
            topInset = system.top
            rightInset = system.right
            bottomInset = system.bottom
        } else {
            leftInset = insets.systemWindowInsetLeft
            topInset = insets.systemWindowInsetTop
            rightInset = insets.systemWindowInsetRight
            bottomInset = insets.systemWindowInsetBottom
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                insets.displayCutout?.let { cutout ->
                    leftInset = maxOf(a = leftInset, b = cutout.safeInsetLeft)
                    topInset = maxOf(a = topInset, b = cutout.safeInsetTop)
                    rightInset = maxOf(a = rightInset, b = cutout.safeInsetRight)
                    bottomInset = maxOf(a = bottomInset, b = cutout.safeInsetBottom)
                }
            }
        }
    }

    fun resolve(surfaceWidth: Int, surfaceHeight: Int, displayWidth: Int, displayHeight: Int): DialViewport {
        val width = minOf(a = surfaceWidth, b = displayWidth)
        val height = minOf(a = surfaceHeight, b = displayHeight)
        val left = windowStart(offset = offsetX, slack = surfaceWidth - width)
        val top = windowStart(offset = offsetY, slack = surfaceHeight - height)
        windowOriginX = left
        windowOriginY = top
        return DialViewport(
            left = maxOf(a = 0f, b = left + leftInset),
            top = maxOf(a = 0f, b = top + topInset),
            right = minOf(a = surfaceWidth.toFloat(), b = left + width - rightInset),
            bottom = minOf(a = surfaceHeight.toFloat(), b = top + height - bottomInset),
        )
    }

    // Offsets pan the visible window inside the surface. A window already as wide as the surface has
    // nothing to pan over, so it must stay put: clamping the start, rather than the end, keeps such a
    // window its full size instead of truncating it to the slack left beyond the reported scroll.
    private fun windowStart(offset: Int, slack: Int): Float =
        (-offset.toFloat()).coerceIn(minimumValue = 0f, maximumValue = slack.toFloat())
}
