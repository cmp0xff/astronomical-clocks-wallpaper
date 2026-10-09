package io.github.godaniya.astronomicalclockswallpaper

/** Usable rectangle in surface coordinates, after reported crop and system insets. */
internal data class DialViewport(val left: Float, val top: Float, val right: Float, val bottom: Float) {
    companion object {
        const val RADIUS_FRACTION = 0.43f

        // Includes half the outer rim stroke, in sky-radius units.
        const val STROKED_EXTENT = 1f + DialRenderer.RIM_WIDTH / 2f / DialRenderer.OUTER_RADIUS
        const val MIN_RADIUS = 16f
        const val PERCENT = 100f

        fun full(width: Int, height: Int): DialViewport =
            DialViewport(left = 0f, top = 0f, right = width.toFloat(), bottom = height.toFloat())
    }
}

internal data class DialPlacement(val centerX: Float, val centerY: Float, val radius: Float)

internal fun DialViewport.resolve(settings: DialDisplaySettings): DialPlacement? {
    val width = right - left
    val height = bottom - top

    val radius = minOf(a = width, b = height) * DialViewport.RADIUS_FRACTION * (settings.size / DialViewport.PERCENT)
    val extent = radius * DialViewport.STROKED_EXTENT
    if (!listOf(left, top, right, bottom).all { it.isFinite() } ||
        radius < DialViewport.MIN_RADIUS ||
        extent * 2f > minOf(a = width, b = height)
    ) {
        return null
    }
    return DialPlacement(
        centerX =
            (left + right) / 2f + (width - 2f * extent) * (settings.horizontal - CENTER_PERCENT) / DialViewport.PERCENT,
        centerY =
            (top + bottom) / 2f + (height - 2f * extent) * (settings.vertical - CENTER_PERCENT) / DialViewport.PERCENT,
        radius = radius,
    )
}

private const val CENTER_PERCENT = 50
