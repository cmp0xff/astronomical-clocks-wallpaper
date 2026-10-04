package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import kotlin.math.abs
import kotlin.math.cos

/**
 * Paints the Moon marker on the ecliptic ring of the Orloj dial at the geometry's geocentric
 * ecliptic longitude, with its illuminated phase.
 */
internal class MoonRenderer {
    private val shadowPaint =
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = DialStyle.MOON_SHADOW
            style = Paint.Style.FILL
        }
    private val illuminatedPaint =
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = DialStyle.MOON_ILLUMINATED
            style = Paint.Style.FILL
        }
    private val rimPaint =
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = DialStyle.GOLD
            style = Paint.Style.STROKE
            strokeWidth = RIM_STROKE_WIDTH
        }

    /**
     * Draws the marker at an already projected [point]; the caller owns suppression, as it does
     * for [SunRenderer]. The disc is drawn at a fixed screen-space orientation, so
     * [southernHemisphere] mirrors it horizontally rather than turning the bright limb to face the
     * Sun: the marker keeps one orientation around the whole ring.
     */
    fun draw(canvas: Canvas, point: DialPoint, phaseLongitudeDeg: Double, southernHemisphere: Boolean) {
        val checkpoint = canvas.save()
        try {
            canvas.translate(point.x.toFloat(), point.y.toFloat())
            if (southernHemisphere) {
                canvas.scale(-1f, 1f)
            }

            canvas.drawCircle(0f, 0f, MOON_RADIUS, shadowPaint)
            val phasePath = buildPhasePath(phaseLongitudeDeg)
            if (phasePath != null) {
                canvas.drawPath(phasePath, illuminatedPaint)
            }
            canvas.drawCircle(0f, 0f, MOON_RADIUS, rimPaint)
        } finally {
            canvas.restoreToCount(checkpoint)
        }
    }

    internal fun buildPhasePath(phaseDeg: Double): Path? {
        val normalizedPhase = phaseDeg.mod(FULL_TURN_DEGREES)
        if (normalizedPhase < EPSILON_DEG || normalizedPhase > FULL_TURN_DEGREES - EPSILON_DEG) {
            return null
        }
        val path = Path()
        if (abs(normalizedPhase - HALF_TURN_DEGREES) < EPSILON_DEG) {
            path.addCircle(0f, 0f, MOON_RADIUS, Path.Direction.CW)
        } else {
            val k = cos(Math.toRadians(normalizedPhase)).toFloat()
            val r = MOON_RADIUS
            val bounds = RectF(-r, -r, r, r)
            val termWidth = abs(k) * r
            val termBounds = RectF(-termWidth, -r, termWidth, r)

            if (normalizedPhase < HALF_TURN_DEGREES) {
                // Waxing: outer limb on right (+x), sweep clockwise from top to bottom
                path.arcTo(bounds, -RIGHT_ANGLE_DEG_FLOAT, HALF_TURN_DEG_FLOAT, false)
                val sweep = if (k > 0) -HALF_TURN_DEG_FLOAT else HALF_TURN_DEG_FLOAT
                path.arcTo(termBounds, RIGHT_ANGLE_DEG_FLOAT, sweep, false)
            } else {
                // Waning: outer limb on left (-x), sweep counter-clockwise from top to bottom
                path.arcTo(bounds, -RIGHT_ANGLE_DEG_FLOAT, -HALF_TURN_DEG_FLOAT, false)
                val sweep = if (k < 0) -HALF_TURN_DEG_FLOAT else HALF_TURN_DEG_FLOAT
                path.arcTo(termBounds, RIGHT_ANGLE_DEG_FLOAT, sweep, false)
            }
            path.close()
        }
        return path
    }

    internal companion object {
        const val MOON_RADIUS = 0.024f
        const val RIM_STROKE_WIDTH = 0.002f
        private const val EPSILON_DEG = 0.5
        private const val RIGHT_ANGLE_DEG_FLOAT = 90f
        private const val HALF_TURN_DEG_FLOAT = 180f
        private const val HALF_TURN_DEGREES = 180.0
    }
}
