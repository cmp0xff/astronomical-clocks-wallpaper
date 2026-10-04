package io.github.godaniya.astronomicalclockswallpaper

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset
import kotlin.math.abs

/** Checks the reference frame against independent SOFA-derived angles, not engine-generated data. */
class DialGeometryTest {
    private val calculator: AstronomyCalculator = AstronomyEngineCalculator()

    @Test
    fun anglesMatchErfaAcrossSeasons() {
        assertEquals(
            "fixtures must span all four seasonal quarters",
            SEASON_COUNT,
            geometryFixtures.map { (it.instant.atZone(ZoneOffset.UTC).monthValue - 1) / 3 }.toSet().size,
        )
        for (fixture in geometryFixtures) {
            for (longitude in LONGITUDES) {
                val site = location(latitude = 50.0, longitude = longitude)
                val geometry = calculator.dialGeometry(fixture.instant, site)
                val expected = (fixture.greenwichSiderealAngleDeg + longitude).mod(FULL_TURN_DEGREES)
                val difference = angleDifferenceDeg(first = geometry.localSiderealAngleDeg, second = expected)
                assertTrue(
                    "sidereal angle at ${fixture.instant}, longitude $longitude",
                    abs(difference) <= SIDEREAL_TOLERANCE_DEG,
                )
                assertEquals(
                    "true obliquity at ${fixture.instant}",
                    fixture.trueObliquityDeg,
                    geometry.trueObliquityDeg,
                    OBLIQUITY_TOLERANCE_DEG,
                )
                assertTrue(geometry.localSiderealAngleDeg >= 0.0 && geometry.localSiderealAngleDeg < FULL_TURN_DEGREES)
                val sun = geometry.sunLongitudeDeg ?: error("no Sun longitude at ${fixture.instant}")
                val sunDifference = angleDifferenceDeg(first = sun, second = fixture.sunLongitudeDeg)
                assertTrue(
                    "Sun longitude at ${fixture.instant} differs by ${sunDifference * SECONDS_PER_DEGREE} arcsec",
                    abs(sunDifference) <= SUN_TOLERANCE_DEG,
                )
                val moon = geometry.moonLongitudeDeg ?: error("no Moon longitude at ${fixture.instant}")
                val moonDifference = angleDifferenceDeg(first = moon, second = fixture.moonLongitudeDeg)
                assertTrue(
                    "Moon longitude at ${fixture.instant} differs by ${moonDifference * SECONDS_PER_DEGREE} arcsec",
                    abs(moonDifference) <= MOON_LONGITUDE_TOLERANCE_DEG,
                )
                val moonPhase = geometry.moonPhaseLongitudeDeg ?: error("no Moon phase at ${fixture.instant}")
                val phaseDifference = angleDifferenceDeg(first = moonPhase, second = fixture.moonPhaseLongitudeDeg)
                assertTrue(
                    "Moon phase at ${fixture.instant} differs by ${phaseDifference * SECONDS_PER_DEGREE} arcsec",
                    abs(phaseDifference) <= MOON_PHASE_TOLERANCE_DEG,
                )
            }
        }
    }

    @Test
    fun dateBoundaryKeepsSiderealRate() {
        val before = calculator.dialGeometry(Instant.parse("2026-03-20T23:59:59Z"), location())
        val after = calculator.dialGeometry(Instant.parse("2026-03-21T00:00:00Z"), location())
        // A sidereal day is about 86164.1 SI seconds; a UTC date change does not reset the dial.
        val advance = angleDifferenceDeg(first = after.localSiderealAngleDeg, second = before.localSiderealAngleDeg)
        assertEquals(0.00417807434, advance, RATE_TOLERANCE_DEG)
    }

    @Test
    fun coordinatesIgnoreSavedZone() {
        val instant = Instant.parse("2026-06-21T00:00:00Z")
        val west = location().copy(zoneId = ZoneId.of("Pacific/Honolulu"))
        val east = west.copy(zoneId = ZoneId.of("Pacific/Kiritimati"))
        assertEquals(calculator.dialGeometry(instant, west), calculator.dialGeometry(instant, east))
    }

    @Test
    fun latitudeIncludesBothPoles() {
        val instant = Instant.parse("2026-09-23T00:05:00Z")
        val reference = calculator.dialGeometry(instant, location())
        for (latitude in listOf(-90.0, -33.87, 0.0, 50.0, 90.0)) {
            val geometry = calculator.dialGeometry(instant, location(latitude = latitude))
            // The comparison covers the Sun and Moon longitudes as well as the angles: those are
            // geocentric, so a southern or polar site must carry exactly the same lunar and solar
            // values as a northern one and only differ in the latitude it records.
            assertEquals(reference.copy(latitudeDeg = latitude), geometry)
        }
    }

    @Test
    fun geometryRejectsInvalidAngles() {
        val valid = DialGeometry(localSiderealAngleDeg = 10.0, trueObliquityDeg = 23.4, latitudeDeg = 50.0)
        for (angle in listOf(-0.1, 360.0, Double.NaN, Double.POSITIVE_INFINITY)) {
            assertRejected { valid.copy(localSiderealAngleDeg = angle) }
        }
        for (angle in listOf(0.0, 90.0, Double.NaN, Double.NEGATIVE_INFINITY)) {
            assertRejected { valid.copy(trueObliquityDeg = angle) }
        }
        for (latitude in listOf(-90.1, 90.1, Double.NaN, Double.POSITIVE_INFINITY)) {
            assertRejected { valid.copy(latitudeDeg = latitude) }
        }
        for (angle in listOf(-0.1, 360.0, Double.NaN, Double.POSITIVE_INFINITY)) {
            assertRejected { valid.copy(sunLongitudeDeg = angle) }
        }
        for (angle in listOf(-0.1, 360.0, Double.NaN, Double.POSITIVE_INFINITY)) {
            assertRejected { valid.copy(moonLongitudeDeg = angle) }
            assertRejected { valid.copy(moonPhaseLongitudeDeg = angle) }
        }
        // An uncalculated Sun or Moon is a supported state, not an error: it is what suppresses
        // the marker for a geometry that carries no site, and it must not trip the range check.
        assertNull(valid.sunLongitudeDeg)
        assertNull(valid.copy(sunLongitudeDeg = null).sunLongitudeDeg)
        assertNull(valid.moonLongitudeDeg)
        assertNull(valid.moonPhaseLongitudeDeg)
    }

    private fun assertRejected(create: () -> DialGeometry) {
        val failure = runCatching(create).exceptionOrNull()
        assertTrue(
            "expected IllegalArgumentException, found ${failure ?: "no exception"}",
            failure is IllegalArgumentException,
        )
    }

    private fun location(latitude: Double = 50.0, longitude: Double = 14.42): ObservingLocation =
        SITE.copy(latitude = latitude, longitude = longitude)

    private companion object {
        val SITE =
            ObservingLocation(
                latitude = 50.0,
                longitude = 14.42,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneOffset.UTC,
            )
        val LONGITUDES = listOf(-180.0, -79.0, 0.0, 14.42, 151.21, 180.0)
        const val SEASON_COUNT = 4
        const val SIDEREAL_TOLERANCE_DEG = 0.0001
        const val OBLIQUITY_TOLERANCE_DEG = 0.00003

        // The engine's largest residual against the ERFA Sun column is 0.000434 degree
        // (1.56 arcseconds); see DialGeometryFixture.kt for how this bound was chosen.
        const val SUN_TOLERANCE_DEG = 0.001

        // The engine's largest residual against the ERFA Moon columns is 0.0016397 degree
        // (5.90 arcseconds) in longitude and 0.0061575 degree (22.17 arcseconds) in phase
        // longitude. Both bounds are set from those measurements, not from the Sun's: see
        // DialGeometryFixture.kt for the two independent lunar theories and the engine
        // `moonPhase` offset behind them. Even so they sit far below the ~5.1 degree ecliptic
        // latitude the marker drops and far below any frame, epoch, or quadrant error.
        const val MOON_LONGITUDE_TOLERANCE_DEG = 0.003
        const val MOON_PHASE_TOLERANCE_DEG = 0.01
        const val SECONDS_PER_DEGREE = 3600.0
        const val RATE_TOLERANCE_DEG = 0.00000001
    }
}
