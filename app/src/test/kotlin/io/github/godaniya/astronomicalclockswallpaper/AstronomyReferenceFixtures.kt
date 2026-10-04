package io.github.godaniya.astronomicalclockswallpaper

import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset

/*
 * Reference data for the astronomy tests.
 *
 * None of these numbers came from Astronomy Engine. They were fetched from independent services
 * and transcribed verbatim, so that the tests compare two implementations rather than one
 * implementation with itself. Fetching is deliberately not part of the build: the tests stay
 * offline and their expected values never move.
 *
 * Sources, all retrieved 2026-09-29
 *
 * - NASA/JPL Horizons, https://ssd.jpl.nasa.gov/horizons/ (API:
 *   https://ssd-api.jpl.nasa.gov/doc/horizons.html), target centres on `coord@399`.
 *   Body positions and apparent magnitudes are topocentric azimuth and elevation with
 *   `APPARENT='REFRACTED'` — apparent places including atmospheric refraction, matching what
 *   the app reports. Solar-event times use the same service with `APPARENT='AIRLESS'`: a whole
 *   UTC day of the Sun's centre elevation at one-minute steps, linearly interpolated to the
 *   crossing of the standard altitudes. Those are -50 arcminutes for the Sun's centre at
 *   sunrise and sunset — the -34 arcminutes of near-horizon refraction at the upper limb, less
 *   the 16-arcminute solar semidiameter — and -6, -12, and -18 degrees for the civil, nautical,
 *   and astronomical twilights. Interpolating a smooth curve sampled once a minute costs at most
 *   a couple of seconds, far inside the event tolerance.
 * - United States Naval Observatory Astronomical Applications API,
 *   https://aa.usno.navy.mil/data/api. An independent second opinion: sunrise, sunset, and
 *   civil twilight published to the minute, and the equinox and solstice instants used as
 *   fixture instants. The `usno*` fields record it; `usnoPolarNote` is its own statement that
 *   an event does not happen at all that day.
 * - Hipparcos main catalogue, ESA 1997 (CDS I/239/hip_main), through VizieR,
 *   https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=I/239/hip_main with `Vmag=<1.65` —
 *   VizieR's strictly-less-than constraint, which is why the row at exactly 1.65 is absent.
 *   Positions are `_RA.icrs` and `_DE.icrs`, the J2000 place with proper motion applied, and
 *   `catalogEpochFixtures` below uses `RAICRS`/`DEICRS`, the same stars at J1991.25.
 * - IAU SOFA reference implementation through pyerfa, https://github.com/liberfa/pyerfa:
 *   `erfa.atco13` reduces each star from its catalogue place through proper motion, parallax,
 *   light deflection, aberration, precession-nutation, Earth rotation, and standard refraction.
 *   Bayer designations and so constellations come from SIMBAD, https://simbad.cds.unistra.fr.
 *
 * Where a source publishes only minutes, the value is quoted to the minute; no digit has been
 * invented. Times without a source entry are `null` because that source reports no such event.
 *
 * The `starFixtures` rows are reproducible by hand, which is why the SOFA call is spelled out.
 * `erfa.atco13` was called with the catalogue's J2000 place in radians, its `pmRA` divided by
 * `cos(dec)` and converted from milliarcseconds per year to radians per year — the conversion
 * from `mu_alpha * cos(delta)` to a rate of change of right ascension that [StarCatalog]
 * documents — `pmDE` converted the same way, parallax and radial velocity zero, the site's
 * longitude and latitude in radians with height zero, `dut1`, `xp`, and `yp` zero, and
 * `phpa = 1013.25`, `tc = 15`, `rh = 0.5`, `wl = 0.55`. Those last four are the reference's own
 * refraction parameters, not the app's: the app uses the engine's Saemundsson fit, standardised
 * at 1010 mb and 10 degrees, so the two refract differently. Every star row kept here is above
 * 15 degrees of altitude, where that difference is far inside the star tolerance. Repeating the
 * call with pyerfa 2.0.1 reproduces all of these rows to within 0.000005 degrees.
 *
 * No generator script is committed. Fetching needs the network and these tests must stay
 * offline, so keeping the retrieval out of the build was preferred over a script nobody runs;
 * the pinned pyerfa plus the parameters above is the recipe, and re-running it is also how the
 * catalogue's completeness claim would be re-checked.
 */

/** An observing location used by the fixtures, named after the place it stands for. */
internal class SiteFixture(val name: String, val latitudeDeg: Double, val longitudeDeg: Double) {
    val location: ObservingLocation
        get() =
            ObservingLocation(
                latitude = latitudeDeg,
                longitude = longitudeDeg,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneOffset.UTC,
            )
}

/**
 * One body's reference position at a fixture instant. [magnitude] is null where the app models no
 * magnitude for the body, which is the Sun only; every other row carries the reference's own.
 */
internal data class BodyFixture(
    val body: String,
    val azimuthDeg: Double,
    val altitudeDeg: Double,
    val magnitude: Double? = null,
)

/** Positions of several bodies at one instant over one site, from JPL Horizons. */
internal data class PositionFixture(
    val label: String,
    val site: SiteFixture,
    val instant: Instant,
    val bodies: List<BodyFixture>,
)

/** Star observations sharing one site and instant, from Hipparcos reduced with SOFA. */
internal data class StarScenario(val site: SiteFixture, val instant: Instant, val stars: List<StarFixture>)

/** One star's horizontal coordinates in degrees and its SIMBAD constellation. */
internal data class StarFixture(
    val name: String,
    val constellation: String,
    val azimuthDeg: Double,
    val altitudeDeg: Double,
)

/**
 * The solar events of one UTC day at one site.
 *
 * The eight event fields are the Horizons crossings; the `usno*` fields repeat sunrise, sunset,
 * and civil twilight as USNO publishes them, and are used to check the crossing method itself.
 */
internal data class EventFixture(
    val site: SiteFixture,
    val date: LocalDate,
    val sunrise: LocalTime?,
    val sunset: LocalTime?,
    val civilDawn: LocalTime?,
    val civilDusk: LocalTime?,
    val nauticalDawn: LocalTime?,
    val nauticalDusk: LocalTime?,
    val astronomicalDawn: LocalTime?,
    val astronomicalDusk: LocalTime?,
    val usnoSunrise: LocalTime?,
    val usnoSunset: LocalTime?,
    val usnoCivilDawn: LocalTime?,
    val usnoCivilDusk: LocalTime?,
    val usnoPolarNote: String?,
)

/**
 * A star's place at the Hipparcos observing epoch, J1991.25, which is exactly 8.75 Julian years
 * before J2000. The catalogue publishes both epochs, so stepping the J2000 place back by the
 * star's proper motion must land on this number: the two epochs are consistent with each other
 * only if `mu_alpha * cos(delta)` is converted correctly.
 */
internal data class CatalogEpochFixture(val name: String, val rightAscensionDeg: Double, val declinationDeg: Double)

/**
 * A lunar phase instant published by USNO, and which phase it is.
 *
 * [phaseLongitudeDeg] is the phase angle the instant is named for: 0 new, 90 first quarter,
 * 180 full, 270 last quarter. Naming it per row rather than deriving it from [phase] keeps the
 * expected value an independent claim instead of a restatement of the label.
 */
internal data class LunarPhaseFixture(val instant: Instant, val phase: String, val phaseLongitudeDeg: Double)

/**
 * The constellation the Sun stands in at a fixture instant, from the IAU boundaries.
 *
 * The equinoxes and solstices fix the Sun's ecliptic longitude exactly at 0, 180, and 270
 * degrees, and the standard IAU ingress dates then place it in Pisces, Virgo, and Sagittarius.
 * The fourth instant is well inside the Sun's Taurus interval, May 14 to June 21.
 */
internal data class SunConstellationFixture(val instant: Instant, val site: SiteFixture, val constellation: String)

/** Combines a fixture's UTC date and time of day into the instant the tests expect. */
internal fun utcInstant(date: LocalDate, time: LocalTime?): Instant? =
    time?.let { date.atTime(it).toInstant(ZoneOffset.UTC) }

/**
 * The signed difference between two angles in degrees, normalised to `-180..180`.
 *
 * Both angles may be wrapped, so a subtraction alone is not enough: the difference between a
 * computed azimuth of 359.999 and a reference of 0.001 is two thousandths of a degree, not 360.
 */
internal fun angleDifferenceDeg(first: Double, second: Double): Double =
    (first - second + HALF_TURN_DEGREES + FULL_TURN_DEGREES) % FULL_TURN_DEGREES - HALF_TURN_DEGREES

private const val HALF_TURN_DEGREES = 180.0

// Observing locations, WGS-84 degrees, north and east positive.
internal val GREENWICH =
    SiteFixture(name = "Greenwich, United Kingdom (Royal Observatory)", latitudeDeg = 51.4779, longitudeDeg = 0.0)
internal val CAPE_TOWN =
    SiteFixture(name = "Cape Town, South Africa", latitudeDeg = -33.9249, longitudeDeg = 18.4241)
internal val QUITO =
    SiteFixture(name = "Quito, Ecuador", latitudeDeg = -0.1807, longitudeDeg = -78.4678)
internal val SVALBARD =
    SiteFixture(name = "Longyearbyen, Svalbard", latitudeDeg = 78.22, longitudeDeg = 15.65)
internal val MCMURDO =
    SiteFixture(name = "McMurdo Station, Antarctica", latitudeDeg = -77.85, longitudeDeg = 166.67)

// Apparent topocentric positions and magnitudes, JPL Horizons, REFRACTED.
internal val positionFixtures: List<PositionFixture> =
    listOf(
        PositionFixture(
            label = "june-solstice-north",
            site = GREENWICH,
            instant = Instant.parse("2026-06-21T08:24:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 102.805313, altitudeDeg = 40.068553),
                    BodyFixture(body = "Moon", azimuthDeg = 48.961976, altitudeDeg = -26.692793, magnitude = -9.833),
                    BodyFixture(body = "Mercury", azimuthDeg = 83.869493, altitudeDeg = 22.739509, magnitude = 0.912),
                    BodyFixture(body = "Venus", azimuthDeg = 72.404142, altitudeDeg = 11.540836, magnitude = -4.024),
                    BodyFixture(body = "Mars", azimuthDeg = 151.789807, altitudeDeg = 54.485156, magnitude = 1.297),
                    BodyFixture(body = "Jupiter", azimuthDeg = 80.475448, altitudeDeg = 19.494436, magnitude = -1.827),
                    BodyFixture(body = "Saturn", azimuthDeg = 208.539921, altitudeDeg = 38.389956, magnitude = 0.812),
                    BodyFixture(body = "Uranus", azimuthDeg = 137.393349, altitudeDeg = 53.258088, magnitude = 5.818),
                    BodyFixture(body = "Neptune", azimuthDeg = 217.768936, altitudeDeg = 32.72827, magnitude = 7.774),
                ),
        ),
        PositionFixture(
            label = "december-solstice-transit",
            site = GREENWICH,
            instant = Instant.parse("2026-12-21T12:00:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 180.459472, altitudeDeg = 15.142566),
                    BodyFixture(body = "Moon", azimuthDeg = 36.653915, altitudeDeg = -7.357389, magnitude = -11.919),
                    BodyFixture(body = "Mercury", azimuthDeg = 186.96439, altitudeDeg = 14.208799, magnitude = -0.865),
                    BodyFixture(body = "Venus", azimuthDeg = 228.225717, altitudeDeg = 13.437286, magnitude = -4.681),
                    BodyFixture(body = "Mars", azimuthDeg = 291.984709, altitudeDeg = -2.135219, magnitude = 0.041),
                    BodyFixture(body = "Jupiter", azimuthDeg = 302.439319, altitudeDeg = -6.696584, magnitude = -2.337),
                    BodyFixture(body = "Saturn", azimuthDeg = 82.946944, altitudeDeg = -3.806996, magnitude = 0.696),
                    BodyFixture(body = "Uranus", azimuthDeg = 28.262226, altitudeDeg = -12.838646, magnitude = 5.611),
                    BodyFixture(body = "Neptune", azimuthDeg = 88.841271, altitudeDeg = -1.068997, magnitude = 7.75),
                ),
        ),
        PositionFixture(
            label = "march-equinox-north",
            site = GREENWICH,
            instant = Instant.parse("2026-03-20T14:46:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 226.648532, altitudeDeg = 28.684297),
                ),
        ),
        PositionFixture(
            label = "december-solstice-antisolar",
            site = GREENWICH,
            instant = Instant.parse("2026-12-21T20:50:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 289.757455, altitudeDeg = -43.818091),
                ),
        ),
        PositionFixture(
            label = "june-solstice-south",
            site = CAPE_TOWN,
            instant = Instant.parse("2026-06-21T12:00:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 340.893472, altitudeDeg = 30.171335),
                    BodyFixture(body = "Moon", azimuthDeg = 76.436534, altitudeDeg = 18.295794, magnitude = -9.919),
                    BodyFixture(body = "Jupiter", azimuthDeg = 13.779159, altitudeDeg = 33.92807, magnitude = -1.826),
                    BodyFixture(body = "Saturn", azimuthDeg = 270.136565, altitudeDeg = -4.982521, magnitude = 0.812),
                ),
        ),
        PositionFixture(
            label = "march-equinox-equator",
            site = QUITO,
            instant = Instant.parse("2026-03-20T14:46:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 89.775252, altitudeDeg = 51.193475),
                    BodyFixture(body = "Moon", azimuthDeg = 76.473775, altitudeDeg = 31.986666, magnitude = -6.211),
                ),
        ),
        PositionFixture(
            label = "polar-day-midnight",
            site = SVALBARD,
            instant = Instant.parse("2026-06-21T00:00:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 14.26124, altitudeDeg = 12.115819),
                    BodyFixture(body = "Moon", azimuthDeg = 295.92868, altitudeDeg = -2.342771, magnitude = -9.667),
                ),
        ),
        PositionFixture(
            label = "polar-night-noon",
            site = SVALBARD,
            instant = Instant.parse("2026-12-21T12:00:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 195.11397, altitudeDeg = -11.444628),
                ),
        ),
        PositionFixture(
            label = "full-moon-january",
            site = GREENWICH,
            instant = Instant.parse("2026-01-03T10:03:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Moon", azimuthDeg = 332.631099, altitudeDeg = -7.260228, magnitude = -12.789),
                ),
        ),
        PositionFixture(
            label = "new-moon-january",
            site = GREENWICH,
            instant = Instant.parse("2026-01-18T19:52:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Moon", azimuthDeg = 273.309468, altitudeDeg = -33.632805, magnitude = -4.251),
                ),
        ),
        PositionFixture(
            label = "full-moon-september",
            site = GREENWICH,
            instant = Instant.parse("2026-09-26T16:49:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Moon", azimuthDeg = 76.113224, altitudeDeg = -5.958474, magnitude = -12.682),
                ),
        ),
        PositionFixture(
            label = "new-moon-august",
            site = GREENWICH,
            instant = Instant.parse("2026-08-12T17:37:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Moon", azimuthDeg = 274.332728, altitudeDeg = 15.797505, magnitude = -3.971),
                ),
        ),
        PositionFixture(
            label = "september-equinox-north",
            site = GREENWICH,
            instant = Instant.parse("2026-09-23T00:05:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 3.976911, altitudeDeg = -37.810039),
                ),
        ),
        PositionFixture(
            label = "june-taurus",
            site = GREENWICH,
            instant = Instant.parse("2026-06-05T12:00:00Z"),
            bodies =
                listOf(
                    BodyFixture(body = "Sun", azimuthDeg = 180.71552, altitudeDeg = 61.106056),
                ),
        ),
    )

// Hipparcos J2000 places reduced to the topocentric horizontal frame with SOFA.
// Only fixtures above 15 degrees altitude are kept, so that both refraction models are small
// and their difference is inside the star tolerance: Saemundsson gives about 3.7 arcminutes
// there. Lower altitudes are where the two models part company — the engine's extra fade
// below -1 degree widens that further — which is why the position tests stop comparing
// altitude at all below -1 degree.
// Every one of the 26 bundled stars appears here at least once.
internal val starFixtures: List<StarScenario> =
    listOf(
        StarScenario(
            site = GREENWICH,
            instant = Instant.parse("2026-06-21T22:00:00Z"),
            stars =
                listOf(
                    StarFixture(
                        name = "Arcturus",
                        constellation = "Boo",
                        azimuthDeg = 221.77046,
                        altitudeDeg = 51.72762,
                    ),
                    StarFixture(name = "Vega", constellation = "Lyr", azimuthDeg = 99.31967, altitudeDeg = 59.9591),
                    StarFixture(name = "Altair", constellation = "Aql", azimuthDeg = 110.5515, altitudeDeg = 26.67119),
                    StarFixture(name = "Spica", constellation = "Vir", azimuthDeg = 220.15852, altitudeDeg = 19.00599),
                    StarFixture(name = "Deneb", constellation = "Cyg", azimuthDeg = 68.59202, altitudeDeg = 44.68507),
                ),
        ),
        StarScenario(
            site = GREENWICH,
            instant = Instant.parse("2026-01-15T02:00:00Z"),
            stars =
                listOf(
                    StarFixture(
                        name = "Arcturus",
                        constellation = "Boo",
                        azimuthDeg = 93.46879,
                        altitudeDeg = 27.40574,
                    ),
                    StarFixture(name = "Capella", constellation = "Aur", azimuthDeg = 289.09254, altitudeDeg = 48.3114),
                    StarFixture(
                        name = "Procyon",
                        constellation = "CMi",
                        azimuthDeg = 218.09545,
                        altitudeDeg = 37.67453,
                    ),
                    StarFixture(
                        name = "Betelgeuse",
                        constellation = "Ori",
                        azimuthDeg = 246.18016,
                        altitudeDeg = 26.88443,
                    ),
                    StarFixture(
                        name = "Aldebaran",
                        constellation = "Tau",
                        azimuthDeg = 269.11981,
                        altitudeDeg = 22.10521,
                    ),
                    StarFixture(name = "Pollux", constellation = "Gem", azimuthDeg = 232.23651, altitudeDeg = 58.59511),
                    StarFixture(
                        name = "Regulus",
                        constellation = "Leo",
                        azimuthDeg = 167.95685,
                        altitudeDeg = 49.85616,
                    ),
                    StarFixture(name = "Castor", constellation = "Gem", azimuthDeg = 240.43104, altitudeDeg = 60.31597),
                    StarFixture(
                        name = "Bellatrix",
                        constellation = "Ori",
                        azimuthDeg = 252.14423,
                        altitudeDeg = 21.67903,
                    ),
                ),
        ),
        StarScenario(
            site = CAPE_TOWN,
            instant = Instant.parse("2026-06-21T20:00:00Z"),
            stars =
                listOf(
                    StarFixture(
                        name = "Arcturus",
                        constellation = "Boo",
                        azimuthDeg = 343.45326,
                        altitudeDeg = 35.3437,
                    ),
                    StarFixture(
                        name = "Rigil Kentaurus",
                        constellation = "Cen",
                        azimuthDeg = 188.50362,
                        altitudeDeg = 62.48288,
                    ),
                    StarFixture(name = "Hadar", constellation = "Cen", azimuthDeg = 197.41269, altitudeDeg = 61.22173),
                    StarFixture(name = "Acrux", constellation = "Cru", azimuthDeg = 208.32036, altitudeDeg = 51.12545),
                    StarFixture(name = "Spica", constellation = "Vir", azimuthDeg = 306.39011, altitudeDeg = 56.6925),
                    StarFixture(name = "Antares", constellation = "Sco", azimuthDeg = 71.11377, altitudeDeg = 71.77486),
                    StarFixture(name = "Mimosa", constellation = "Cru", azimuthDeg = 211.10756, altitudeDeg = 55.02267),
                    StarFixture(name = "Gacrux", constellation = "Cru", azimuthDeg = 216.77667, altitudeDeg = 54.27543),
                    StarFixture(name = "Shaula", constellation = "Sco", azimuthDeg = 106.64219, altitudeDeg = 61.18163),
                ),
        ),
        StarScenario(
            site = QUITO,
            instant = Instant.parse("2026-03-20T03:00:00Z"),
            stars =
                listOf(
                    StarFixture(name = "Sirius", constellation = "CMa", azimuthDeg = 246.19342, altitudeDeg = 44.88799),
                    StarFixture(name = "Canopus", constellation = "Car", azimuthDeg = 209.56678, altitudeDeg = 24.0748),
                    StarFixture(
                        name = "Arcturus",
                        constellation = "Boo",
                        azimuthDeg = 69.77693,
                        altitudeDeg = 18.82526,
                    ),
                    StarFixture(name = "Capella", constellation = "Aur", azimuthDeg = 318.97784, altitudeDeg = 17.2803),
                    StarFixture(name = "Rigel", constellation = "Ori", azimuthDeg = 261.09368, altitudeDeg = 24.54678),
                    StarFixture(
                        name = "Procyon",
                        constellation = "CMi",
                        azimuthDeg = 280.84914,
                        altitudeDeg = 60.53435,
                    ),
                    StarFixture(
                        name = "Betelgeuse",
                        constellation = "Ori",
                        azimuthDeg = 279.14017,
                        altitudeDeg = 34.61205,
                    ),
                    StarFixture(name = "Acrux", constellation = "Cru", azimuthDeg = 161.06155, altitudeDeg = 19.4931),
                    StarFixture(name = "Spica", constellation = "Vir", azimuthDeg = 103.22738, altitudeDeg = 31.90851),
                    StarFixture(name = "Pollux", constellation = "Gem", azimuthDeg = 319.16715, altitudeDeg = 51.47772),
                    StarFixture(name = "Mimosa", constellation = "Cru", azimuthDeg = 156.57055, altitudeDeg = 19.80782),
                    StarFixture(name = "Regulus", constellation = "Leo", azimuthDeg = 34.00417, altitudeDeg = 75.46016),
                    StarFixture(name = "Adhara", constellation = "CMa", azimuthDeg = 228.90969, altitudeDeg = 42.73944),
                    StarFixture(name = "Castor", constellation = "Gem", azimuthDeg = 321.16611, altitudeDeg = 47.16808),
                    StarFixture(name = "Gacrux", constellation = "Cru", azimuthDeg = 155.91405, altitudeDeg = 23.10885),
                    StarFixture(
                        name = "Bellatrix",
                        constellation = "Ori",
                        azimuthDeg = 277.2642,
                        altitudeDeg = 27.26172,
                    ),
                ),
        ),
        StarScenario(
            site = SVALBARD,
            instant = Instant.parse("2026-12-21T18:00:00Z"),
            stars =
                listOf(
                    StarFixture(name = "Vega", constellation = "Lyr", azimuthDeg = 285.48553, altitudeDeg = 36.56822),
                    StarFixture(
                        name = "Capella",
                        constellation = "Aur",
                        azimuthDeg = 103.90562,
                        altitudeDeg = 50.12256,
                    ),
                    StarFixture(
                        name = "Aldebaran",
                        constellation = "Tau",
                        azimuthDeg = 123.07526,
                        altitudeDeg = 23.34869,
                    ),
                    StarFixture(name = "Pollux", constellation = "Gem", azimuthDeg = 73.47625, altitudeDeg = 25.19937),
                    StarFixture(name = "Deneb", constellation = "Cyg", azimuthDeg = 257.54556, altitudeDeg = 49.17078),
                    StarFixture(name = "Castor", constellation = "Gem", azimuthDeg = 75.01836, altitudeDeg = 29.48372),
                ),
        ),
        // Achernar and Fomalhaut are far southern stars, and neither is above the 15-degree floor
        // this table keeps at any of its other instants. Both culminate from the Cape, which
        // covers them at once.
        StarScenario(
            site = CAPE_TOWN,
            instant = Instant.parse("2026-09-15T23:00:00Z"),
            stars =
                listOf(
                    StarFixture(
                        name = "Achernar",
                        constellation = "Eri",
                        azimuthDeg = 150.4347,
                        altitudeDeg = 60.76058,
                    ),
                    StarFixture(
                        name = "Fomalhaut",
                        constellation = "PsA",
                        azimuthDeg = 287.41707,
                        altitudeDeg = 77.65736,
                    ),
                ),
        ),
    )

// Solar events: Horizons crossings at the standard altitudes, with the USNO
// published values alongside. Null means the source reports no such event.
internal val eventFixtures: List<EventFixture> =
    listOf(
        EventFixture(
            site = GREENWICH,
            date = LocalDate.parse("2026-06-21"),
            sunrise = LocalTime.of(3, 42, 45),
            sunset = LocalTime.of(20, 20, 53),
            civilDawn = LocalTime.of(2, 55, 3),
            civilDusk = LocalTime.of(21, 8, 35),
            nauticalDawn = LocalTime.of(1, 40, 36),
            nauticalDusk = LocalTime.of(22, 23, 1),
            astronomicalDawn = null,
            astronomicalDusk = null,
            usnoSunrise = LocalTime.of(3, 43),
            usnoSunset = LocalTime.of(20, 21),
            usnoCivilDawn = LocalTime.of(2, 55),
            usnoCivilDusk = LocalTime.of(21, 9),
            usnoPolarNote = null,
        ),
        EventFixture(
            site = GREENWICH,
            date = LocalDate.parse("2026-12-21"),
            sunrise = LocalTime.of(8, 3, 5),
            sunset = LocalTime.of(15, 53, 2),
            civilDawn = LocalTime.of(7, 22, 47),
            civilDusk = LocalTime.of(16, 33, 20),
            nauticalDawn = LocalTime.of(6, 39, 37),
            nauticalDusk = LocalTime.of(17, 16, 31),
            astronomicalDawn = LocalTime.of(5, 58, 49),
            astronomicalDusk = LocalTime.of(17, 57, 18),
            usnoSunrise = LocalTime.of(8, 3),
            usnoSunset = LocalTime.of(15, 53),
            usnoCivilDawn = LocalTime.of(7, 23),
            usnoCivilDusk = LocalTime.of(16, 33),
            usnoPolarNote = null,
        ),
        EventFixture(
            site = CAPE_TOWN,
            date = LocalDate.parse("2026-06-21"),
            sunrise = LocalTime.of(5, 51, 20),
            sunset = LocalTime.of(15, 44, 54),
            civilDawn = LocalTime.of(5, 23, 35),
            civilDusk = LocalTime.of(16, 12, 39),
            nauticalDawn = LocalTime.of(4, 52, 18),
            nauticalDusk = LocalTime.of(16, 43, 55),
            astronomicalDawn = LocalTime.of(4, 21, 48),
            astronomicalDusk = LocalTime.of(17, 14, 25),
            usnoSunrise = LocalTime.of(5, 51),
            usnoSunset = LocalTime.of(15, 45),
            usnoCivilDawn = LocalTime.of(5, 24),
            usnoCivilDusk = LocalTime.of(16, 13),
            usnoPolarNote = null,
        ),
        EventFixture(
            site = CAPE_TOWN,
            date = LocalDate.parse("2026-03-20"),
            sunrise = LocalTime.of(4, 49, 23),
            sunset = LocalTime.of(16, 57, 35),
            civilDawn = LocalTime.of(4, 24, 26),
            civilDusk = LocalTime.of(17, 22, 30),
            nauticalDawn = LocalTime.of(3, 55, 19),
            nauticalDusk = LocalTime.of(17, 51, 33),
            astronomicalDawn = LocalTime.of(3, 25, 51),
            astronomicalDusk = LocalTime.of(18, 20, 56),
            usnoSunrise = LocalTime.of(4, 49),
            usnoSunset = LocalTime.of(16, 58),
            usnoCivilDawn = LocalTime.of(4, 24),
            usnoCivilDusk = LocalTime.of(17, 22),
            usnoPolarNote = null,
        ),
        EventFixture(
            site = QUITO,
            date = LocalDate.parse("2026-03-20"),
            sunrise = LocalTime.of(11, 17, 59),
            sunset = LocalTime.of(23, 24, 29),
            civilDawn = LocalTime.of(10, 57, 20),
            civilDusk = LocalTime.of(23, 45, 9),
            nauticalDawn = LocalTime.of(10, 33, 20),
            nauticalDusk = LocalTime.of(0, 9, 27),
            astronomicalDawn = LocalTime.of(10, 9, 20),
            astronomicalDusk = LocalTime.of(0, 33, 26),
            usnoSunrise = LocalTime.of(11, 18),
            usnoSunset = LocalTime.of(23, 24),
            usnoCivilDawn = LocalTime.of(10, 57),
            usnoCivilDusk = LocalTime.of(23, 45),
            usnoPolarNote = null,
        ),
        EventFixture(
            site = SVALBARD,
            date = LocalDate.parse("2026-06-21"),
            sunrise = null,
            sunset = null,
            civilDawn = null,
            civilDusk = null,
            nauticalDawn = null,
            nauticalDusk = null,
            astronomicalDawn = null,
            astronomicalDusk = null,
            usnoSunrise = null,
            usnoSunset = null,
            usnoCivilDawn = null,
            usnoCivilDusk = null,
            usnoPolarNote = "Object continuously above the Horizon; Object continuously above the Twilight Limit",
        ),
        EventFixture(
            site = SVALBARD,
            date = LocalDate.parse("2026-12-21"),
            sunrise = null,
            sunset = null,
            civilDawn = null,
            civilDusk = null,
            nauticalDawn = LocalTime.of(9, 58, 7),
            nauticalDusk = LocalTime.of(11, 52, 44),
            astronomicalDawn = LocalTime.of(6, 36, 59),
            astronomicalDusk = LocalTime.of(15, 13, 52),
            usnoSunrise = null,
            usnoSunset = null,
            usnoCivilDawn = null,
            usnoCivilDusk = null,
            usnoPolarNote = "Object continuously below the Horizon; Object continuously below the Twilight Limit",
        ),
        EventFixture(
            site = MCMURDO,
            date = LocalDate.parse("2026-12-21"),
            sunrise = null,
            sunset = null,
            civilDawn = null,
            civilDusk = null,
            nauticalDawn = null,
            nauticalDusk = null,
            astronomicalDawn = null,
            astronomicalDusk = null,
            usnoSunrise = null,
            usnoSunset = null,
            usnoCivilDawn = null,
            usnoCivilDusk = null,
            usnoPolarNote = "Object continuously above the Horizon; Object continuously above the Twilight Limit",
        ),
    )

// Each star at the Hipparcos observing epoch, J1991.25 (CDS I/239/hip_main,
// columns RAICRS / DEICRS), which is 8.75 Julian years before the J2000 place.
internal val catalogEpochFixtures: List<CatalogEpochFixture> =
    listOf(
        CatalogEpochFixture(name = "Sirius", rightAscensionDeg = 101.28854105, declinationDeg = -16.71314306),
        CatalogEpochFixture(name = "Canopus", rightAscensionDeg = 95.98787763, declinationDeg = -52.69571799),
        CatalogEpochFixture(name = "Arcturus", rightAscensionDeg = 213.91811403, declinationDeg = 19.18726997),
        CatalogEpochFixture(name = "Rigil Kentaurus", rightAscensionDeg = 219.92041034, declinationDeg = -60.83514707),
        CatalogEpochFixture(name = "Vega", rightAscensionDeg = 279.23410832, declinationDeg = 38.78299311),
        CatalogEpochFixture(name = "Capella", rightAscensionDeg = 79.17206517, declinationDeg = 45.99902927),
        CatalogEpochFixture(name = "Rigel", rightAscensionDeg = 78.63446353, declinationDeg = -8.20163919),
        CatalogEpochFixture(name = "Procyon", rightAscensionDeg = 114.82724194, declinationDeg = 5.22750767),
        CatalogEpochFixture(name = "Achernar", rightAscensionDeg = 24.42813204, declinationDeg = -57.23666007),
        CatalogEpochFixture(name = "Betelgeuse", rightAscensionDeg = 88.79287161, declinationDeg = 7.40703634),
        CatalogEpochFixture(name = "Hadar", rightAscensionDeg = 210.95601898, declinationDeg = -60.3729784),
        CatalogEpochFixture(name = "Altair", rightAscensionDeg = 297.6945086, declinationDeg = 8.86738491),
        CatalogEpochFixture(name = "Acrux", rightAscensionDeg = 186.64975585, declinationDeg = -63.09905586),
        CatalogEpochFixture(name = "Aldebaran", rightAscensionDeg = 68.98000195, declinationDeg = 16.50976164),
        CatalogEpochFixture(name = "Spica", rightAscensionDeg = 201.2983523, declinationDeg = -11.16124491),
        CatalogEpochFixture(name = "Antares", rightAscensionDeg = 247.35194804, declinationDeg = -26.43194608),
        CatalogEpochFixture(name = "Pollux", rightAscensionDeg = 116.33068263, declinationDeg = 28.02631031),
        CatalogEpochFixture(name = "Fomalhaut", rightAscensionDeg = 344.41177323, declinationDeg = -29.62183701),
        CatalogEpochFixture(name = "Mimosa", rightAscensionDeg = 191.93049537, declinationDeg = -59.68873246),
        CatalogEpochFixture(name = "Deneb", rightAscensionDeg = 310.3579727, declinationDeg = 45.28033423),
        CatalogEpochFixture(name = "Regulus", rightAscensionDeg = 152.09358075, declinationDeg = 11.96719513),
        CatalogEpochFixture(name = "Adhara", rightAscensionDeg = 104.65644451, declinationDeg = -28.97208931),
        CatalogEpochFixture(name = "Castor", rightAscensionDeg = 113.65001898, declinationDeg = 31.88863645),
        CatalogEpochFixture(name = "Gacrux", rightAscensionDeg = 187.79137202, declinationDeg = -57.11256922),
        CatalogEpochFixture(name = "Shaula", rightAscensionDeg = 263.40219373, declinationDeg = -37.10374835),
        CatalogEpochFixture(name = "Bellatrix", rightAscensionDeg = 81.28278416, declinationDeg = 6.34973451),
    )

// Lunar phase instants published by USNO (aa.usno.navy.mil/api/moon/phases/year). The two
// quarter instants were added 2026-10-04 from the same source: an elongation error that
// vanishes at new and full — a swapped sign, for one — still moves the quarters, so a table
// holding only those two phases cannot tell the waxing side from the waning one.
internal val lunarPhaseFixtures: List<LunarPhaseFixture> =
    listOf(
        LunarPhaseFixture(
            instant = Instant.parse("2026-01-03T10:03:00Z"),
            phase = "Full Moon",
            phaseLongitudeDeg = 180.0,
        ),
        LunarPhaseFixture(
            instant = Instant.parse("2026-01-10T15:48:00Z"),
            phase = "Last Quarter",
            phaseLongitudeDeg = 270.0,
        ),
        LunarPhaseFixture(
            instant = Instant.parse("2026-01-18T19:52:00Z"),
            phase = "New Moon",
            phaseLongitudeDeg = 0.0,
        ),
        LunarPhaseFixture(
            instant = Instant.parse("2026-01-26T04:47:00Z"),
            phase = "First Quarter",
            phaseLongitudeDeg = 90.0,
        ),
        LunarPhaseFixture(
            instant = Instant.parse("2026-08-12T17:37:00Z"),
            phase = "New Moon",
            phaseLongitudeDeg = 0.0,
        ),
        LunarPhaseFixture(
            instant = Instant.parse("2026-09-26T16:49:00Z"),
            phase = "Full Moon",
            phaseLongitudeDeg = 180.0,
        ),
    )

// The Sun's constellation from the IAU boundaries at the USNO season instants
// (aa.usno.navy.mil/api/seasons) and one instant inside the Sun's Taurus interval:
// March equinox, ecliptic longitude 0; the Sun's Taurus span, May 14 to June 21;
// September equinox, ecliptic longitude 180; December solstice, ecliptic longitude 270.
internal val sunConstellationFixtures: List<SunConstellationFixture> =
    listOf(
        SunConstellationFixture(
            instant = Instant.parse("2026-03-20T14:46:00Z"),
            site = GREENWICH,
            constellation = "Psc",
        ),
        SunConstellationFixture(
            instant = Instant.parse("2026-06-05T12:00:00Z"),
            site = GREENWICH,
            constellation = "Tau",
        ),
        SunConstellationFixture(
            instant = Instant.parse("2026-09-23T00:05:00Z"),
            site = GREENWICH,
            constellation = "Vir",
        ),
        SunConstellationFixture(
            instant = Instant.parse("2026-12-21T20:50:00Z"),
            site = GREENWICH,
            constellation = "Sgr",
        ),
    )
