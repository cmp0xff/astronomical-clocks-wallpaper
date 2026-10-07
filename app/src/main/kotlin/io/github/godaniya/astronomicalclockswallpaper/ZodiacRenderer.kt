package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.util.Log
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import kotlin.math.sqrt

/** Complete ecliptic ring, including the part below the horizon, with tropical longitude labels. */
internal class ZodiacRenderer {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val degenerateCoordinateLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping divider: degenerate coordinate",
            level = Log.WARN,
        )
    private val nonFiniteEndpointLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping divider: non-finite endpoint",
            level = Log.WARN,
        )
    private val equinoxStarPath =
        Path().apply {
            val outerRadius = STAR_OUTER_RADIUS
            val innerRadius = STAR_OUTER_RADIUS * STAR_INNER_RATIO
            val angleStep = Math.PI / STAR_POINTS
            val startAngle = -Math.PI / 2.0
            for (i in 0 until STAR_VERTICES) {
                val r = if (i % 2 == 0) outerRadius else innerRadius
                val angle = startAngle + i * angleStep
                val x = (r * cos(angle)).toFloat()
                val y = (r * sin(angle)).toFloat()
                if (i == 0) {
                    moveTo(x, y)
                } else {
                    lineTo(x, y)
                }
            }
            close()
        }

    fun draw(canvas: Canvas, projection: OrlojProjection, palette: DialPalette = DialStyle.DARK_PALETTE) {
        val circle = projection.zodiacCircle
        paint.style = Paint.Style.STROKE
        paint.color = palette.gold
        paint.strokeWidth = RING_OUTER_WIDTH
        canvas.drawCircle(circle.center.x.toFloat(), circle.center.y.toFloat(), circle.radius.toFloat(), paint)
        paint.color = palette.zodiacBand
        paint.strokeWidth = RING_INNER_WIDTH
        canvas.drawCircle(circle.center.x.toFloat(), circle.center.y.toFloat(), circle.radius.toFloat(), paint)
        drawDividers(canvas, projection, palette)
        drawEquinoxStar(canvas, projection, palette)
        drawSigns(canvas, projection, palette)
    }

    private fun drawDividers(canvas: Canvas, projection: OrlojProjection, palette: DialPalette) {
        val circle = projection.zodiacCircle
        paint.style = Paint.Style.STROKE
        paint.color = palette.gold
        paint.strokeWidth = DIVIDER_WIDTH
        val halfBand = RING_INNER_WIDTH / 2
        var firstDegenerateIndex = -1
        var firstDegenerateDistance = 0.0
        var firstNonFiniteIndex = -1
        var firstNonFiniteStart = 0.0
        var firstNonFiniteEnd = 0.0
        for (index in SIGNS.indices) {
            val point = projection.eclipticPoint(index * DEGREES_PER_SIGN)
            val distance = hypot(x = point.x, y = point.y)
            if (!(distance > 0.0 && distance.isFinite())) {
                if (firstDegenerateIndex == -1) {
                    firstDegenerateIndex = index
                    firstDegenerateDistance = distance
                }
            } else {
                val ux = point.x / distance
                val uy = point.y / distance
                val start =
                    rayCircleDistance(
                        directionX = ux,
                        directionY = uy,
                        center = circle.center,
                        radius = circle.radius - halfBand,
                    )
                val end =
                    rayCircleDistance(
                        directionX = ux,
                        directionY = uy,
                        center = circle.center,
                        radius = circle.radius + halfBand,
                    )
                val startX = (ux * start).toFloat()
                val startY = (uy * start).toFloat()
                val endX = (ux * end).toFloat()
                val endY = (uy * end).toFloat()
                val isStartFinite = start.isFinite() && startX.isFinite() && startY.isFinite()
                val isEndFinite = end.isFinite() && endX.isFinite() && endY.isFinite()
                if (isStartFinite && isEndFinite) {
                    canvas.drawLine(startX, startY, endX, endY, paint)
                } else if (firstNonFiniteIndex == -1) {
                    firstNonFiniteIndex = index
                    firstNonFiniteStart = start
                    firstNonFiniteEnd = end
                }
            }
        }
        reportDividerAnomalies(
            degenerateIndex = firstDegenerateIndex,
            degenerateDistance = firstDegenerateDistance,
            nonFiniteIndex = firstNonFiniteIndex,
            nonFiniteStart = firstNonFiniteStart,
            nonFiniteEnd = firstNonFiniteEnd,
        )
    }

    private fun reportDividerAnomalies(
        degenerateIndex: Int,
        degenerateDistance: Double,
        nonFiniteIndex: Int,
        nonFiniteStart: Double,
        nonFiniteEnd: Double,
    ) {
        if (degenerateIndex != -1) {
            degenerateCoordinateLog
                .recordFailure(detail = "sign $degenerateIndex: distance=$degenerateDistance")
        } else {
            degenerateCoordinateLog.recordSuccess()
        }
        if (nonFiniteIndex != -1) {
            nonFiniteEndpointLog.recordFailure(
                detail = "sign $nonFiniteIndex: start=$nonFiniteStart, end=$nonFiniteEnd",
            )
        } else {
            nonFiniteEndpointLog.recordSuccess()
        }
    }

    private fun rayCircleDistance(directionX: Double, directionY: Double, center: DialPoint, radius: Double): Double {
        val centerSquared = center.x * center.x + center.y * center.y
        val projectionAlong = directionX * center.x + directionY * center.y
        val rawDiscriminant = projectionAlong * projectionAlong - centerSquared + radius * radius
        val discriminant = maxOf(a = 0.0, b = rawDiscriminant)
        return projectionAlong + sqrt(discriminant)
    }

    private fun drawEquinoxStar(canvas: Canvas, projection: OrlojProjection, palette: DialPalette) {
        val point = projection.eclipticPoint(0.0)
        val checkpoint = canvas.save()
        try {
            canvas.translate(point.x.toFloat(), point.y.toFloat())
            paint.style = Paint.Style.FILL
            paint.color = palette.gold
            canvas.drawPath(equinoxStarPath, paint)
        } finally {
            canvas.restoreToCount(checkpoint)
        }
    }

    private fun drawSigns(canvas: Canvas, projection: OrlojProjection, palette: DialPalette) {
        val checkpoint = canvas.save()
        try {
            canvas.scale(1 / DialStyle.TEXT_UNITS, 1 / DialStyle.TEXT_UNITS)
            paint.style = Paint.Style.FILL
            paint.color = palette.hand
            paint.typeface = SIGNS_TYPEFACE
            paint.textAlign = Paint.Align.CENTER
            paint.textSize = SIGN_SIZE * DialStyle.TEXT_UNITS
            val textOffset = -(paint.ascent() + paint.descent()) / CENTER_DIVISOR
            for ((index, sign) in SIGNS.withIndex()) {
                val point = projection.eclipticPoint(index * DEGREES_PER_SIGN + SIGN_OFFSET_DEG)
                canvas.drawText(
                    sign,
                    point.x.toFloat() * DialStyle.TEXT_UNITS,
                    point.y.toFloat() * DialStyle.TEXT_UNITS + textOffset,
                    paint,
                )
            }
        } finally {
            canvas.restoreToCount(checkpoint)
        }
    }

    private companion object {
        private const val TAG = "ZodiacRenderer"
        const val RING_OUTER_WIDTH = 0.09f
        const val RING_INNER_WIDTH = 0.075f
        const val DIVIDER_WIDTH = 0.0075f
        const val SIGN_SIZE = 0.044f
        const val CENTER_DIVISOR = 2f
        const val DEGREES_PER_SIGN = 30.0
        const val SIGN_OFFSET_DEG = 15.0
        const val STAR_OUTER_RADIUS = 0.02f
        const val STAR_INNER_RATIO = 0.4f
        const val STAR_POINTS = 5
        const val STAR_VERTICES = 10
        val SIGNS = listOf("ARI", "TAU", "GEM", "CAN", "LEO", "VIR", "LIB", "SCO", "SAG", "CAP", "AQU", "PIS")
        private val SIGNS_TYPEFACE: Typeface = Typeface.create("sans-serif", Typeface.NORMAL)
    }
}
