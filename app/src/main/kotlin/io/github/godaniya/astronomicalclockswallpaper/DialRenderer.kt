package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.util.Log
import kotlin.math.cos
import kotlin.math.sin

/** Draws a 24-hour civil dial and optional site geometry; instances are confined to the rendering thread. */
internal class DialRenderer {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val plate = OrlojPlateRenderer()
    private val zodiac = ZodiacRenderer()
    private val sun = SunRenderer()
    private val moon = MoonRenderer()
    private val hand =
        Path().apply {
            moveTo(0f, -HAND_LENGTH)
            lineTo(HAND_TIP_WIDTH, -HAND_SHOULDER)
            lineTo(HAND_SHAFT_WIDTH, -HAND_SHOULDER)
            lineTo(HAND_SHAFT_WIDTH, HAND_TAIL)
            lineTo(-HAND_SHAFT_WIDTH, HAND_TAIL)
            lineTo(-HAND_SHAFT_WIDTH, -HAND_SHOULDER)
            lineTo(-HAND_TIP_WIDTH, -HAND_SHOULDER)
            close()
        }

    /** Each frame uses civil time and geometric values from the caller's single instant. */
    fun renderDial(
        canvas: Canvas,
        state: ClockState,
        geometry: DialGeometry? = null,
        layers: DialLayers = DialLayers(),
    ) {
        if (canvas.width <= 0 || canvas.height <= 0) {
            Log.w(TAG, "skipping render: empty canvas ${canvas.width}x${canvas.height}")
            return
        }
        canvas.drawColor(DialStyle.BACKGROUND)
        val radius = minOf(a = canvas.width, b = canvas.height) * RADIUS_FRACTION
        if (radius < MIN_DIAL_RADIUS) {
            Log.w(TAG, "skipping dial: radius $radius < minimum $MIN_DIAL_RADIUS")
            return
        }
        val checkpoint = canvas.save()
        try {
            canvas.translate(canvas.width / CENTER_DIVISOR, canvas.height / CENTER_DIVISOR)
            canvas.scale(radius / OUTER_RADIUS, radius / OUTER_RADIUS)
            drawCivilScale(canvas)
            val projection = geometry?.let(::OrlojProjection)
            plate.draw(canvas, projection, layers.isSunEnabled)
            if (projection != null && layers.isZodiacRingEnabled) {
                zodiac.draw(canvas, projection)
            }
            val sunPoint = projection?.sunPoint
            if (sunPoint != null && layers.isSunEnabled) {
                sun.draw(canvas, sunPoint)
            }
            // The marker needs both a projected position and a phase, so like the Sun it is drawn
            // only when the geometry carries them; the caller owns that suppression.
            if (projection != null && layers.isMoonEnabled) {
                val moonPoint = projection.moonPoint
                val moonPhase = geometry.moonPhaseLongitudeDeg
                if (moonPoint != null && moonPhase != null) {
                    moon.draw(
                        canvas = canvas,
                        point = moonPoint,
                        phaseLongitudeDeg = moonPhase,
                        southernHemisphere = projection.isSouthern,
                    )
                }
            }
            drawCivilHand(canvas, state.hourAngle)
        } finally {
            canvas.restoreToCount(checkpoint)
        }
    }

    private fun drawCivilScale(canvas: Canvas) {
        paint.style = Paint.Style.FILL
        paint.color = DialStyle.RIM
        canvas.drawCircle(0f, 0f, OUTER_RADIUS, paint)
        paint.style = Paint.Style.STROKE
        paint.color = DialStyle.GOLD
        paint.strokeWidth = RIM_WIDTH
        canvas.drawCircle(0f, 0f, OUTER_RADIUS, paint)
        canvas.drawCircle(0f, 0f, SCALE_INNER_RADIUS, paint)
        paint.color = DialStyle.MUTED_GOLD
        paint.strokeWidth = FINE_WIDTH
        canvas.drawCircle(0f, 0f, OUTER_RADIUS - RIM_INSET, paint)
        drawHours(canvas)
    }

    private fun drawHours(canvas: Canvas) {
        val checkpoint = canvas.save()
        canvas.scale(1 / DialStyle.TEXT_UNITS, 1 / DialStyle.TEXT_UNITS)
        paint.typeface = HOURS_TYPEFACE
        paint.textAlign = Paint.Align.CENTER
        paint.textSize = NUMERAL_SIZE * DialStyle.TEXT_UNITS
        val textOffset = -(paint.ascent() + paint.descent()) / CENTER_DIVISOR
        for ((index, numeral) in ROMAN_HOURS.withIndex()) {
            val angle =
                Math
                    .toRadians(
                        (index + 1) * CivilDialConstants.DEGREES_PER_HOUR + CivilDialConstants.MIDNIGHT_ANGLE_DEG,
                    )
            val x = sin(angle).toFloat()
            val y = -cos(angle).toFloat()
            paint.color = DialStyle.GOLD
            paint.style = Paint.Style.FILL
            canvas.drawText(
                numeral,
                x * NUMERAL_RADIUS * DialStyle.TEXT_UNITS,
                y * NUMERAL_RADIUS * DialStyle.TEXT_UNITS + textOffset,
                paint,
            )
            paint.strokeWidth = HOUR_TICK_WIDTH * DialStyle.TEXT_UNITS
            canvas.drawLine(
                x * TICK_INNER_RADIUS * DialStyle.TEXT_UNITS,
                y * TICK_INNER_RADIUS * DialStyle.TEXT_UNITS,
                x * TICK_OUTER_RADIUS * DialStyle.TEXT_UNITS,
                y * TICK_OUTER_RADIUS * DialStyle.TEXT_UNITS,
                paint,
            )
        }
        canvas.restoreToCount(checkpoint)
    }

    private fun drawCivilHand(canvas: Canvas, angleDegrees: Float) {
        val checkpoint = canvas.save()
        canvas.rotate(angleDegrees)
        paint.color = DialStyle.HAND
        paint.style = Paint.Style.FILL
        canvas.drawPath(hand, paint)
        canvas.restoreToCount(checkpoint)
        paint.color = DialStyle.GOLD
        canvas.drawCircle(0f, 0f, HUB_RADIUS, paint)
        paint.color = DialStyle.RIM
        canvas.drawCircle(0f, 0f, HUB_INNER_RADIUS, paint)
    }

    private companion object {
        const val CENTER_DIVISOR = 2f
        const val RADIUS_FRACTION = 0.43f
        const val MIN_DIAL_RADIUS = 16f
        const val OUTER_RADIUS = 1.37f
        const val SCALE_INNER_RADIUS = 1.05f
        const val RIM_WIDTH = 0.008f
        const val RIM_INSET = 0.026f
        const val FINE_WIDTH = 0.003f
        const val NUMERAL_SIZE = 0.084f
        const val NUMERAL_RADIUS = 1.205f
        const val HOUR_TICK_WIDTH = 0.005f
        const val TICK_INNER_RADIUS = 1.065f
        const val TICK_OUTER_RADIUS = 1.095f
        const val HAND_LENGTH = 1.055f
        const val HAND_SHOULDER = 0.95f
        const val HAND_TIP_WIDTH = 0.034f
        const val HAND_SHAFT_WIDTH = 0.011f
        const val HAND_TAIL = 0.13f
        const val HUB_RADIUS = 0.039f
        const val HUB_INNER_RADIUS = 0.018f
        private val HOURS_TYPEFACE: Typeface = Typeface.create("serif", Typeface.NORMAL)
        val ROMAN_HOURS =
            listOf(
                "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
                "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI", "XXII", "XXIII", "XXIV",
            )
    }
}

/** Contains argument and canvas-state failures while preserving the scheduled per-second redraw. */
internal fun containRenderFailure(draw: () -> Unit) {
    try {
        draw()
    } catch (e: IllegalArgumentException) {
        Log.e(TAG, "skipping frame: invalid render argument: ${e.message.orEmpty()}", e)
    } catch (e: IllegalStateException) {
        Log.e(TAG, "skipping frame: canvas in an invalid state: ${e.message.orEmpty()}", e)
    }
}

private const val TAG = "DialRenderer"
