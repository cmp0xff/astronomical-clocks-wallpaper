package io.github.godaniya.astronomicalclockswallpaper

/**
 * Geometric reference frame for the dial, independent of Android and the ephemeris engine.
 *
 * [localSiderealAngleDeg] is local apparent sidereal time in degrees, east-positive longitude
 * added to Greenwich apparent sidereal time, normalized to `[0, 360)`. It measures the local
 * meridian against the true equinox of date. [trueObliquityDeg] is the angle between the true
 * equator and ecliptic of date, including nutation. [latitudeDeg] is the observer's geographic
 * latitude, positive north. [sunLongitudeDeg] is the Sun's geometric ecliptic longitude in the
 * true ecliptic and equinox of date, in degrees, normalized to `[0, 360)`, or `null` when it has
 * not been calculated. It carries precession and nutation but neither annual aberration nor light
 * deflection, and it is the longitude the engine's `sunPosition` returns. These reference angles
 * are unrefracted, and a null [sunLongitudeDeg] suppresses the marker rather than inventing a
 * position.
 */
internal data class DialGeometry(
    val localSiderealAngleDeg: Double,
    val trueObliquityDeg: Double,
    val latitudeDeg: Double,
    val sunLongitudeDeg: Double? = null,
) {
    init {
        require(localSiderealAngleDeg >= 0.0 && localSiderealAngleDeg < FULL_TURN_DEGREES) {
            "local sidereal angle must be in [0, 360)"
        }
        require(trueObliquityDeg > 0.0 && trueObliquityDeg < RIGHT_ANGLE_DEGREES) {
            "true obliquity must be in (0, 90)"
        }
        require(ObservingLocation.isValidLatitude(latitudeDeg)) { "latitude must be in [-90, 90]" }
        sunLongitudeDeg?.let {
            require(it >= 0.0 && it < FULL_TURN_DEGREES) { "sun longitude must be in [0, 360)" }
        }
    }
}
