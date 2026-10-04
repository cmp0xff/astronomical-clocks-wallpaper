# Orloj dial foundation

The wallpaper uses accurate astronomical geometry with an original Canvas design inspired by
the Prague Orloj. This is the foundation for #5. It includes the civil clock, zodiac,
equator, tropics, horizon, and astronomical-night boundary. Sun rendering is implemented in #27;
Moon position and illuminated phase are implemented in #28. Other astronomy layers, display size,
position and brightness controls, calendar artwork, apostles, historical hour systems, and
mechanical approximations remain outside this slice. It does not complete all of #5.

## Astronomical frame

`AstronomyCalculator.dialGeometry(instant, location)` returns an Android-free
`DialGeometry`: local apparent sidereal angle, true obliquity of date, observer latitude, the Sun's
ecliptic longitude, the Moon's geocentric ecliptic longitude, and the Moon's phase longitude, all in
degrees. The pinned engine's `siderealTime` gives Greenwich
apparent sidereal hours;
multiplying by 15 and adding east-positive longitude gives the local angle. The public
`rotationEctEqd` rotation of the ecliptic y-axis into the true equator of date gives true obliquity.
The Sun's longitude is a **geometric** longitude in the true ecliptic and equinox of date: the
engine's `sunPosition` applies light-time retardation and the IAU 2006 precession–nutation matrix,
but not annual aberration or gravitational light deflection, so its value sits about 20.49 arcseconds
from an apparent place.

The Moon's longitude is the same kind of quantity from `eclipticGeoMoon`: geocentric, in the true
ecliptic and equinox of date, carrying precession and nutation. It is more purely geometric than the
Sun's, because that path applies no light-time retardation either, and it is geocentric rather than
topocentric, so the lunar horizontal parallax of up to about one degree is not applied. The phase
longitude comes from the engine's `moonPhase`, the Moon's ecliptic longitude less the Sun's reduced
to `[0, 360)`, so 0° is new, 90° first quarter, 180° full and 270° last quarter; it is not
bit-identical to subtracting the two longitudes above, because the engine reduces those two bodies
by a path of its own. Every one of these fields is `null` in a `DialGeometry` built without it, and
a null longitude suppresses the marker rather than inventing a position.
No engine types cross the new geometry interface. Existing body positions and UTC event windows
retain their earlier contracts; the foundation does not compute the full body/event list per frame.

## Projection

The [Astronomical Institute's Prague guide](https://astro.cas.cz/bh2010/files/praha.pdf), printed
pages 4–5 (PDF pages 4–5), describes the Prague instrument's unusual north-pole stereographic
projection. The public descriptions of the clock in the Sources section below agree on it: Cancer
is the outer sky boundary, Capricorn the inner tropic, and the equator lies between them. The
complete ecliptic is a rotating offset circle, including the part below the horizon.

The projection is taken from the celestial pole above the horizon: the north pole for a northern
site, the south pole for a southern one. For hour angle
`H = local apparent sidereal angle − right ascension` and declination `δ`:

```text
northern plate: r = tan(45° + δ/2)
southern plate: r = tan(45° − δ/2)
x = r sin(H)
y = −r cos(H)
```

Screen x increases rightward and y downward. Divide both coordinates by
`tan(45° + trueObliquity/2)` so the outer sky boundary has radius 1. On a northern plate that
boundary is Cancer (declination +obliquity) and Capricorn is the inner tropic; on a southern plate
the two swap — **Capricorn is outside and Cancer inside** — because the south-pole plate is the
radial inversion of the north-pole plate through the equator circle. The equator radius is the
inverse of the normalizing factor and the inner tropic its square on both plates. Increasing
sidereal angle rotates the projected sky clockwise. Ecliptic longitude is converted through the
true obliquity into equatorial coordinates before projection; equal longitude intervals are not
equal intervals around the offset circle. The twelve labels denote tropical zodiac signs, rather
than the unequal IAU constellations.

Each of the twelve sign boundaries carries a gold divider that runs from the dial centre outward
through its boundary point and is clipped to the ring's night band. Because the ring centre is
offset from the dial centre, the divider crosses the ring obliquely: it departs from the ring's own
radius by up to the true obliquity at the ARI/LIB equinox boundaries and coincides with it at the
CAN/CAP solstice boundaries. The `kshetline/prague-clock` simulator draws its sign boundaries the
same way, as paths from the dial origin to the ring's outer edge, masked to the band.

Because a southern plate is the point reflection of a northern one, the dial centre is always the
celestial pole below the horizon — the south pole at altitude −latitude on a northern plate, the
north pole at altitude −|latitude| on a southern one. The night disc therefore nests inside the
twilight disc in both hemispheres, as on the Prague instrument. The sky content follows the
projection rather than the shading: the zodiac ring is point-reflected and jumps by 180° as a site
crosses the equator, while the shaded regions do not change. The published descriptions are all of
the Prague instrument at 50°N; none discusses southern latitudes, so the south-pole construction —
the Capricorn/Cancer swap, the point-reflected zodiac, and the night-inside-twilight nesting — is
documented here explicitly rather than cited.

The plate is geometric and unrefracted. For raw projected coordinates before normalizing, observer
latitude magnitude `|φ|`, and `q = x² + y²`, its altitude satisfies:

```text
sin(altitude) = [sin(|φ|) (q − 1) − 2 cos(|φ|) y] / (q + 1)
```

The regions depend only on `|φ|`, so both hemispheres shade the same circles. Day is altitude ≥ 0°,
twilight is −18° ≤ altitude < 0°, and astronomical night is below −18°. These are sky regions on
the fixed plate, not a whole-screen tint based on the current Sun. Equatorial horizons are lines;
polar horizons are circles. The projected horizon and night contours are bounded to the sky disk so
nearly equatorial sites do not generate enormous Canvas coordinates. Contour samples are at most one
degree apart on the sphere; for terrestrial obliquity below 24°, their chord error is below 0.25
pixels at a 500-pixel sky radius. The plate fills these sampled contours; which side of a threshold
a point lies on is decided analytically instead. The zodiac remains complete over every plate
region.

## Clock, settings, and lifecycle

The Roman scale shows 24 civil hours: XII at the top, XXIV at the bottom, VI on the left, XVIII
on the right. One hand follows saved-site civil time including DST. Its angle is independent of
the zodiac's sidereal rotation; it is not a solar position marker.

**Zodiac ring**, **Sun**, and **Moon** default to enabled and persist across recreation. The first
controls the rotating zodiac and its labels; the second controls the radiant golden Sun marker on the
ecliptic ring together with the day/twilight/night shading and its horizon and astronomical-night
boundaries — with it off the plate degrades to a clean instrument grid, keeping the tropics, the
equator, and the outer rim. With the Sun layer on, the marker is drawn only when the geometry carries a
Sun longitude; a geometry without one (such as test or offline plate geometries constructed without a
solar position), or an absent geometry when no site is saved, shows no marker at all. The third controls
the astronomical Moon marker, drawn on the same ecliptic ring with its illuminated phase; it needs both
a lunar longitude and a phase longitude, so a geometry missing either shows no lunar marker.
The toggle was renamed from "Day and night" to "Sun" before release; a stored value under the old
`day_and_night` key is ignored rather than migrated, so the layer returns to its enabled default. Without
a saved site, layer checkboxes in Settings are disabled and only the civil clock is shown, using the
phone timezone. Settings explains that an observing location is required for sky geometry.

### Moon marker conventions

The Moon marker is a disc placed on the ecliptic ring at the Moon's geocentric longitude, so it shares
the Sun marker's ring convention: **ecliptic latitude is dropped**, and the Moon's, which reaches about
±5.1°, is not drawn. The angle around the ring is exact; the displacement off it is a convention of the
instrument. Geocentric rather than topocentric placement is also a decision, not an omission — the
lunar horizontal parallax is under a degree, and the reference instrument's Moon pointer is likewise a
geocentric place.

Waxing phases light the **right** limb and waning phases the **left** one, as seen from a northern site:
the terminator is parameterized directly by the phase longitude, so the lit fraction runs from nothing
at 0° (new) through the right half at 90° (first quarter), the full disc at 180° (full), and the left
half at 270° (last quarter). Southern sites **mirror the disc horizontally**, so the lit limb sides
swap. This is a stated screen-space convention, not a consequence of the plate inversion, which
point-reflects the sky through the dial centre: the marker keeps one orientation around the whole ring
rather than turning its bright limb to face the Sun marker, which the dial also draws. The orientation,
the phase limbs and the hemisphere flip are pinned by pixel assertions in `DialRendererTest`.

At conjunction the two markers project to the same point, and the Moon is drawn after the Sun, so the
Moon's disc — dark at new moon — covers the Sun's core while its rays remain visible. That is the
intended order, not a rendering failure: the dial draws the nearer body in front.

The projection places a body at `x = r sin H`, `y = −r cos H` for hour angle `H`, so the marker's
bearing from the dial centre is the Sun's hour angle, with noon at the top of the Roman scale. Whether
that reading is local apparent solar time, and how it is offset from the civil hand, is
[#57](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/57); this layer guarantees the
geometric bearing only.

Each engine listens for location and layer changes, maintains one immutable settings snapshot,
and draws each frame from one instant. Updates take effect on the next visible tick. Hidden
engines do not start rendering, and destroyed engines unregister both preference listeners.
Rendering stays at one frame per second while visible.

## Sources

The projection, the day/twilight/night regions, and the night circle follow these descriptions of
the Prague instrument:

- Astronomical Institute of the Czech Academy of Sciences, guide to the Old Town Hall
  astronomical clock — <https://astro.cas.cz/bh2010/files/praha.pdf>, printed pages 4–5.
- Wikipedia, *Prague astronomical clock* —
  <https://en.wikipedia.org/wiki/Prague_astronomical_clock>.
- `orloj.org` — <https://orloj.org/>.
- `orloj.cesnet.cz` — <https://orloj.cesnet.cz/>.
- *Prague* at `wijzerweb.be` — <https://wijzerweb.be/prague.html>.
- The `kshetline/prague-clock` simulator — <https://github.com/kshetline/prague-clock>.
- The `drifted.in/horologium-app` astrolabe simulator — <https://drifted.in/horologium-app/>.

All of them describe the Prague instrument at 50°N. None discusses southern latitudes; the
south-pole construction this project uses for them is documented above and is not taken from these
sources.

## Verification

The geometry reference tests use independently generated ERFA/SOFA fixtures with their generator,
time-scale conventions, and tolerances documented in
[`DialGeometryFixture.kt`](../app/src/test/kotlin/io/github/godaniya/astronomicalclockswallpaper/DialGeometryFixture.kt).
They assume UT1 = UTC and TT − UTC = 69.184 seconds; the pinned engine uses modeled DeltaT.
The comparison tolerances (0.0001° sidereal angle, 0.00003° obliquity, and 0.001° for the Sun
longitude added in #27) describe agreement with
those fixtures, not physical UT1 accuracy. Omitting measured DUT1 can shift sidereal angle by
up to 13.5 arcseconds. The Sun column's frame, generator, and the reason for its 0.001° bound are
recorded there too; its largest residual against the engine is 0.000434° (1.56″), and JPL Horizons
independently gives the same instant's geocentric Sun to within 0.374″, which is what fixes the
value as geometric and aberration-free. Analytic projection
tests cover equinoxes/solstices, circle tangencies, rotation direction, northern/southern sites,
the equator, poles, day/night classification, the southern mirror of the altitude field, and the
point-reflected southern zodiac. Robolectric tests cover layer persistence,
missing-location behavior, same-instant frame calculation, and hidden/destroyed engines.
Canvas tests inspect representative renders analytically and through targeted pixel probes. Screenshot
golden baselines are deferred because Android displays and screenshot pipelines apply device- and
firmware-dependent colour transforms and font-shaping antialiasing, so literal byte-for-byte image
comparisons produce false positives across environments (see [device-testing.md](device-testing.md)).
Representative PNGs for visual inspection are explicitly exported under `app/build/reports/orloj` with
`./gradlew exportRepresentativeImages`.

Run `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh`. Physical-device home and
lit-lock-screen correctness for the Sun marker (#27) are documented in [device-testing.md](device-testing.md);
frame cost and battery behavior remain unrun and are tracked in #6.
