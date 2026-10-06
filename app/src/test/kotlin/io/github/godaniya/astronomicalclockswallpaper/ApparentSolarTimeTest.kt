package io.github.godaniya.astronomicalclockswallpaper

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import kotlin.math.abs
import kotlin.math.atan2

/**
 * Validates the relationship between the zoned civil hand and local apparent solar time (#57).
 *
 * Checks:
 * 1. At independent local apparent noon instants, the Sun marker's bearing is XII (0.0°) within
 *    astronomical tolerances, while the civil hand reflects the site's civil time and documented offset.
 * 2. An hour away from those instants the bearing advances at the apparent solar rate, which is what
 *    makes the transit bearing a reading of apparent solar time rather than a coordinate coincidence.
 * 3. Northern and southern sites, under both DST and non-DST conditions, are verified.
 * 4. Dial shading (day, twilight, night regions) and zodiac sidereal rotation are strictly zone-independent.
 * 5. The fixture set covers a northern and a southern site under both DST and non-DST conditions.
 *
 * The offset check guards the fixture constants and the JDK zone rules, not the engine's ephemeris.
 * It re-derives the documented offset `civil − apparent = UTC offset − east longitude/15° − equation
 * of time` from each fixture's own transit instant, longitude, and equation of time, and requires it to
 * reproduce that fixture's hand angle. Because the fixture's equation of time and hand angle were
 * themselves derived from the same transit instant, substituting one into the other is an algebraic
 * identity that catches a mistyped fixture constant or a change in the JDK timezone database, and
 * nothing more. The engine's bearing is what checks 1 and 2 measure instead.
 */
class ApparentSolarTimeTest {
    private val calculator = AstronomyEngineCalculator()

    @Test
    fun fixturesCoverSitesAndDst() {
        // Issue #57 requires a northern and a southern site under both DST and non-DST
        // conditions. Two hemispheres crossed with two DST states is exactly four
        // combinations, so a full set here keeps a dropped or relabelled fixture from
        // silently narrowing what the other tests exercise.
        //
        // The equator (latitude 0°) belongs to neither hemisphere, and `> 0` would file it
        // under southern, so an equatorial fixture could stand in for the southern one and
        // still complete the square. Reject it explicitly; then every fixture is strictly
        // northern or strictly southern and a count of four does mean the whole matrix.
        assertTrue(
            "no solar-noon fixture may sit on the equator: latitude 0° is neither hemisphere",
            apparentSolarTimeFixtures.none { it.latitudeDeg == 0.0 },
        )
        assertEquals(
            "fixtures must cover both hemispheres under both DST and non-DST conditions",
            HEMISPHERE_DST_COMBINATIONS,
            apparentSolarTimeFixtures.map { (it.latitudeDeg > 0) to it.isDst }.toSet().size,
        )
    }

    @Test
    fun apparentNoonSunBearingAtXii() {
        for (fixture in apparentSolarTimeFixtures) {
            assertDstMatchesZoneRules(fixture)
            val location = fixture.location
            val sunBearing = sunBearingDeg(fixture, fixture.instant)
            val deviationFromXii =
                if (sunBearing > HALF_TURN_DEGREES) {
                    FULL_TURN_DEGREES - sunBearing
                } else {
                    sunBearing
                }
            assertTrue(
                "Sun bearing at local apparent noon must point to XII (0.0°) within tolerance for ${fixture.siteName}",
                deviationFromXii < SOLAR_NOON_TOLERANCE_DEG,
            )

            // Verify civil time and hand angle
            val localTime = fixture.instant.atZone(location.zoneId).toLocalTime()
            assertEquals("Civil time must match fixture for ${fixture.siteName}", fixture.expectedCivilTime, localTime)

            val handAngle = clockState(localTime).hourAngle.toDouble()
            assertEquals(
                "Civil hand angle must match expected angle for ${fixture.siteName}",
                fixture.expectedHandAngleDeg,
                handAngle,
                CIVIL_HAND_TOLERANCE_DEG,
            )

            assertFixtureOffsetConsistent(fixture)
        }
    }

    /**
     * The bearing's hourly advance, which is degenerate at transit and so needs a second instant.
     *
     * Local apparent solar time is defined by the Sun's hour angle, which advances at 15°/h; over one
     * hour of UT the marker's bearing therefore advances by 15° plus the hour's change in the equation
     * of time. This exercises a different quantity from [apparentNoonSunBearingAtXii]: the bearing
     * there is `atan2(x, -y)` evaluated at one instant, while the advance also depends on how the
     * engine propagates sidereal angle, longitude, and the Sun's right ascension between instants.
     */
    @Test
    fun sunBearingAdvancesAtSolarRate() {
        for (fixture in apparentSolarTimeFixtures) {
            val bearingAtTransit = sunBearingDeg(fixture, fixture.instant)
            val bearingAnHourLater = sunBearingDeg(fixture, fixture.instant.plus(Duration.ofHours(1)))
            val hourlyAdvance = (bearingAnHourLater - bearingAtTransit).mod(FULL_TURN_DEGREES)
            assertTrue(
                "Sun bearing must advance by ${APPARENT_SOLAR_RATE_DEG_PER_HOUR}°/h for ${fixture.siteName}, " +
                    "measured $hourlyAdvance°",
                abs(hourlyAdvance - APPARENT_SOLAR_RATE_DEG_PER_HOUR) < HOURLY_RATE_TOLERANCE_DEG,
            )
        }
    }

    @Test
    fun shadingAndZodiacIgnoreZone() {
        // Varying the civil timezone for an observing site alters the civil hand angle
        // while leaving plate shading (day, twilight, night contours) and the zodiac ring's
        // sidereal orientation completely identical.
        val instant = Instant.parse("2026-06-21T11:04:07Z")
        val latitude = 50.0875
        val longitude = 14.4214
        val zones =
            listOf(
                ZoneId.of("Europe/Prague"),
                ZoneId.of("UTC"),
                ZoneId.of("America/New_York"),
                ZoneId.of("Asia/Tokyo"),
                ZoneId.of("Pacific/Honolulu"),
            )
        val geometries =
            zones.map { zone ->
                val location =
                    ObservingLocation(
                        latitude = latitude,
                        longitude = longitude,
                        source = ObservingLocation.Source.MANUAL,
                        zoneId = zone,
                    )
                calculator.dialGeometry(instant, location)
            }
        val projections = geometries.map { OrlojProjection(it) }

        val firstGeo = geometries.first()
        for (geo in geometries.drop(1)) {
            assertEquals(firstGeo.localSiderealAngleDeg, geo.localSiderealAngleDeg, 0.0)
            assertEquals(firstGeo.trueObliquityDeg, geo.trueObliquityDeg, 0.0)
            assertEquals(firstGeo.sunLongitudeDeg, geo.sunLongitudeDeg)
            assertEquals(firstGeo.moonLongitudeDeg, geo.moonLongitudeDeg)
            assertEquals(firstGeo.moonPhaseLongitudeDeg, geo.moonPhaseLongitudeDeg)
        }

        val firstProj = projections.first()
        for (proj in projections.drop(1)) {
            // Probe more than the two contours the plate draws (horizon at 0°, astronomical night at
            // -18°) so the zone-independence claim is not an artifact of those two altitudes alone.
            assertEquals(firstProj.altitudeRegion(0.0), proj.altitudeRegion(0.0))
            assertEquals(firstProj.altitudeRegion(-6.0), proj.altitudeRegion(-6.0))
            assertEquals(firstProj.altitudeRegion(-12.0), proj.altitudeRegion(-12.0))
            assertEquals(firstProj.altitudeRegion(-18.0), proj.altitudeRegion(-18.0))
            assertEquals(firstProj.zodiacCircle, proj.zodiacCircle)
            assertEquals(firstProj.sunPoint, proj.sunPoint)
        }

        // Civil hand angles must differ according to each timezone
        val handAngles = mutableSetOf<Float>()
        for (zone in zones) {
            val time = instant.atZone(zone).toLocalTime()
            handAngles.add(clockState(time).hourAngle)
        }
        assertEquals(
            "Distinct timezones must yield distinct civil hand angles",
            zones.size,
            handAngles.size,
        )
    }

    private fun assertDstMatchesZoneRules(fixture: ApparentSolarTimeFixture) {
        assertEquals(
            "DST flag must match the zone rules for ${fixture.siteName}",
            fixture.isDst,
            fixture.zoneId.rules.isDaylightSavings(fixture.instant),
        )
    }

    // Bearing in degrees clockwise from the negative y-axis (XII / top vertical axis). Projection
    // places a body at `(x, y) ∝ (sin H, -cos H)`, so `atan2(x, -y)` is the projection's own hour
    // angle; see docs/orloj.md for why that equals the body's apparent hour angle.
    private fun sunBearingDeg(fixture: ApparentSolarTimeFixture, instant: Instant): Double {
        val projection = OrlojProjection(calculator.dialGeometry(instant, fixture.location))
        val sunPoint = projection.sunPoint
        assertNotNull("Sun marker must be present in geometry for ${fixture.siteName}", sunPoint)
        return Math.toDegrees(atan2(y = sunPoint!!.x, x = -sunPoint.y)).mod(FULL_TURN_DEGREES)
    }

    // See the class KDoc: this is a fixture/zone-database consistency check, not an ephemeris check.
    private fun assertFixtureOffsetConsistent(fixture: ApparentSolarTimeFixture) {
        val utcOffsetHours =
            fixture.zoneId.rules
                .getOffset(fixture.instant)
                .totalSeconds /
                SECONDS_PER_HOUR
        val longitudeOffsetHours = fixture.longitudeDeg / DEGREES_PER_HOUR
        val equationOfTimeHours = fixture.expectedEquationOfTimeMinutes / MINUTES_PER_HOUR
        val expectedOffset = (utcOffsetHours - longitudeOffsetHours - equationOfTimeHours) * DEGREES_PER_HOUR

        assertEquals(
            "Fixture hand angle must equal the documented civil-to-apparent offset for ${fixture.siteName}",
            expectedOffset.mod(FULL_TURN_DEGREES),
            fixture.expectedHandAngleDeg,
            CIVIL_HAND_TOLERANCE_DEG,
        )
    }

    private companion object {
        const val FULL_TURN_DEGREES = 360.0
        const val HALF_TURN_DEGREES = 180.0
        const val DEGREES_PER_HOUR = 15.0
        const val SECONDS_PER_HOUR = 3600.0
        const val MINUTES_PER_HOUR = 60.0

        // Allows ephemeris/frame differences at independently sourced JPL Horizons solar transits.
        const val SOLAR_NOON_TOLERANCE_DEG = 0.02

        // Local apparent solar time advances 15°/h by definition. The engine's geometric Sun omits
        // annual aberration and topocentric parallax, but both are near-constant over an hour and
        // cancel in the difference, so the only residual is the equation of time's hourly change:
        // bounded by ~0.0052°/h (about 30 s of equation of time per day). The four fixtures measure
        // 14.99630°–14.99772°/h, a largest residual of 0.0037° (Sydney summer), so this tolerance
        // keeps better than a fivefold margin. If it ever trips, tighten the interval before widening.
        const val APPARENT_SOLAR_RATE_DEG_PER_HOUR = 15.0
        const val HOURLY_RATE_TOLERANCE_DEG = 0.02

        // Civil hand angle precision from LocalTime seconds
        const val CIVIL_HAND_TOLERANCE_DEG = 0.005

        // Two hemispheres crossed with two DST states.
        const val HEMISPHERE_DST_COMBINATIONS = 4
    }
}
