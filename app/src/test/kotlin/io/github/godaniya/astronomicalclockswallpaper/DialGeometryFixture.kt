package io.github.godaniya.astronomicalclockswallpaper

import java.time.Instant

/*
 * Independent reference angles generated 2026-09-30 with pyerfa 2.0.1.5 (ERFA 2.0.1,
 * SOFA 20231011), Python 3.13.15. The Sun longitude column was added 2026-10-04 with the
 * same pyerfa 2.0.1.5 release under Python 3.11.16. ERFA is derived from SOFA; no Astronomy
 * Engine result or formula was used to generate any of these values. All angles are degrees.
 *
 * Primary sources:
 * https://github.com/liberfa/erfa/blob/v2.0.1/src/gst06a.c
 * https://github.com/liberfa/erfa/blob/v2.0.1/src/obl06.c
 * https://github.com/liberfa/erfa/blob/v2.0.1/src/nut06a.c
 * https://github.com/liberfa/erfa/blob/v2.0.1/src/epv00.c
 * https://github.com/liberfa/erfa/blob/v2.0.1/src/pnm06a.c
 * https://github.com/liberfa/pyerfa/tree/v2.0.1.5
 *
 * Reproduce each timestamp's calendar fields (y, m, d, hh, mm, ss) in Python:
 *   import erfa, math
 *   utc = erfa.dtf2d("UTC", y, m, d, hh, mm, ss)
 *   ut1 = erfa.utcut1(*utc, 0.0)
 *   tt = erfa.taitt(*erfa.utctai(*utc))
 *   gast = math.degrees(erfa.gst06a(*ut1, *tt))
 *   obliquity = math.degrees(erfa.obl06(*tt) + erfa.nut06a(*tt)[1])
 *
 * GAST uses IAU 2006 precession and IAU 2000A nutation. True obliquity is IAU 2006
 * mean obliquity plus the nutation in obliquity. There is no refraction. Longitude is
 * east-positive; local apparent sidereal angle is (GAST + longitude) modulo 360.
 * UT1-UTC is explicitly zero, matching the engine's rotation approximation rather than
 * measured Earth orientation. UTC-to-TT uses the leap-second table: TT-UTC = 69.184 s
 * for these dates. The engine instead predicts Delta-T around 75.05-75.65 s here and
 * truncates its nutation series to five terms. The tests allow these model differences:
 * 0.0001 degree (0.36 arcsecond) in sidereal angle and 0.00003 degree (0.108 arcsecond)
 * in obliquity. These are comparison tolerances, not claimed accuracy against measured
 * UT1: ignoring DUT1 can itself cost up to about 13.5 arcseconds of rotation.
 *
 * sunLongitudeDeg reproduces the same quantity the engine's `sunPosition` returns, and
 * mirrors that function step for step rather than using the nearest ERFA routine:
 *   import erfa, math
 *   import numpy as np
 *   LIGHT_TIME_DAYS = 0.005775518331089121   # 1 / 173.1446326846693 AU/day (C_AUDAY)
 *   retarded = (tt[0], tt[1] - LIGHT_TIME_DAYS)
 *   pvh, pvb = erfa.epv00(*retarded)          # ICRS heliocentric Earth
 *   sun_icrs = -np.asarray(pvh[0])            # geocentric Sun
 *   eqd = erfa.pnm06a(*retarded) @ sun_icrs   # true equator and equinox of date
 *   eps = erfa.obl06(*retarded) + erfa.nut06a(*retarded)[1]
 *   ey = eqd[1]*math.cos(eps) + eqd[2]*math.sin(eps)
 *   lon = math.degrees(math.atan2(ey, eqd[0])) % 360.0
 *
 * Two choices in that recipe matter. `ecm06` is deliberately not used: it is the *mean*
 * ecliptic and equinox of date, so it drops the nutation in longitude, which the engine
 * includes. And the obliquity is evaluated at the light-time-retarded instant, because
 * `sunPosition` calls `earthTilt(adjustedTime)`; the trueObliquityDeg column above instead
 * uses the unretarded instant, so the two columns rest on marginally different epochs.
 * The frame is the *geometric* ecliptic longitude in the true ecliptic and equinox of date:
 * light-time retardation, precession, and nutation are applied, and annual aberration and
 * gravitational light deflection are not. Calling it "apparent" would overclaim by the
 * constant of aberration, about 20.49 arcseconds.
 *
 * Tolerance is set from evidence. Against the pinned engine the largest residual over the
 * seven instants is 0.000434 degree (1.56 arcsecond), on 2026-06-21T00:00:00Z, consistent
 * with the engine's five-term nutation truncation and its modeled Delta-T. The tolerance is
 * 0.001 degree (3.6 arcseconds), about 2.3 times that residual and far below both the 20.49
 * arcsecond annual aberration and the 17.2 arcsecond nutation amplitude, so a frame error, a
 * dropped nutation term, or a wrongly applied aberration term still fails the comparison.
 *
 * Second, independent source. JPL Horizons (DE441), geocentric observer, target Sun,
 * 2026-06-21T00:00:00Z, quantity 31 gives ObsEcLon = 89.6655938 degrees, against this
 * fixture's 89.6656977: a difference of 0.0001040 degree (0.374 arcsecond). Two independent
 * ephemerides and precession-nutation theories agreeing to well under an arcsecond is
 * enough to identify the terms the value does *not* contain: applying the annual-aberration
 * term to the ERFA vector moves it 0.0057 degree (20.5 arcseconds), and rotating to the mean
 * equinox of date moves it 0.0022 degree (7.8 arcseconds, the nutation in longitude), both
 * far outside the residual. Horizons' geocentric Sun place therefore excludes annual
 * aberration at this instant too, which is the second, independent confirmation that the
 * value is geometric and not apparent. The residual itself is not attributed to a single
 * term here. Query: CENTER='500@399', QUANTITIES='31', START_TIME='2026-06-21 00:00',
 * STEP_SIZE='1 m'. Its topocentric counterpart (CENTER='coord@399',
 * SITE_COORD='14.42,50.0,0') gives 89.6659068, 0.75 arcsecond away, which is solar parallax.
 *
 * The rows cover all four 2026 seasons plus adjacent instants across a UTC date boundary.
 * Times label evaluation instants; they do not claim to be exact equinox/solstice events.
 */

/** Greenwich true-of-date angles from the independent ERFA computation described above. */
internal data class DialGeometryFixture(
    val instant: Instant,
    val greenwichSiderealAngleDeg: Double,
    val trueObliquityDeg: Double,
    val sunLongitudeDeg: Double,
)

internal val geometryFixtures: List<DialGeometryFixture> =
    listOf(
        DialGeometryFixture(
            instant = Instant.parse("2026-01-01T00:00:00Z"),
            greenwichSiderealAngleDeg = 100.662223880875,
            trueObliquityDeg = 23.438137227782,
            sunLongitudeDeg = 280.568492619233,
        ),
        DialGeometryFixture(
            instant = Instant.parse("2026-03-20T14:46:00Z"),
            greenwichSiderealAngleDeg = 39.649369936616,
            trueObliquityDeg = 23.438406235100,
            sunLongitudeDeg = 0.000006215121,
        ),
        DialGeometryFixture(
            instant = Instant.parse("2026-06-21T00:00:00Z"),
            greenwichSiderealAngleDeg = 269.208537339434,
            trueObliquityDeg = 23.437975731881,
            sunLongitudeDeg = 89.665697696424,
        ),
        DialGeometryFixture(
            instant = Instant.parse("2026-09-23T00:05:00Z"),
            greenwichSiderealAngleDeg = 3.113096710875,
            trueObliquityDeg = 23.438125248960,
            sunLongitudeDeg = 179.999872297327,
        ),
        DialGeometryFixture(
            instant = Instant.parse("2026-12-21T20:50:00Z"),
            greenwichSiderealAngleDeg = 42.938088902050,
            trueObliquityDeg = 23.437637299363,
            sunLongitudeDeg = 269.999737210750,
        ),
        DialGeometryFixture(
            instant = Instant.parse("2026-03-20T23:59:59Z"),
            greenwichSiderealAngleDeg = 178.524381893801,
            trueObliquityDeg = 23.438400611018,
            sunLongitudeDeg = 0.382364988191,
        ),
        DialGeometryFixture(
            instant = Instant.parse("2026-03-21T00:00:00Z"),
            greenwichSiderealAngleDeg = 178.528559968142,
            trueObliquityDeg = 23.438400610836,
            sunLongitudeDeg = 0.382376490221,
        ),
    )
