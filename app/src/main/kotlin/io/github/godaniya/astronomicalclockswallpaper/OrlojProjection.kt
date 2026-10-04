package io.github.godaniya.astronomicalclockswallpaper

import kotlin.math.abs
import kotlin.math.acos
import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.tan

/** Canvas-oriented coordinates, normalized to the outer sky boundary (Cancer north, Capricorn south). */
internal data class DialPoint(val x: Double, val y: Double)

/** An exact projected circle in normalized dial coordinates. */
internal data class DialCircle(val center: DialPoint, val radius: Double)

/**
 * The time-invariant inputs the static plate geometry depends on. Latitude is fixed for a site, but
 * true obliquity drifts with the instant, so the plate cache compares keys with a tolerance instead
 * of by exact equality; see [OrlojPlateRenderer].
 */
internal data class PlateKey(val latitudeDeg: Double, val trueObliquityDeg: Double)

/**
 * Stereographic projection of the sky onto the dial plane, taken from the celestial pole above the
 * horizon: the north pole for a northern site, the south pole for a southern one. Angles use the
 * true equator/ecliptic of date; the reference plate is geometric and unrefracted.
 *
 * On a northern plate Cancer (declination +obliquity) is the outer sky boundary, Capricorn the
 * inner tropic, and the dial centre is the south celestial pole at altitude -latitude. A southern
 * plate is the same construction viewed from the other pole: Capricorn becomes the outer boundary
 * and Cancer the inner tropic, and the centre is the north celestial pole, still at altitude
 * -|latitude|. Because the centre is always the pole below the horizon, the night disc nests inside
 * the twilight disc in both hemispheres.
 *
 * The drawn circles and the altitude regions at latitude -|latitude| are numerically identical to
 * those at +|latitude|; only the sky content moves, radially inverted through the equator circle.
 * The zodiac ring is therefore point-reflected through the dial centre and jumps by 180 degrees as
 * a site crosses the equator, while the shading does not.
 *
 * See https://astro.cas.cz/bh2010/files/praha.pdf, printed pages 4–5, for the north-pole plate.
 */
internal class OrlojProjection(private val geometry: DialGeometry) {
    /** Identifies the static plate geometry this projection would produce. */
    val plateKey: PlateKey =
        PlateKey(
            latitudeDeg = geometry.latitudeDeg,
            trueObliquityDeg = geometry.trueObliquityDeg,
        )
    private val obliquityRad = Math.toRadians(geometry.trueObliquityDeg)
    private val siderealRad = Math.toRadians(geometry.localSiderealAngleDeg)

    /** Whether the plate is drawn for a southern site, which inverts the sky content radially. */
    val isSouthern: Boolean = geometry.latitudeDeg < 0
    private val zodiacCenterSign = if (isSouthern) 1.0 else -1.0
    private val latitudeRad = Math.toRadians(abs(geometry.latitudeDeg))
    private val cancerRadius = tan(QUARTER_TURN_RAD / 2 + obliquityRad / 2)

    val equatorRadius: Double = 1 / cancerRadius
    val capricornRadius: Double = tan(QUARTER_TURN_RAD / 2 - obliquityRad / 2) / cancerRadius
    val zodiacCircle: DialCircle =
        DialCircle(
            center =
                DialPoint(
                    x = zodiacCenterSign * (1 - capricornRadius) * cos(siderealRad) / 2,
                    y = zodiacCenterSign * (1 - capricornRadius) * sin(siderealRad) / 2,
                ),
            radius = (1 + capricornRadius) / 2,
        )

    /** The Sun's projected position, or `null` when no longitude has been calculated. */
    val sunPoint: DialPoint? get() = geometry.sunLongitudeDeg?.let(::eclipticPoint)

    /** The Moon's projected position, or `null` when no longitude has been calculated. */
    val moonPoint: DialPoint? get() = geometry.moonLongitudeDeg?.let(::eclipticPoint)

    /**
     * Projects a body at ecliptic [longitudeDeg] onto the dial's ecliptic ring.
     *
     * Ecliptic latitude is deliberately ignored. The Sun's is under an arcminute, while the Moon's
     * reaches about 5.1 degrees, but on the reference instrument both pointers ride the ecliptic
     * ring, so the ring — not the body's true place — is what the dial shows. A body's angle around
     * the ring is therefore exact, and its displacement off the ring is a convention.
     */
    fun eclipticPoint(longitudeDeg: Double): DialPoint {
        val longitude = Math.toRadians(longitudeDeg)
        val equatorialX = cos(longitude)
        val equatorialY = sin(longitude) * cos(obliquityRad)
        val equatorialZ = sin(longitude) * sin(obliquityRad)
        val polarDistance = if (isSouthern) 1 + equatorialZ else 1 - equatorialZ
        val scale = equatorRadius / polarDistance
        return DialPoint(
            x = (sin(siderealRad) * equatorialX - cos(siderealRad) * equatorialY) * scale,
            y = -(cos(siderealRad) * equatorialX + sin(siderealRad) * equatorialY) * scale,
        )
    }

    /** Inverse projection followed by the geometric altitude equation, with no horizon singularity. */
    fun altitudeDeg(point: DialPoint): Double {
        val rawX = point.x * cancerRadius
        val rawY = point.y * cancerRadius
        val radiusSquared = rawX * rawX + rawY * rawY
        val sine = (sin(latitudeRad) * (radiusSquared - 1) - 2 * cos(latitudeRad) * rawY) / (radiusSquared + 1)
        return Math.toDegrees(asin(sine.coerceIn(minimumValue = -1.0, maximumValue = 1.0)))
    }

    fun isAboveAltitude(point: DialPoint, altitudeDeg: Double): Boolean = this.altitudeDeg(point) >= altitudeDeg

    /** Clipped polylines; even a nearly straight horizon never produces huge drawing coordinates. */
    fun altitudeBoundary(altitudeDeg: Double): List<List<DialPoint>> =
        AltitudeContour(this, geometry, altitudeDeg).boundary()

    /** Closed fill contours inside the sky disk. Use an even-odd fill rule to preserve polar holes. */
    fun altitudeRegion(minAltitudeDeg: Double): List<List<DialPoint>> =
        AltitudeContour(this, geometry, minAltitudeDeg).region()

    private companion object {
        const val QUARTER_TURN_RAD = Math.PI / 2
    }
}

/**
 * Samples an altitude circle on the sphere before projection, keeping its finite visible arc only.
 * Azimuth steps are at most one degree. At terrestrial obliquities below 24 degrees, the bounded
 * projection's second derivative is less than 13, so chord error is below 0.25 pixels at a
 * 500-pixel sky radius: 13 * radians(1)^2 / 8 * 500. What a plate fills is this sampled polygon;
 * deciding which side of a threshold a point lies on stays analytic in [OrlojProjection.isAboveAltitude].
 */
private class AltitudeContour(
    private val projection: OrlojProjection,
    geometry: DialGeometry,
    private val altitudeDeg: Double,
) {
    init {
        require(altitudeDeg in -RIGHT_ANGLE_DEGREES..RIGHT_ANGLE_DEGREES) { "altitude must be in [-90, 90]" }
    }

    private val altitudeRad = Math.toRadians(altitudeDeg)

    // Regions depend only on |latitude|: a south-pole plate at -phi is the mirror of a north-pole
    // plate at +|phi|, so sampling the same sky circles reproduces the same drawn contours.
    private val latitudeRad = Math.toRadians(abs(geometry.latitudeDeg))
    private val capDeclination = sin(Math.toRadians(geometry.trueObliquityDeg))
    private val centerDeclination = sin(altitudeRad) * sin(latitudeRad)
    private val declinationAmplitude = cos(altitudeRad) * cos(latitudeRad)
    private val startAzimuth = visibleArcStart()
    private val arc = visibleArc()

    private fun visibleArc(): List<DialPoint> {
        val start = startAzimuth ?: return emptyList()
        return sampleArc(start = start, sweep = FULL_TURN_RAD - 2 * start, pointAt = ::projectAzimuth)
    }

    private fun visibleArcStart(): Double? {
        val minimumDeclination = centerDeclination - declinationAmplitude
        val maximumDeclination = centerDeclination + declinationAmplitude
        return when {
            declinationAmplitude < ROUNDING_TOLERANCE -> {
                if (centerDeclination <= capDeclination) 0.0 else null
            }

            capDeclination >= maximumDeclination -> {
                0.0
            }

            capDeclination < minimumDeclination -> {
                null
            }

            else -> {
                val cosine = (capDeclination - centerDeclination) / declinationAmplitude
                acos(cosine.coerceIn(minimumValue = -1.0, maximumValue = 1.0))
            }
        }
    }

    private fun projectAzimuth(azimuth: Double): DialPoint {
        val equatorialX = -cos(altitudeRad) * sin(azimuth)
        val equatorialY = sin(altitudeRad) * cos(latitudeRad) - cos(altitudeRad) * cos(azimuth) * sin(latitudeRad)
        val equatorialZ = centerDeclination + declinationAmplitude * cos(azimuth)
        val scale = projection.equatorRadius / (1 - equatorialZ)
        return DialPoint(x = equatorialX * scale, y = -equatorialY * scale)
    }

    fun boundary(): List<List<DialPoint>> = if (arc.isEmpty()) emptyList() else listOf(arc)

    fun region(): List<List<DialPoint>> {
        val isCenterAbove = projection.isAboveAltitude(DialPoint(x = 0.0, y = 0.0), altitudeDeg)
        val isEdgeAbove = projection.isAboveAltitude(DialPoint(x = 1.0, y = 0.0), altitudeDeg)
        return when {
            arc.isEmpty() -> {
                if (isCenterAbove) listOf(skyCircle()) else emptyList()
            }

            startAzimuth == 0.0 -> {
                if (isEdgeAbove) listOf(skyCircle(), arc) else listOf(arc)
            }

            else -> {
                listOf(arc + closingSkyArc())
            }
        }
    }

    private fun closingSkyArc(): List<DialPoint> {
        val endAngle = atan2(y = arc.last().y, x = arc.last().x)
        val startAngle = atan2(y = arc.first().y, x = arc.first().x)
        val positiveSweep = (startAngle - endAngle + FULL_TURN_RAD) % FULL_TURN_RAD
        val midpoint = skyPoint(endAngle + positiveSweep / 2)
        val isMidpointAbove = projection.isAboveAltitude(midpoint, altitudeDeg)
        val sweep = if (isMidpointAbove) positiveSweep else positiveSweep - FULL_TURN_RAD
        return sampleArc(start = endAngle, sweep = sweep, pointAt = ::skyPoint)
    }

    private fun skyCircle(): List<DialPoint> = sampleArc(start = 0.0, sweep = FULL_TURN_RAD, pointAt = ::skyPoint)

    private fun skyPoint(angle: Double): DialPoint = DialPoint(x = cos(angle), y = sin(angle))

    private fun sampleArc(start: Double, sweep: Double, pointAt: (Double) -> DialPoint): List<DialPoint> =
        (0..ARC_SEGMENTS).map { step -> pointAt(start + sweep * step / ARC_SEGMENTS) }

    private companion object {
        const val FULL_TURN_RAD = 2 * Math.PI
        const val ARC_SEGMENTS = 360
        const val ROUNDING_TOLERANCE = 1e-14
    }
}
