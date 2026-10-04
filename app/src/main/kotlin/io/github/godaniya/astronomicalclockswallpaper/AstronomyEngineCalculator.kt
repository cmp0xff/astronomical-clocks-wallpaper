package io.github.godaniya.astronomicalclockswallpaper

import io.github.cosinekitty.astronomy.Aberration
import io.github.cosinekitty.astronomy.Body
import io.github.cosinekitty.astronomy.Direction
import io.github.cosinekitty.astronomy.EquatorEpoch
import io.github.cosinekitty.astronomy.Observer
import io.github.cosinekitty.astronomy.Refraction
import io.github.cosinekitty.astronomy.Time
import io.github.cosinekitty.astronomy.Topocentric
import io.github.cosinekitty.astronomy.Vector
import io.github.cosinekitty.astronomy.constellation
import io.github.cosinekitty.astronomy.equator
import io.github.cosinekitty.astronomy.horizon
import io.github.cosinekitty.astronomy.illumination
import io.github.cosinekitty.astronomy.moonPhase
import io.github.cosinekitty.astronomy.rotationEctEqd
import io.github.cosinekitty.astronomy.rotationEqjHor
import io.github.cosinekitty.astronomy.searchAltitude
import io.github.cosinekitty.astronomy.searchRiseSet
import io.github.cosinekitty.astronomy.siderealTime
import io.github.cosinekitty.astronomy.sunPosition
import java.time.Instant
import java.time.temporal.ChronoUnit
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin

/**
 * The production [AstronomyCalculator], backed by Astronomy Engine.
 *
 * Every Solar System body is reduced the same way: the engine gives topocentric equatorial
 * coordinates of date with aberration corrected, [horizon] turns those into azimuth and altitude,
 * and the altitude is adjusted for the standard atmosphere with [Refraction.Normal]. Fixed stars
 * cannot go through [equator], because they are not Solar System bodies, so [StarCatalog] supplies
 * their J2000 place and proper motion and [rotationEqjHor] carries it to the horizon. See
 * `docs/astronomy.md` for frames, units, and tolerances.
 *
 * Three small conventions recur below.
 *
 * - An [Instant] becomes an engine [Time] through `fromMillisecondsSince1970`, which takes whole
 *   milliseconds and so drops the sub-millisecond part. Nothing here is sensitive to less than a
 *   millisecond. `Instant.toEpochMilli` throws `ArithmeticException` outside the range of epoch
 *   milliseconds a `long` holds, which the contract on [AstronomyCalculator] records.
 * - A computed azimuth is folded into `0..360` with `mod`. The engine already normalises azimuth
 *   into that range, so the fold is a guard rather than a correction: `(-0.0).mod(360.0)` is
 *   still `-0.0`, and [Horizontal] accepts that because `-0.0` is the same bearing as `0.0`.
 * - The observer sits at zero height above the ellipsoid. The observing location is a coarse
 *   position, so the difference a few metres of altitude makes is far below the tolerances here.
 */
internal class AstronomyEngineCalculator : AstronomyCalculator {
    override fun dialGeometry(time: Instant, location: ObservingLocation): DialGeometry {
        val engineTime = Time.fromMillisecondsSince1970(time.toEpochMilli())
        // Longitude 90 degrees on the true ecliptic becomes (0, cos(epsilon), sin(epsilon))
        // in the true equatorial frame. Use the public rotation API to recover true obliquity.
        val eclipticAxis = Vector(x = 0.0, y = 1.0, z = 0.0, t = engineTime)
        val equatorialAxis = rotationEctEqd(engineTime).rotate(eclipticAxis)
        val sunEcliptic = sunPosition(engineTime)
        return DialGeometry(
            localSiderealAngleDeg =
                (siderealTime(engineTime) * DEGREES_PER_HOUR + location.longitude).mod(FULL_TURN_DEGREES),
            trueObliquityDeg = Math.toDegrees(atan2(y = equatorialAxis.z, x = equatorialAxis.y)),
            latitudeDeg = location.latitude,
            sunLongitudeDeg = sunEcliptic.elon.mod(FULL_TURN_DEGREES),
        )
    }

    override fun sky(time: Instant, location: ObservingLocation): Sky {
        val engineTime = Time.fromMillisecondsSince1970(time.toEpochMilli())
        val observer = Observer(latitude = location.latitude, longitude = location.longitude, height = 0.0)
        return Sky(
            sun = sunState(engineTime, observer),
            moon = moonState(engineTime, observer),
            planets = Planet.entries.map { planetState(it, engineTime, observer) },
            stars = starStates(engineTime, observer),
            events = EventKind.entries.map { RiseSetEvent(kind = it, time = eventInstant(it, time, observer)) },
        )
    }

    private fun sunState(time: Time, observer: Observer): SolarState {
        val position = horizontalPosition(body = Body.Sun, time = time, observer = observer)
        val constellation = j2000ConstellationSymbol(body = Body.Sun, time = time, observer = observer)
        return SolarState(position = position, constellation = constellation)
    }

    private fun moonState(time: Time, observer: Observer): MoonState {
        val light = illumination(body = Body.Moon, time = time)
        return MoonState(
            position = horizontalPosition(body = Body.Moon, time = time, observer = observer),
            phaseLongitudeDeg = moonPhase(time),
            phaseFraction = light.phaseFraction,
            phaseAngleDeg = light.phaseAngle,
            magnitude = light.mag,
        )
    }

    private fun planetState(planet: Planet, time: Time, observer: Observer): PlanetState {
        val position = horizontalPosition(body = planet.body, time = time, observer = observer)
        val magnitude = illumination(body = planet.body, time = time).mag
        return PlanetState(planet = planet, position = position, magnitude = magnitude)
    }

    private fun starStates(time: Time, observer: Observer): List<StarState> {
        // One rotation matrix serves every star, and building it is the expensive part.
        val toHorizon = rotationEqjHor(time, observer)
        val yearsSinceJ2000 = time.ut / DAYS_PER_JULIAN_YEAR
        return StarCatalog.stars.map { star ->
            val raDeg = star.rightAscensionDegAfter(years = yearsSinceJ2000)
            val decDeg = star.declinationDegAfter(years = yearsSinceJ2000)
            val j2000 =
                j2000UnitVectorFromRaDec(
                    rightAscensionDeg = raDeg,
                    declinationDeg = decDeg,
                    time = time,
                )
            val horizontal =
                toHorizon
                    .rotate(j2000)
                    .toHorizontal(Refraction.Normal)
            StarState(
                name = star.name,
                // `constellation` and `horizon` both take right ascension in sidereal hours, so
                // the catalogue's degrees are converted on the way in.
                constellation = constellation(ra = raDeg / DEGREES_PER_HOUR, dec = decDeg).symbol,
                position =
                    Horizontal(
                        azimuthDeg = horizontal.lon.mod(FULL_TURN_DEGREES),
                        altitudeDeg = horizontal.lat,
                    ),
                magnitude = star.magnitude,
            )
        }
    }

    private fun horizontalPosition(body: Body, time: Time, observer: Observer): Horizontal {
        val ofDate =
            equator(
                body = body,
                time = time,
                observer = observer,
                equdate = EquatorEpoch.OfDate,
                aberration = Aberration.Corrected,
            )
        val topocentric: Topocentric =
            horizon(
                time = time,
                observer = observer,
                ra = ofDate.ra,
                dec = ofDate.dec,
                refraction = Refraction.Normal,
            )
        return Horizontal(
            azimuthDeg = topocentric.azimuth.mod(FULL_TURN_DEGREES),
            altitudeDeg = topocentric.altitude,
        )
    }

    private fun j2000ConstellationSymbol(body: Body, time: Time, observer: Observer): String {
        // The IAU boundaries are tabulated at B1875, and the engine's `constellation` takes a
        // J2000 position and precesses it there itself. Feeding it the of-date position used for
        // the horizon would add precession since 2000 — already about a third of a degree, enough
        // to put a body near a boundary in the wrong constellation.
        val j2000 =
            equator(
                body = body,
                time = time,
                observer = observer,
                equdate = EquatorEpoch.J2000,
                aberration = Aberration.Corrected,
            )
        return constellation(ra = j2000.ra, dec = j2000.dec).symbol
    }

    private fun eventInstant(kind: EventKind, time: Instant, observer: Observer): Instant? {
        val dayStart = Time.fromMillisecondsSince1970(time.truncatedTo(ChronoUnit.DAYS).toEpochMilli())
        val direction = if (kind.isRising) Direction.Rise else Direction.Set
        val centerAltitude = kind.centerAltitudeDeg
        // `searchRiseSet` adds the Sun's angular radius to the -34-arcminute horizon refraction,
        // so the upper limb crosses while the centre sits near -50 arcminutes. A twilight is
        // instead defined by the airless centre, so it searches for that altitude directly.
        val found =
            if (centerAltitude == null) {
                searchRiseSet(
                    body = Body.Sun,
                    observer = observer,
                    direction = direction,
                    startTime = dayStart,
                    limitDays = EVENT_WINDOW_DAYS,
                )
            } else {
                searchAltitude(
                    body = Body.Sun,
                    observer = observer,
                    direction = direction,
                    startTime = dayStart,
                    limitDays = EVENT_WINDOW_DAYS,
                    altitude = centerAltitude,
                )
            }
        return found?.let { Instant.ofEpochMilli(it.toMillisecondsSince1970()) }
    }

    private fun j2000UnitVectorFromRaDec(rightAscensionDeg: Double, declinationDeg: Double, time: Time): Vector {
        // The result is a unit vector in the J2000 mean equator and equinox frame (the engine's
        // EQJ). Right ascension and declination already carry the proper motion, so the vector
        // does not depend on the instant; `t` is only the label the engine's Vector carries.
        // Dating the vector is the rotationEqjHor matrix applied to it afterwards.
        val ra = Math.toRadians(rightAscensionDeg)
        val dec = Math.toRadians(declinationDeg)
        return Vector(x = cos(dec) * cos(ra), y = cos(dec) * sin(ra), z = sin(dec), t = time)
    }
}

private const val EVENT_WINDOW_DAYS = 1.0
private const val DEGREES_PER_HOUR = 15.0
private const val DAYS_PER_JULIAN_YEAR = 365.25
