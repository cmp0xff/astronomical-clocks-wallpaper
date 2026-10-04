package io.github.godaniya.astronomicalclockswallpaper

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZoneOffset
import kotlin.math.abs

/**
 * Checks the engine-backed calculator against independent reference data in
 * [AstronomyReferenceFixtures]. These are plain JUnit tests: [AstronomyEngineCalculator] and
 * everything it reaches are free of Android types, so no Robolectric environment is needed.
 *
 * The tolerances are set below what the acceptance criteria allow and well above what the two
 * implementations actually disagree by, so a real regression fails while a rounding difference
 * does not. `docs/astronomy.md` records the measured spreads.
 */
class AstronomyEngineCalculatorTest {
    private val calculator = AstronomyEngineCalculator()

    @Test
    fun sunPositionsMatchHorizons() {
        assertPositions(expectedBodies = arrayOf("Sun"), toleranceDeg = SUN_TOLERANCE_DEG)
    }

    @Test
    fun moonPositionsMatchHorizons() {
        assertPositions(expectedBodies = arrayOf("Moon"), toleranceDeg = MOON_TOLERANCE_DEG)
    }

    @Test
    fun planetPositionsMatchHorizons() {
        val planets = Planet.entries.map { it.body.name }.toTypedArray()
        assertPositions(expectedBodies = planets, toleranceDeg = PLANET_TOLERANCE_DEG)
    }

    @Test
    fun magnitudesMatchHorizons() {
        var compared = 0
        for (fixture in positionFixtures) {
            val sky = calculator.sky(fixture.instant, fixture.site.location)
            // A null magnitude is a body this app models no magnitude for. The Sun is the one
            // such body: its fixture rows record a position and nothing else, because there is
            // nothing in [SolarState] to compare a solar magnitude against.
            val comparable = fixture.bodies.filter { it.magnitude != null }
            for (body in comparable) {
                assertEquals(
                    "${fixture.label}/${body.body} magnitude",
                    requireNotNull(body.magnitude),
                    magnitudeOf(sky, body.body),
                    MAGNITUDE_TOLERANCE,
                )
                compared++
            }
        }
        assertTrue("no magnitudes were compared", compared >= Planet.entries.size)
    }

    @Test
    fun lunarPhasesMatchUsnoInstants() {
        // The table is the only external reference for the Moon's phase. An elongation error that
        // vanishes at the nodes — subtracting the Moon from the Sun instead of the reverse, say —
        // leaves new and full untouched but turns every quarter inside out, so a table holding
        // only those two phases would not catch it.
        assertEquals(
            "the table must cover all four named phases",
            setOf("New Moon", "First Quarter", "Full Moon", "Last Quarter"),
            lunarPhaseFixtures.map { it.phase }.toSet(),
        )
        for (fixture in lunarPhaseFixtures) {
            val sky = calculator.sky(fixture.instant, GREENWICH.location)
            val where = "${fixture.phase} at ${fixture.instant}"
            // New-moon fixtures land just below 360 degrees, so the comparison must wrap.
            val skyDifference =
                angleDifferenceDeg(
                    first = sky.moon.phaseLongitudeDeg,
                    second = fixture.phaseLongitudeDeg,
                )
            assertTrue("$where phase longitude", abs(skyDifference) <= PHASE_TOLERANCE_DEG)
            when (fixture.phase) {
                "New Moon" -> {
                    assertTrue("$where illuminated fraction", sky.moon.phaseFraction <= NEW_FRACTION)
                    assertTrue("$where phase angle", sky.moon.phaseAngleDeg >= NEW_PHASE_ANGLE_DEG)
                }

                "Full Moon" -> {
                    assertTrue("$where illuminated fraction", sky.moon.phaseFraction >= FULL_FRACTION)
                    assertTrue("$where phase angle", sky.moon.phaseAngleDeg <= FULL_PHASE_ANGLE_DEG)
                    assertTrue("$where magnitude", sky.moon.magnitude < FULL_MOON_MAGNITUDE_LIMIT)
                }

                else -> {
                    // A quarter is half lit whatever its phase angle sign, which is exactly what a
                    // swapped elongation preserves; the named phase longitude above is what pins it.
                    assertEquals("$where illuminated fraction", HALF_FRACTION, sky.moon.phaseFraction, QUARTER_FRACTION)
                }
            }

            val geom = calculator.dialGeometry(fixture.instant, GREENWICH.location)
            val moonLon = requireNotNull(geom.moonLongitudeDeg) { "$where dialGeometry Moon longitude" }
            val moonPhase = requireNotNull(geom.moonPhaseLongitudeDeg) { "$where dialGeometry Moon phase" }
            assertTrue(moonLon >= 0.0 && moonLon < FULL_TURN_DEGREES)
            val geometryDifference = angleDifferenceDeg(first = moonPhase, second = fixture.phaseLongitudeDeg)
            assertTrue("$where dialGeometry phase longitude", abs(geometryDifference) <= PHASE_TOLERANCE_DEG)
        }
    }

    @Test
    fun eventsMatchHorizonsCrossings() {
        // The table is the only external reference for solar events, and these assertions are the
        // properties #4 asks the event check to cover: both hemispheres, and the polar cases where
        // an event does not happen at all. Without them a trimmed table would pass vacuously.
        assertTrue("no northern event fixture", eventFixtures.any { it.site.latitudeDeg > 0 })
        assertTrue("no southern event fixture", eventFixtures.any { it.site.latitudeDeg < 0 })
        assertTrue("no polar event fixture", eventFixtures.any { it.usnoPolarNote != null })
        for (fixture in eventFixtures) {
            val sky = calculator.sky(utcMidnight(fixture.date), fixture.site.location)
            for (kind in EventKind.entries) {
                val where = "${fixture.site.name} ${fixture.date} $kind"
                val expected = expectedEvent(fixture, kind)
                val actual = sky.eventTime(kind)
                if (expected == null) {
                    assertNull("$where should not occur", actual)
                } else {
                    assertNotNull("$where should occur", actual)
                    assertWithin(
                        where = where,
                        expected = expected,
                        actual = requireNotNull(actual),
                        seconds = EVENT_TOLERANCE_SECONDS,
                    )
                }
            }
        }
    }

    @Test
    fun horizonEventsAgreeWithUsno() {
        for (fixture in eventFixtures) {
            val sky = calculator.sky(utcMidnight(fixture.date), fixture.site.location)
            val published =
                listOf(
                    EventKind.SUNRISE to fixture.usnoSunrise,
                    EventKind.SUNSET to fixture.usnoSunset,
                    EventKind.CIVIL_DAWN to fixture.usnoCivilDawn,
                    EventKind.CIVIL_DUSK to fixture.usnoCivilDusk,
                )
            for ((kind, time) in published) {
                assertUsnoAgreement(fixture = fixture, kind = kind, published = time, sky = sky)
            }
        }
    }

    @Test
    fun sunMatchesIauConstellations() {
        // Four instants that resolve to four different constellations: trimming the table to one
        // row would leave the check passing against a single boundary region.
        assertEquals(
            "the fixtures should resolve to four different constellations",
            SUN_CONSTELLATION_COUNT,
            sunConstellationFixtures.map { it.constellation }.toSet().size,
        )
        for (fixture in sunConstellationFixtures) {
            val sky = calculator.sky(fixture.instant, fixture.site.location)
            assertEquals(
                "Sun constellation at ${fixture.instant}",
                fixture.constellation,
                sky.sun.constellation,
            )
        }
    }

    @Test
    fun eventWindowIsTheUtcDay() {
        // Quito's nautical dusk falls just after midnight UTC and its nautical dawn late in the
        // UTC morning, so both belong to the same UTC day. The window depends on the date alone:
        // asking at either end of the day must give the same answer.
        val early = calculator.sky(Instant.parse("2026-03-20T00:00:00Z"), QUITO.location)
        val late = calculator.sky(Instant.parse("2026-03-20T23:59:59Z"), QUITO.location)
        for (kind in EventKind.entries) {
            assertEquals(
                "$kind must not depend on the time of day",
                early.eventTime(kind),
                late.eventTime(kind),
            )
        }
        val dusk = requireNotNull(early.eventTime(EventKind.NAUTICAL_DUSK)) { "no nautical dusk" }
        val dawn = requireNotNull(early.eventTime(EventKind.NAUTICAL_DAWN)) { "no nautical dawn" }
        assertTrue("nautical dusk $dusk should precede dawn $dawn on one UTC day", dusk < dawn)
        val dayStart = Instant.parse("2026-03-20T00:00:00Z")
        val dayEnd = Instant.parse("2026-03-21T00:00:00Z")
        assertTrue("nautical dusk $dusk should be inside the UTC day", dusk >= dayStart && dusk < dayEnd)

        // The following UTC day is a different window with its own times.
        val nextDay = calculator.sky(Instant.parse("2026-03-21T12:00:00Z"), QUITO.location)
        val sunrise = requireNotNull(early.eventTime(EventKind.SUNRISE)) { "no sunrise" }
        val nextSunrise = requireNotNull(nextDay.eventTime(EventKind.SUNRISE)) { "no next sunrise" }
        assertTrue("the next UTC day should shift its sunrise", nextSunrise > sunrise)
    }

    @Test
    fun skyIsDefinedAtBothPoles() {
        // Azimuth is not a meaningful direction at a pole — every bearing is south from the
        // north pole — so the risk is a reduction that returns NaN or an out-of-range angle
        // there, which [Horizontal] would reject and turn into a thrown exception in the middle
        // of a render. The engine's azimuth is a normalised atan2 and stays finite, so the
        // calculator is expected to answer. Nothing else covers a latitude so far from the
        // fixtures' 78.22 degrees, and [ObservingLocation] accepts exactly 90.
        for (latitude in listOf(90.0, -90.0)) {
            val site =
                ObservingLocation(
                    latitude = latitude,
                    longitude = 0.0,
                    source = ObservingLocation.Source.MANUAL,
                    zoneId = ZoneOffset.UTC,
                )
            for (instant in POLAR_INSTANTS) {
                val sky = calculator.sky(instant, site)
                val where = "latitude $latitude at $instant"
                assertEquals("$where stars", StarCatalog.stars.size, sky.stars.size)
                assertEquals("$where planets", Planet.entries.size, sky.planets.size)
                // A pole sees no solar event at all: over one UTC day the Sun's altitude there
                // moves only by the day's change in declination, under half a degree, and the
                // nearest of the eight thresholds is 50 arcminutes away.
                for (kind in EventKind.entries) {
                    assertNull("$where $kind must not occur", sky.eventTime(kind))
                }
            }
        }
    }

    @Test
    fun savedZoneDoesNotChangeSky() {
        // Quito's twilight straddles UTC midnight. These civil zones put the same instant on
        // different dates, so an accidental change to local-day event searches also fails here.
        val instant = Instant.parse("2026-03-20T23:30:00Z")
        val west = QUITO.location.copy(zoneId = ZoneId.of("Pacific/Honolulu"))
        val east = QUITO.location.copy(zoneId = ZoneId.of("Pacific/Kiritimati"))
        assertTrue(instant.atZone(west.zoneId).toLocalDate() != instant.atZone(east.zoneId).toLocalDate())
        assertEquals(
            "civil timezone must not change bodies, phases, stars, or UTC solar events",
            calculator.sky(instant, west),
            calculator.sky(instant, east),
        )
    }

    @Test
    fun skyReportsEveryBodyAndEvent() {
        val sky = calculator.sky(Instant.parse("2026-06-21T12:00:00Z"), GREENWICH.location)
        assertEquals(Planet.entries.size, sky.planets.size)
        assertEquals(StarCatalog.stars.size, sky.stars.size)
        assertEquals(EventKind.entries.size, sky.events.size)
        for (kind in EventKind.entries) {
            assertEquals("$kind appears once", 1, sky.events.count { it.kind == kind })
        }
    }

    private fun assertPositions(expectedBodies: Array<String>, toleranceDeg: Double) {
        var compared = 0
        for (fixture in positionFixtures) {
            val sky = calculator.sky(fixture.instant, fixture.site.location)
            for (body in fixture.bodies) {
                if (body.body !in expectedBodies) continue
                val actual = positionOf(sky, body.body)
                val where = "${fixture.label}/${body.body}"
                assertTrue(
                    "$where azimuth: expected ${body.azimuthDeg}, found ${actual.azimuthDeg}",
                    abs(angleDifferenceDeg(first = actual.azimuthDeg, second = body.azimuthDeg)) <= toleranceDeg,
                )
                // The app applies Refraction.Normal, which fades toward the nadir below -1
                // degree, while the JPL Horizons convention holds the -1 degree value. The two
                // models agree above that limit; below it refraction lifts altitude but does not
                // turn bearing, so only azimuth is compared there.
                if (body.altitudeDeg >= REFRACTION_COMPARABLE_ALTITUDE_DEG) {
                    assertEquals("$where altitude", body.altitudeDeg, actual.altitudeDeg, toleranceDeg)
                }
                compared++
            }
        }
        assertTrue("no positions were compared for ${expectedBodies.joinToString()}", compared > 0)
    }

    private fun assertUsnoAgreement(fixture: EventFixture, kind: EventKind, published: LocalTime?, sky: Sky) {
        val where = "${fixture.site.name} ${fixture.date} ${kind.name.lowercase()}"
        val note = fixture.usnoPolarNote?.let { " ($it)" }.orEmpty()
        val actual = sky.eventTime(kind)
        if (published == null) {
            assertNull("$where: USNO reports no event$note", actual)
            return
        }
        assertNotNull("$where: USNO publishes $published$note", actual)
        assertWithin(
            where = "$where against USNO",
            expected = requireNotNull(utcInstant(date = fixture.date, time = published)),
            actual = requireNotNull(actual),
            seconds = USNO_TOLERANCE_SECONDS,
        )
    }

    private fun expectedEvent(fixture: EventFixture, kind: EventKind): Instant? {
        val timeOfDay =
            when (kind) {
                EventKind.SUNRISE -> fixture.sunrise
                EventKind.SUNSET -> fixture.sunset
                EventKind.CIVIL_DAWN -> fixture.civilDawn
                EventKind.CIVIL_DUSK -> fixture.civilDusk
                EventKind.NAUTICAL_DAWN -> fixture.nauticalDawn
                EventKind.NAUTICAL_DUSK -> fixture.nauticalDusk
                EventKind.ASTRONOMICAL_DAWN -> fixture.astronomicalDawn
                EventKind.ASTRONOMICAL_DUSK -> fixture.astronomicalDusk
            }
        return utcInstant(date = fixture.date, time = timeOfDay)
    }

    private fun positionOf(sky: Sky, body: String): Horizontal {
        val planet = Planet.entries.firstOrNull { it.body.name == body }
        return when (body) {
            SUN -> sky.sun.position
            MOON -> sky.moon.position
            else -> sky.planets.single { state -> state.planet == requireNotNull(planet) }.position
        }
    }

    private fun magnitudeOf(sky: Sky, body: String): Double {
        require(body != SUN) { "the Sun has no modelled magnitude" }
        return if (body == MOON) sky.moon.magnitude else planetOf(sky, body).magnitude
    }

    private fun planetOf(sky: Sky, body: String): PlanetState {
        val planet = Planet.entries.single { it.body.name == body }
        return sky.planets.single { it.planet == planet }
    }

    private fun assertWithin(where: String, expected: Instant, actual: Instant, seconds: Long) {
        val delta = Duration.between(expected, actual).seconds
        assertTrue("$where: expected $expected, found $actual (${delta}s)", abs(delta) <= seconds)
    }

    private fun utcMidnight(date: LocalDate): Instant = date.atStartOfDay(ZoneOffset.UTC).toInstant()

    private companion object {
        const val SUN = "Sun"
        const val MOON = "Moon"

        // Bounds retain the measured-spread checks formerly run in separate comparison passes.
        // docs/astronomy.md distinguishes these regression bounds from acceptance tolerances.
        const val SUN_TOLERANCE_DEG = 0.002
        const val MOON_TOLERANCE_DEG = 0.003
        const val PLANET_TOLERANCE_DEG = 0.008
        const val MAGNITUDE_TOLERANCE = 0.2
        const val PHASE_TOLERANCE_DEG = 0.01
        const val HALF_FRACTION = 0.5

        // USNO publishes a quarter to the minute, and the engine's phase differs from the
        // geometric elongation by about 20 arcseconds, under 0.006 degree of lunar motion.
        const val QUARTER_FRACTION = 0.01
        const val FULL_FRACTION = 0.99
        const val NEW_FRACTION = 0.01
        const val FULL_PHASE_ANGLE_DEG = 5.0
        const val NEW_PHASE_ANGLE_DEG = 175.0
        const val FULL_MOON_MAGNITUDE_LIMIT = -12.0
        const val EVENT_TOLERANCE_SECONDS = 5L
        const val USNO_TOLERANCE_SECONDS = 60L
        const val REFRACTION_COMPARABLE_ALTITUDE_DEG = -1.0

        /** The four IAU constellations the Sun is checked in: Pisces, Taurus, Virgo, Sagittarius. */
        const val SUN_CONSTELLATION_COUNT = 4

        /** Instants spread across the year for [skyIsDefinedAtBothPoles]. */
        val POLAR_INSTANTS: List<Instant> =
            listOf(
                Instant.parse("2026-03-20T12:00:00Z"),
                Instant.parse("2026-06-21T00:00:00Z"),
                Instant.parse("2026-12-21T12:00:00Z"),
            )
    }
}
