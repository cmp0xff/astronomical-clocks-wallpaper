package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import kotlin.math.cos
import kotlin.math.sin

/** Paints the astronomical Sun marker on the ecliptic ring of the Orloj dial. */
internal class SunRenderer {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val sunPath = buildSunPath()

    /** Draws the marker at an already projected [point]; the caller owns suppression. */
    fun draw(canvas: Canvas, point: DialPoint) {
        val checkpoint = canvas.save()
        canvas.translate(point.x.toFloat(), point.y.toFloat())
        paint.color = DialStyle.GOLD
        paint.style = Paint.Style.FILL
        canvas.drawPath(sunPath, paint)
        canvas.restoreToCount(checkpoint)
    }

    private fun buildSunPath(): Path {
        val path = Path()
        path.addCircle(0f, 0f, CORE_RADIUS, Path.Direction.CW)
        val stepRad = 2 * Math.PI / RAY_COUNT
        for (i in 0 until RAY_COUNT) {
            val angle = i * stepRad
            val rayRadius = if (i % 2 == 0) LONG_RAY_RADIUS else SHORT_RAY_RADIUS
            val tipX = (cos(angle) * rayRadius).toFloat()
            val tipY = (sin(angle) * rayRadius).toFloat()
            val baseAngle1 = angle - RAY_HALF_WIDTH_RAD
            val baseAngle2 = angle + RAY_HALF_WIDTH_RAD
            val baseX1 = (cos(baseAngle1) * CORE_RADIUS).toFloat()
            val baseY1 = (sin(baseAngle1) * CORE_RADIUS).toFloat()
            val baseX2 = (cos(baseAngle2) * CORE_RADIUS).toFloat()
            val baseY2 = (sin(baseAngle2) * CORE_RADIUS).toFloat()

            path.moveTo(baseX1, baseY1)
            path.lineTo(tipX, tipY)
            path.lineTo(baseX2, baseY2)
            path.close()
        }
        return path
    }

    private companion object {
        const val RAY_COUNT = 16
        const val CORE_RADIUS = 0.020f
        const val LONG_RAY_RADIUS = 0.042f
        const val SHORT_RAY_RADIUS = 0.033f
        const val RAY_HALF_WIDTH_RAD = Math.PI / (RAY_COUNT * 2)
    }
}
