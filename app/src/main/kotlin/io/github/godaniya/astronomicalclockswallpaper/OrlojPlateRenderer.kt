package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import kotlin.math.abs

/**
 * Paints the geometric plate inside the sky boundary. The Sun layer adds the day/twilight/night
 * fills and draws their horizon and night contour strokes; the tropics, equator, and rim stay.
 *
 * Caches the static plate geometry keyed by observer latitude and true obliquity, so steady-state
 * ticks redraw pre-built Path objects instead of re-sampling contours or allocating new paths.
 * Each frame still builds an OrlojProjection for the rotating zodiac ring.
 *
 * Every cached path belongs to the Sun layer, so it is built and drawn only while that layer is
 * enabled; disabling the Sun therefore samples no contours, as it did before the paths were cached.
 */
internal class OrlojPlateRenderer {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var cachedPlate: CachedPlate? = null

    fun draw(
        canvas: Canvas,
        projection: OrlojProjection?,
        isSunEnabled: Boolean,
        palette: DialPalette = DialStyle.DARK_PALETTE,
    ) {
        paint.style = Paint.Style.FILL
        paint.color = palette.night
        canvas.drawCircle(0f, 0f, SKY_RADIUS, paint)
        if (projection != null) {
            val plate = if (isSunEnabled) getOrCreatePlate(projection) else null
            if (plate != null) {
                paint.style = Paint.Style.FILL
                paint.color = palette.twilight
                canvas.drawPath(plate.paths.twilightFill, paint)
                paint.color = palette.sky
                canvas.drawPath(plate.paths.dayFill, paint)
            }
            drawGrid(
                canvas = canvas,
                projection = projection,
                plate = plate,
                palette = palette,
            )
        }
        paint.color = palette.gold
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = OUTER_WIDTH
        canvas.drawCircle(0f, 0f, SKY_RADIUS, paint)
    }

    private fun getOrCreatePlate(projection: OrlojProjection): CachedPlate {
        val key = projection.plateKey
        val current = cachedPlate
        if (current != null && current.matches(key)) {
            return current
        }
        val newPlate = buildPlate(projection, key)
        cachedPlate = newPlate
        return newPlate
    }

    private fun buildPlate(projection: OrlojProjection, key: PlateKey): CachedPlate {
        val twilightPath = Path().apply { fillType = Path.FillType.EVEN_ODD }
        for (contour in projection.altitudeRegion(NIGHT_ALTITUDE)) {
            traceContour(twilightPath, contour)
            twilightPath.close()
        }

        val dayPath = Path().apply { fillType = Path.FillType.EVEN_ODD }
        for (contour in projection.altitudeRegion(HORIZON_ALTITUDE)) {
            traceContour(dayPath, contour)
            dayPath.close()
        }

        val nightBoundaryPath = Path()
        for (contour in projection.altitudeBoundary(NIGHT_ALTITUDE)) {
            traceContour(nightBoundaryPath, contour)
        }

        val horizonBoundaryPath = Path()
        for (contour in projection.altitudeBoundary(HORIZON_ALTITUDE)) {
            traceContour(horizonBoundaryPath, contour)
        }

        return CachedPlate(
            key = key,
            paths =
                PlatePaths(
                    twilightFill = twilightPath,
                    dayFill = dayPath,
                    nightBoundary = nightBoundaryPath,
                    horizonBoundary = horizonBoundaryPath,
                ),
        )
    }

    // The two radii come straight off the projection and cost nothing, so they are not cached; only
    // the boundaries need the plate, and they are drawn only when the Sun layer built it.
    private fun drawGrid(canvas: Canvas, projection: OrlojProjection, plate: CachedPlate?, palette: DialPalette) {
        paint.style = Paint.Style.STROKE
        // The two reference circles cross the sky, twilight, and night regions in one stroke, and no
        // single gold clears the graphics minimum against all three (bronze on the light palette's
        // night is 1.44:1, mutedGold on twilight 1.07:1 in the dark palette). A page-toned casing
        // underlines them on the surfaces the ink cannot reach and vanishes on the ones it can.
        paint.color = palette.casing
        paint.strokeWidth = GRID_CASING_WIDTH
        canvas.drawCircle(0f, 0f, projection.capricornRadius.toFloat(), paint)
        canvas.drawCircle(0f, 0f, projection.equatorRadius.toFloat(), paint)
        paint.color = palette.mutedGold
        paint.strokeWidth = GRID_WIDTH
        canvas.drawCircle(0f, 0f, projection.capricornRadius.toFloat(), paint)
        paint.color = palette.gold
        canvas.drawCircle(0f, 0f, projection.equatorRadius.toFloat(), paint)
        if (plate != null) {
            paint.color = palette.mutedGold
            paint.strokeWidth = BOUNDARY_WIDTH
            canvas.drawPath(plate.paths.nightBoundary, paint)
            paint.color = palette.gold
            canvas.drawPath(plate.paths.horizonBoundary, paint)
        }
    }

    private fun traceContour(path: Path, contour: List<DialPoint>) {
        if (contour.isEmpty()) return
        val first = contour[0]
        path.moveTo(first.x.toFloat(), first.y.toFloat())
        for (i in 1 until contour.size) {
            val point = contour[i]
            path.lineTo(point.x.toFloat(), point.y.toFloat())
        }
    }

    private data class PlatePaths(
        val twilightFill: Path,
        val dayFill: Path,
        val nightBoundary: Path,
        val horizonBoundary: Path,
    )

    private class CachedPlate(val key: PlateKey, val paths: PlatePaths) {
        fun matches(other: PlateKey): Boolean {
            val isLatitudeSame = abs(key.latitudeDeg - other.latitudeDeg) < EPSILON_LATITUDE
            val isObliquitySame = abs(key.trueObliquityDeg - other.trueObliquityDeg) < EPSILON_OBLIQUITY
            return isLatitudeSame && isObliquitySame
        }

        private companion object {
            const val EPSILON_LATITUDE = 1e-7
            const val EPSILON_OBLIQUITY = 1e-4
        }
    }

    private companion object {
        const val SKY_RADIUS = 1f
        const val NIGHT_ALTITUDE = -18.0
        const val HORIZON_ALTITUDE = 0.0
        const val GRID_WIDTH = 0.0035f
        const val GRID_CASING_WIDTH = 0.0095f
        const val BOUNDARY_WIDTH = 0.006f
        const val OUTER_WIDTH = 0.008f
    }
}
