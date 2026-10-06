package io.github.godaniya.astronomicalclockswallpaper

import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId

/**
 * Independent reference fixtures for local apparent noon (solar meridian transit) generated
 * from JPL Horizons (DE441 ephemeris, target Sun, topocentric observer) and independent
 * celestial transit calculations.
 *
 * At Local Apparent Noon:
 * - The Sun transits the local celestial meridian; its apparent local hour angle is `H = 0.0°`.
 * - Local Apparent Solar Time is exactly 12:00:00.
 * - The dial uses the engine's geocentric geometric Sun position, which omits annual aberration and
 *   topocentric solar parallax; its marker therefore approximates, rather than exactly represents, the
 *   apparent hour angle. The test checks its bearing against XII within the documented tolerance, which
 *   covers that residual.
 * - The civil hand reflects the observing location's geographic timezone (including DST) at that instant.
 * - The civil/apparent offset is predicted by:
 *   `civil - apparent = zone offset (incl. DST) - longitude / 15° - equation of time`.
 *
 * Primary sources:
 * - JPL Horizons (DE441 ephemeris), topocentric observer at each site's coordinates, target Sun,
 *   `CENTER='coord@399'`, `COMMAND='10'`, `QUANTITIES='4'` (azimuth/elevation). Transit instants
 *   are linearly interpolated between the listed one-minute azimuth samples.
 *   For northern sites transit occurs at azimuth 180.0° (due South); for southern sites transit occurs at
 *   azimuth 0.0°/360.0° (due North).
 * - Queries (east longitude, north latitude, elevation in km):
 *   1. Prague (50.0875° N, 14.4214° E, `Europe/Prague`):
 *      `SITE_COORD='14.4214,50.0875,0'`
 *      - 2026-06-21T11:04:07Z (Summer / DST, CEST = UTC+2):
 *        JPL Horizons 11:04:00 az=179.937°, 11:05:00 az=180.449°, transit at 11:04:07 UTC.
 *        Civil time: 13:04:07 CEST.
 *      - 2026-01-15T11:11:42Z (Winter / non-DST, CET = UTC+1):
 *        JPL Horizons 11:11:00 az=179.829°, 11:12:00 az=180.075°, transit at 11:11:42 UTC.
 *        Civil time: 12:11:42 CET.
 *   2. Sydney (-33.8688° S, 151.2093° E, `Australia/Sydney`):
 *      `SITE_COORD='151.2093,-33.8688,0'`
 *      - 2026-01-15T02:04:24Z (Summer / DST, AEDT = UTC+11):
 *        JPL Horizons 02:04:00 az=0.432°, 02:05:00 az=359.374° (-0.626°), transit at 02:04:24 UTC.
 *        Civil time: 13:04:24 AEDT.
 *      - 2026-06-21T01:56:53Z (Winter / non-DST, AEST = UTC+10):
 *        JPL Horizons 01:56:00 az=0.242°, 01:57:00 az=359.970° (-0.030°), transit at 01:56:53 UTC.
 *        Civil time: 11:56:53 AEST.
 *
 * Equation-of-time fixture values are derived at each transit from the same transit instant and
 * longitude as the rest of that fixture, as `apparent noon − mean solar time`, where mean local solar
 * time is `UTC + longitude / 15°`. They are therefore a cross-check on the fixture constants and the
 * JDK zone rules, not an independent source against the engine's ephemeris: the transit instants above
 * are independently sourced from JPL Horizons, but each equation-of-time value restates its own transit
 * instant. The engine residual the tests actually measure is the omitted annual aberration (~20.5″)
 * plus topocentric solar parallax (~8.8″), both absent from the geometric Sun position the dial projects.
 */
internal data class ApparentSolarTimeFixture(
    val siteName: String,
    val latitudeDeg: Double,
    val longitudeDeg: Double,
    val zoneId: ZoneId,
    val isDst: Boolean,
    val instant: Instant,
    val expectedEquationOfTimeMinutes: Double,
    val expectedCivilTime: LocalTime,
    val expectedHandAngleDeg: Double,
) {
    /** The manual observing site this fixture describes, as [AstronomyEngineCalculator] consumes it. */
    val location: ObservingLocation =
        ObservingLocation(
            latitude = latitudeDeg,
            longitude = longitudeDeg,
            source = ObservingLocation.Source.MANUAL,
            zoneId = zoneId,
        )
}

internal val apparentSolarTimeFixtures: List<ApparentSolarTimeFixture> =
    listOf(
        ApparentSolarTimeFixture(
            siteName = "Prague Summer (DST)",
            latitudeDeg = 50.0875,
            longitudeDeg = 14.4214,
            zoneId = ZoneId.of("Europe/Prague"),
            isDst = true,
            instant = Instant.parse("2026-06-21T11:04:07Z"),
            expectedEquationOfTimeMinutes = -1.8022666666667,
            expectedCivilTime = LocalTime.of(13, 4, 7),
            expectedHandAngleDeg = 16.02916666666667,
        ),
        ApparentSolarTimeFixture(
            siteName = "Prague Winter (non-DST)",
            latitudeDeg = 50.0875,
            longitudeDeg = 14.4214,
            zoneId = ZoneId.of("Europe/Prague"),
            isDst = false,
            instant = Instant.parse("2026-01-15T11:11:42Z"),
            expectedEquationOfTimeMinutes = -9.3856,
            expectedCivilTime = LocalTime.of(12, 11, 42),
            expectedHandAngleDeg = 2.925,
        ),
        ApparentSolarTimeFixture(
            siteName = "Sydney Summer (DST)",
            latitudeDeg = -33.8688,
            longitudeDeg = 151.2093,
            zoneId = ZoneId.of("Australia/Sydney"),
            isDst = true,
            instant = Instant.parse("2026-01-15T02:04:24Z"),
            expectedEquationOfTimeMinutes = -9.2372,
            expectedCivilTime = LocalTime.of(13, 4, 24),
            expectedHandAngleDeg = 16.10,
        ),
        ApparentSolarTimeFixture(
            siteName = "Sydney Winter (non-DST)",
            latitudeDeg = -33.8688,
            longitudeDeg = 151.2093,
            zoneId = ZoneId.of("Australia/Sydney"),
            isDst = false,
            instant = Instant.parse("2026-06-21T01:56:53Z"),
            expectedEquationOfTimeMinutes = -1.7205333333333,
            expectedCivilTime = LocalTime.of(11, 56, 53),
            expectedHandAngleDeg = 359.22083333333336,
        ),
    )
