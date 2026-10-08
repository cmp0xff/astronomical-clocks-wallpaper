package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Bitmap
import android.graphics.Canvas

/**
 * A magnified zodiac-ring render centred on the 0° Aries equinox, shared by the star-orientation
 * pixel probes and the representative image exports so the Canvas transform and its field of view
 * live in one place. The bitmap is square, so one sky radius spans [scaleFor] pixels and the whole
 * square covers [FIELD_OF_VIEW_SKY_RADII] sky radii across, centred on the equinox.
 */
internal object EquinoxStarDetail {
    /** Side of the rendered field of view in sky radii; the star's 0.04-wide span fills half of it. */
    const val FIELD_OF_VIEW_SKY_RADII = 0.08

    /** Pixels per sky radius for a square bitmap [sizePx] pixels on a side. */
    fun scaleFor(sizePx: Int): Float = (sizePx / FIELD_OF_VIEW_SKY_RADII).toFloat()

    /** Renders the zodiac ring, dividers, and equinox star centred on the equinox point. */
    fun render(geometry: DialGeometry, sizePx: Int): Bitmap {
        val projection = OrlojProjection(geometry)
        val point = projection.eclipticPoint(0.0)
        val bitmap = Bitmap.createBitmap(sizePx, sizePx, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(DialStyle.BACKGROUND)
        canvas.translate(sizePx / 2f, sizePx / 2f)
        canvas.scale(scaleFor(sizePx), scaleFor(sizePx))
        canvas.translate(-point.x.toFloat(), -point.y.toFloat())
        ZodiacRenderer().draw(canvas, projection)
        return bitmap
    }
}
