package io.github.godaniya.astronomicalclockswallpaper

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Independent analytic fixtures for the north-pole construction documented at
 * https://astro.cas.cz/bh2010/files/praha.pdf, printed pages 4–5.
 * Unit-radius Cancer coordinates use an artificial 30-degree obliquity so the expected
 * values follow exact 30/60/90-degree triangles, independently of Astronomy Engine.
 */
class OrlojProjectionTest {
    @Test
    fun equinoxAndSolsticeFixtures() {
        val projection = projection(0.0)
        val equator = 1 / sqrt(3.0)
        assertEquals(equator, projection.equatorRadius, TOLERANCE)
        assertEquals(1.0 / 3, projection.capricornRadius, TOLERANCE)
        assertPoint(expected = DialPoint(x = 0.0, y = -equator), actual = projection.eclipticPoint(0.0))
        assertPoint(expected = DialPoint(x = -1.0, y = 0.0), actual = projection.eclipticPoint(90.0))
        assertPoint(expected = DialPoint(x = 0.0, y = equator), actual = projection.eclipticPoint(180.0))
        assertPoint(expected = DialPoint(x = 1.0 / 3, y = 0.0), actual = projection.eclipticPoint(270.0))
    }

    @Test
    fun completeZodiacIsTangent() {
        val projection = projection(37.0)
        val circle = projection.zodiacCircle
        val offset = hypot(x = circle.center.x, y = circle.center.y)
        assertEquals(1.0, offset + circle.radius, TOLERANCE)
        assertEquals(projection.capricornRadius, circle.radius - offset, TOLERANCE)
        for (longitude in 0 until 360 step 15) {
            val point = projection.eclipticPoint(longitude.toDouble())
            assertEquals(circle.radius, hypot(x = point.x - circle.center.x, y = point.y - circle.center.y), TOLERANCE)
        }
    }

    @Test
    fun siderealRotationIsClockwise() {
        val initial = projection(0.0)
        val rotated = projection(90.0)
        for (longitude in 0 until 360 step 30) {
            val point = initial.eclipticPoint(longitude.toDouble())
            assertPoint(
                expected = DialPoint(x = -point.y, y = point.x),
                actual =
                    rotated
                        .eclipticPoint(longitude.toDouble()),
            )
        }
        val point = initial.zodiacCircle.center
        assertPoint(expected = DialPoint(x = -point.y, y = point.x), actual = rotated.zodiacCircle.center)
    }

    @Test
    fun fullRingIncludesBelowHorizon() {
        val projection = projection(0.0)
        val signs = (0 until 360 step 30).map { projection.eclipticPoint(it.toDouble()) }
        assertTrue(signs.any { projection.altitudeDeg(it) < 0 })
        assertTrue(signs.any { projection.altitudeDeg(it) > 0 })
        assertEquals(12, signs.size)
    }

    @Test
    fun southernRingIsPointReflected() {
        val north = projection(sidereal = 37.0, latitude = 50.0)
        val south = projection(sidereal = 37.0, latitude = -50.0)
        assertPoint(
            expected = DialPoint(x = -north.zodiacCircle.center.x, y = -north.zodiacCircle.center.y),
            actual = south.zodiacCircle.center,
        )
        assertEquals(north.zodiacCircle.radius, south.zodiacCircle.radius, TOLERANCE)
        // The south-pole projection is the radial inversion of the north one through the equator
        // circle. That inversion maps the ecliptic ring to its point reflection and carries each
        // ecliptic longitude to the reflection of the antipodal one. This pins the handedness.
        for (longitude in 0 until 360 step 15) {
            val reflected = north.eclipticPoint((longitude + 180).toDouble())
            assertPoint(
                expected = DialPoint(x = -reflected.x, y = -reflected.y),
                actual = south.eclipticPoint(longitude.toDouble()),
            )
        }
    }

    @Test
    fun southernTropicsAreSwapped() {
        // At a 30-degree obliquity the equator radius is 1/sqrt(3) and the inner tropic 1/3.
        // On a south plate Capricorn (declination -obliquity) is the outer sky boundary and
        // Cancer (declination +obliquity) the inner one, the reverse of the Prague plate.
        val southern = projection(sidereal = 0.0, latitude = -50.0)
        assertPoint(expected = DialPoint(x = 1.0, y = 0.0), actual = southern.eclipticPoint(270.0))
        assertPoint(expected = DialPoint(x = -1.0 / 3, y = 0.0), actual = southern.eclipticPoint(90.0))
        // The equinoxes still lie on the equator circle, unchanged between the plates.
        assertPoint(expected = DialPoint(x = 0.0, y = -1 / sqrt(3.0)), actual = southern.eclipticPoint(0.0))
        assertPoint(expected = DialPoint(x = 0.0, y = 1 / sqrt(3.0)), actual = southern.eclipticPoint(180.0))
    }

    @Test
    fun sunBearingEqualsHourAngle() {
        // For any point on the ecliptic ring, its bearing clockwise from the top vertical (XII)
        // equals its hour angle H = local apparent sidereal angle - right ascension.
        for (latitude in listOf(50.0, -50.0)) {
            val projection =
                OrlojProjection(
                    DialGeometry(
                        localSiderealAngleDeg = 45.0,
                        trueObliquityDeg = 23.44,
                        latitudeDeg = latitude,
                    ),
                )
            for (longitude in 0 until 360 step 15) {
                val point = projection.eclipticPoint(longitude.toDouble())
                val bearingDeg = Math.toDegrees(atan2(y = point.x, x = -point.y)).mod(360.0)
                val lonRad = Math.toRadians(longitude.toDouble())
                val oblRad = Math.toRadians(23.44)
                val eqX = cos(lonRad)
                val eqY = sin(lonRad) * cos(oblRad)
                val raDeg = Math.toDegrees(atan2(y = eqY, x = eqX)).mod(360.0)
                val expectedHourAngle = (45.0 - raDeg).mod(360.0)
                assertEquals(expectedHourAngle, bearingDeg, TOLERANCE_BEARING)
            }
        }
    }

    private fun projection(sidereal: Double, latitude: Double = 50.0): OrlojProjection {
        val geometry =
            DialGeometry(localSiderealAngleDeg = sidereal, trueObliquityDeg = 30.0, latitudeDeg = latitude)
        return OrlojProjection(geometry)
    }

    private fun assertPoint(expected: DialPoint, actual: DialPoint) {
        assertEquals(expected.x, actual.x, TOLERANCE)
        assertEquals(expected.y, actual.y, TOLERANCE)
    }

    private companion object {
        const val TOLERANCE = 1e-12
        const val TOLERANCE_BEARING = 1e-10
    }
}
