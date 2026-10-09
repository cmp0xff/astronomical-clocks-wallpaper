package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.graphics.Color
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

    // A degenerate or undersized surface lasts until the framework recreates it, which can span
    // many ticks, so both skips below are bounded like the other per-frame failure sites.
    private val emptyCanvasLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping render: empty canvas",
            level = Log.WARN,
        )
    private val undersizedDialLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping dial: viewport cannot fit the dial",
            level = Log.WARN,
        )

    /** Each frame uses civil time and geometric values from the caller's single instant. */
    fun renderDial(
        canvas: Canvas,
        state: ClockState,
        geometry: DialGeometry? = null,
        layers: DialLayers = DialLayers(),
        palette: DialPalette = DialStyle.DARK_PALETTE,
    ) = renderDisplay(
        canvas = canvas,
        state = state,
        geometry = geometry,
        layers = layers,
        style =
            DialRenderStyle(
                palette = palette,
                display = DialDisplaySettings(),
                viewport = DialViewport.full(width = canvas.width, height = canvas.height),
            ),
    )

    fun renderDisplay(
        canvas: Canvas,
        state: ClockState,
        geometry: DialGeometry?,
        layers: DialLayers,
        style: DialRenderStyle,
    ) {
        val palette = style.palette
        val display = style.display
        val viewport = style.viewport
        if (canvas.width <= 0 || canvas.height <= 0) {
            emptyCanvasLog.recordFailure(detail = "${canvas.width}x${canvas.height}")
            return
        }
        canvas.drawColor(palette.background)
        val placement = viewport.resolve(display)
        if (placement == null) {
            undersizedDialLog.recordFailure(detail = "unusable viewport=$viewport display=$display")
            applyBrightness(canvas, display.brightness)
            return
        }
        val radius = placement.radius
        val checkpoint = canvas.save()
        try {
            canvas.translate(placement.centerX, placement.centerY)
            canvas.scale(radius / OUTER_RADIUS, radius / OUTER_RADIUS)
            drawCivilScale(canvas, palette)
            val projection = geometry?.let(::OrlojProjection)
            plate.draw(
                canvas = canvas,
                projection = projection,
                isSunEnabled = layers.isSunEnabled,
                palette = palette,
            )
            if (projection != null && layers.isZodiacRingEnabled) {
                zodiac.draw(canvas, projection, palette)
            }
            val sunPoint = projection?.sunPoint
            if (sunPoint != null && layers.isSunEnabled) {
                sun.draw(
                    canvas = canvas,
                    point = sunPoint,
                    palette = palette,
                    isZodiacBandVisible = layers.isZodiacRingEnabled,
                )
            }
            drawMoon(
                canvas = canvas,
                projection = projection,
                phase = geometry?.moonPhaseLongitudeDeg,
                layers = layers,
                palette = palette,
            )
            drawCivilHand(canvas, state.hourAngle, palette)
        } finally {
            canvas.restoreToCount(checkpoint)
        }
        applyBrightness(canvas, display.brightness)
        // Only a frame that ran to completion ends the episode, so a throw leaves both counters alone.
        emptyCanvasLog.recordSuccess()
        undersizedDialLog.recordSuccess()
    }

    private fun applyBrightness(canvas: Canvas, brightness: Int) {
        if (brightness < FULL_BRIGHTNESS) {
            canvas
                .drawColor(
                    Color
                        .argb(((FULL_BRIGHTNESS - brightness) * MAX_ALPHA / FULL_BRIGHTNESS).toInt(), 0, 0, 0),
                )
        }
    }

    private fun drawMoon(
        canvas: Canvas,
        projection: OrlojProjection?,
        phase: Double?,
        layers: DialLayers,
        palette: DialPalette,
    ) {
        // The marker needs both a projected position and a phase, so like the Sun it is drawn
        // only when the geometry carries them; the caller owns that suppression.
        if (projection != null && layers.isMoonEnabled) {
            val moonPoint = projection.moonPoint
            val moonPhase = phase
            if (moonPoint != null && moonPhase != null) {
                moon.draw(
                    canvas = canvas,
                    point = moonPoint,
                    phase = MoonDiscPhase(longitudeDeg = moonPhase, isSouthern = projection.isSouthern),
                    palette = palette,
                    isZodiacBandVisible = layers.isZodiacRingEnabled,
                )
            }
        }
    }

    private fun drawCivilScale(canvas: Canvas, palette: DialPalette) {
        paint.style = Paint.Style.FILL
        paint.color = palette.rim
        canvas.drawCircle(0f, 0f, OUTER_RADIUS, paint)
        paint.style = Paint.Style.STROKE
        paint.color = palette.gold
        paint.strokeWidth = RIM_WIDTH
        canvas.drawCircle(0f, 0f, OUTER_RADIUS, paint)
        canvas.drawCircle(0f, 0f, SCALE_INNER_RADIUS, paint)
        paint.color = palette.mutedGold
        paint.strokeWidth = FINE_WIDTH
        canvas.drawCircle(0f, 0f, OUTER_RADIUS - RIM_INSET, paint)
        drawHours(canvas, palette)
    }

    private fun drawHours(canvas: Canvas, palette: DialPalette) {
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
            paint.color = palette.gold
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

    private fun drawCivilHand(canvas: Canvas, angleDegrees: Float, palette: DialPalette) {
        val checkpoint = canvas.save()
        canvas.rotate(angleDegrees)
        // The hand crosses both the pale plate and the dark night region, and no single ink clears the
        // graphics minimum against both (bronze on night measured 1.04:1). The page-toned casing
        // underlay outlines it over the dark region and disappears over the pale one.
        paint.color = palette.casing
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = HAND_CASING_WIDTH
        canvas.drawPath(hand, paint)
        paint.color = palette.hand
        paint.style = Paint.Style.FILL
        canvas.drawPath(hand, paint)
        canvas.restoreToCount(checkpoint)
        paint.color = palette.gold
        canvas.drawCircle(0f, 0f, HUB_RADIUS, paint)
        paint.color = palette.rim
        canvas.drawCircle(0f, 0f, HUB_INNER_RADIUS, paint)
    }

    internal companion object {
        const val CENTER_DIVISOR = 2f
        const val FULL_BRIGHTNESS = 100
        const val MAX_ALPHA = 255f
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

        // A stroke is centred on the path outline, so half of it widens the blade; 0.010 leaves a rim of
        // about 1.7 px at the 1080 px wallpaper size on each side of the 7.5 px shaft.
        const val HAND_CASING_WIDTH = 0.010f
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

/** Contains argument and canvas-state failures while bounding repeated per-frame error logs. */
internal class RenderFailureContainment {
    private val renderArgumentLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping frame: invalid render argument",
            level = Log.ERROR,
        )
    private val renderStateLog =
        RepeatedFailureLog(
            tag = TAG,
            message = "skipping frame: canvas in an invalid state",
            level = Log.ERROR,
        )

    fun containRenderFailure(draw: () -> Unit) {
        try {
            draw()
            renderArgumentLog.recordSuccess()
            renderStateLog.recordSuccess()
        } catch (e: IllegalArgumentException) {
            renderArgumentLog.recordFailure(e, detail = e.message.orEmpty())
        } catch (e: IllegalStateException) {
            renderStateLog.recordFailure(e, detail = e.message.orEmpty())
        }
    }
}

private const val TAG = "DialRenderer"
