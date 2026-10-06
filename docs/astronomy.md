# Astronomy calculations

[Astronomy Calculator](../app/src/main/kotlin/io/github/godaniya/astronomicalclockswallpaper/AstronomyCalculator.kt)
answers one question: *what does the sky look like from a saved observing location at a given
instant?* It implements part of #4: the Sun, the Moon with its phase, the seven planets visible
from Earth, a bundled set of bright stars, and the day's sunrise, sunset, and twilights. It also
supplies local apparent sidereal angle and true obliquity for the [Orloj dial foundation](orloj.md).
The existing `Sky` contract and UTC event window remain unchanged.

| File | Role |
| --- | --- |
| `SkyState.kt` | The result types: `Sky`, `Horizontal`, per-body state, `EventKind`, `RiseSetEvent` |
| `AstronomyCalculator.kt` | The interface, and the time and event-window contract |
| `AstronomyEngineCalculator.kt` | The implementation, backed by Astronomy Engine |
| `DialGeometry.kt` | Sidereal angle, true obliquity of date, saved observer latitude, and the Sun's and Moon's ecliptic longitudes |
| `StarCatalog.kt` | The bundled Hipparcos bright stars and their proper-motion arithmetic |

Every one of them is free of `android.*` imports. Combined with `java.time` being available
natively at the API 26 minimum, that is what lets the tests in
`app/src/test/kotlin/.../AstronomyEngineCalculatorTest.kt` and `...StarTest.kt` run as plain
JUnit with no Robolectric environment.

## Units

| Quantity | Unit |
| --- | --- |
| Azimuth, altitude, phase angle, phase longitude, constellation boundaries | degrees |
| Right ascension | sidereal hours — only inside the engine boundary, never in `Sky` |
| Magnitude | Johnson V, as the engine's or the catalogue's model reports it |
| Distance | astronomical units, internal to Astronomy Engine; not exposed |
| Time | `java.time.Instant`, UTC, millisecond resolution |

`Instant` carries no zone, and nothing under `AstronomyCalculator` reads a `ZoneId`.
Converting an instant to civil time is the caller's job. The
[product contract](design.md) requires the selected site's coordinates for astronomy
and its geographic timezone, including DST, for civil time. Both displays use the
same instant. A site change updates both; a phone-timezone change changes neither
the saved site nor its civil clock. With no selected site, only the civil clock is
shown, using the phone timezone.

Prague Orloj is the visual and projection reference, with geometry adapted to the
selected observing site. Projection mathematics belong in the [Orloj guide](orloj.md).

### Current integration limits

The wallpaper on `main` still reads the phone's civil time and does not render these
astronomy results. [PR #29](https://github.com/godaniya/astronomical-clocks-wallpaper/pull/29)
adds saved-timezone infrastructure, but current-location and raw-coordinate saves
capture the phone zone; migration also tags legacy coordinates with the phone zone.
That does not establish the site's geographic timezone.
[Issue #24](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/24) owns the remaining
gap across current location, raw coordinates, city selection, and correction of
previously phone-tagged records. The offline city chooser is
[issue #21](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/21).

## Coordinate frames

| Frame | Epoch | Where it appears |
| --- | --- | --- |
| Horizontal (HOR) | — | `Horizontal.azimuthDeg`, `altitudeDeg`: clockwise from north, up from the mathematical horizon |
| Equatorial of date (EQD) | Date and time of the observation | The intermediate step for the Sun, Moon, and planets |
| Equatorial J2000 (EQJ) | J2000.0 | The star catalogue's own frame, and the frame a constellation lookup is made from |
| Equatorial B1875 | B1875.0 | Inside the engine: the IAU constellation boundaries are tabulated there |
| Ecliptic of date | Date and time of the observation | `MoonState.phaseLongitudeDeg`, the Moon's ecliptic longitude less the Sun's |

In `Sky`, the ecliptic frame appears in the phase longitude, the Moon's elongation from the Sun
along the ecliptic. Every other angle in `Sky` is horizontal or equatorial. The separate
`DialGeometry` result adds true obliquity from the ecliptic-to-equatorial rotation of date
for the projected zodiac; the reference grid does not reuse refracted body positions. Its Sun and
Moon longitudes are the frame the dial plots, not the sky it shows: both are **geometric** ecliptic
longitudes in the true ecliptic and equinox of date, so the topocentric, aberration-corrected and
refracted positions above are not what the markers ride. See [orloj.md](orloj.md) for the dial's
own frame, and `DialGeometryFixture.kt` for the independent reference angles and tolerances.

Positions of the Sun, Moon, and planets are computed with `Aberration.Corrected` at
`EquatorEpoch.OfDate`, then converted with `Refraction.Normal`. This is the standard topocentric
path: aberration matters because the light takes time to arrive, and of-date coordinates are what
a horizon conversion needs.

**Constellation labels are resolved from J2000, not of date.** The IAU boundaries are tabulated at
B1875, and the engine's `constellation` takes a J2000 position and precesses it there itself.
Feeding it of-date coordinates would add precession since 2000 — about a third of a degree, enough
to land on the wrong side of a boundary for a body near one. `Sky` carries the three-letter IAU
abbreviation (`Tau`, `CMa`), not the full name, because that is what star charts and dials print;
the full name is a presentation choice for #5.

## Fixed stars

Stars are not Solar System bodies, so they cannot go through the planetary path. `StarCatalog`
carries each star's Hipparcos place at epoch J2000 and its proper motion, and the reduction is:

1. Advance the catalogue place linearly in time: `alpha = alpha_0 + mu_alpha * t` and
   `delta = delta_0 + mu_delta * t`, with `t` in Julian years since J2000.
2. Build the unit vector in J2000 mean equator and equinox (EQJ).
3. Rotate it to the horizontal frame with `rotationEqjHor` — precession, nutation, and Earth
   rotation in one public engine call — and convert with `Refraction.Normal`.

The catalogue's `pmRA` column is `mu_alpha * cos(delta)`, so it is divided by `cos(delta)` before
it is added to a right ascension. Skipping that would understate the drift of every star away
from the equator, by a factor of two for Rigil Kentaurus at declination -60 degrees. The
`properMotionReproducesEpoch` test pins this: it steps each star's J2000 place back 8.75 Julian
years and requires the result to match the catalogue's own published J1991.25 place.

The catalogue is the result of the Hipparcos query recorded in
[dependencies.md](dependencies.md): every main-catalogue entry with `Vmag < 1.65`, which is 27
rows, less Alpha Centauri B (HIP 71681) — 26 stars. Alpha Centauri B trails Rigil Kentaurus by
about fifteen arcseconds and would draw two labels on one point of the dial. Re-running that query is what
establishes the set; the suite pins the 26 recorded rows and cannot re-derive them offline, so the
query is recorded rather than only its result. The cut itself is a rendering choice rather than a
requirement, and it excludes Elnath (HIP 25428), which sits exactly on it.

Two deliberate simplifications apply to stars only:

- **No annual aberration.** The planetary path corrects for it; the star rotation chain above
  does not. The effect is about 20 arcseconds, 0.0056 degrees.
- **No annual parallax.** The Earth's orbit displaces the nearest star by under an arcsecond.

Both are far inside the star tolerance below, and the first is visible in the measured spread: the
star fixtures disagree with their SOFA reference by up to 0.020 degrees, this aberration among the
causes, against at most 0.005 degrees for the Sun, Moon, and planets, whose path corrects for it.

## Refraction

Positions and the horizon events use `Refraction.Normal`: the engine's Saemundsson fit,
`1.02 / tan(h + 10.3 / (h + 5.11))` arcminutes above -1 degree of altitude. It is standardised at
1010 mb and 10 °C, so a very hot or a very low-pressure day will shift a horizon event by a few
seconds. That is well inside the tolerance, and modelling actual weather is out of scope.

Two consequences are worth recording:

- The engine fades refraction toward the nadir below -1 degree of altitude — 38.8 arcminutes of
  Saemundsson there, scaled by `(h + 90) / 89` — while the Horizons reference keeps the -1 degree
  value. The two therefore diverge as a body descends: 0.32 degrees for the Sun at -44 degrees,
  the lowest fixture, and more below that. The position tests compare **azimuth only** there:
  refraction lifts altitude but does not turn bearing. Above -1 degree everything is compared.
- The observer's height above the ellipsoid is fixed at zero, approximating a sea-level observer.
  The calculator does not model terrain, elevation-dependent horizon dip, or local obstructions.
  The reference tolerances therefore do not establish accuracy for an elevated observer or the
  visible horizon at a real site.

The rise/set convention is the standard one: the Sun's *upper limb* crosses the horizon, including
the conventional 34 arcminutes of near-horizon refraction, which puts the centre about 50
arcminutes below it. The three twilights are defined by the Sun's **centre** crossing -6, -12, and
-18 degrees airlessly, which is what makes them a different engine call rather than the same one
with a different altitude. The engine uses UT1 ≈ UTC, which can be off by up to 0.9 seconds from
real UT1 — under 0.004 degrees of Earth rotation, and again inside the tolerances.

## Supported date range

Astronomy Engine targets ±1 arcminute against the USNO's NOVAS reference, and its own
documentation validates equinoxes and solstices only for 1800–2100 (within 2 minutes). The
fixtures here span 2026. This app has no requirement outside the present era, so it inherits the
engine's range rather than narrowing it, and nothing in this repository has been verified outside
2026. Very large or negative `Instant` values are not rejected; they are simply unverified. An
instant far enough out that `toEpochMilli` overflows a `long` — roughly ±292 million years — does
throw, which is the first entry in the failure contract below, and pre-1970 instants need no
special handling: the event window still starts at UTC midnight of the right day.

## Events and their window

Positions are computed at the requested instant. Solar events cannot be: they belong to a day.
Each `EventKind` reports its **first** occurrence at or after the start of the UTC day containing
the requested instant, searched up to 24 hours later. The window therefore depends on the date
but not on the time of day, and `eventTime` returns `null` when the event genuinely does not
happen — polar day, polar night, or a twilight band the Sun never reaches. A `null` is a
statement about the sky, not missing data; the dial must not substitute a guessed time.

The window is UTC, not civil, because the calculator is timezone-agnostic. An event that falls in
the next UTC day belongs to that day's window and appears in a call with an instant in it. The
`eventWindowIsTheUtcDay` test pins the contract using Quito, whose nautical dusk lands just after
midnight UTC. Any consumer displaying solar events against local civil days must query across the
relevant UTC days rather than assuming the search window coincides with the local midnight-to-midnight day.

## Failure behaviour

`sky()` catches nothing and logs nothing, and a `null` event time is always a statement about the
sky rather than a failed calculation. What it *can* throw is listed in full on
`AstronomyCalculator`; in outline, `ArithmeticException` for an instant outside the range of epoch
milliseconds, `IllegalArgumentException` from a position that fails a range check or from a
declination outside ±90 degrees, `ExceptionInInitializerError` if a bundled catalogue row is
malformed — and, on every later attempt in the same process, `NoClassDefFoundError` instead, which
is the one a field report is most likely to see — and the engine's `InternalError` when one of its
iterations or searches cannot converge. Nothing here swallows any of them, which is the letter of
the project rule; logging them is the caller's job, because this layer is deliberately free of
Android types and owns no logger. #5 owns the render loop and therefore owns what a failure does
to a frame.

When a kind is absent from a `Sky` rather than its time being `null`, `eventTime` throws rather
than answering `null` — see the KDoc on `SkyState.kt` for why the two are kept distinct.

## Accuracy against independent references

Expected values in the tests never come from Astronomy Engine. They come from the USNO and JPL
Horizons for the Sun, Moon, planets, and solar events; from the Hipparcos catalogue plus an IAU
SOFA reduction for the stars; and from published USNO lunar phases and season instants. The
fixture file records each source and the request used to obtain it, including the SOFA parameters
the star rows were reduced with. Tolerances sit well above what the two implementations actually
disagree by, so a regression fails while a rounding difference does not.

"Measured spread" records the largest observed disagreement over the committed fixtures, as
requested by #4. The ordinary reference assertions enforce the tighter regression bounds below
in the same pass that checks each position, magnitude, event, or phase. Star constellation
assertions share the horizontal-position comparisons in
`starReferencesMatch`; `properMotionReproducesEpoch` retains the
independent catalogue-epoch check. A passing run establishes these bounds, not the exact measured
figures: those observations need to be remeasured when the implementation or fixtures change.
Acceptance tolerances remain distinct from both the enforced bounds and the measured spreads.

| Quantity | Acceptance tolerance | Enforced bound | Measured spread | Reference |
| --- | --- | --- | --- | --- |
| Sun azimuth and altitude | 0.05° | 0.002° | 0.0008° | JPL Horizons, apparent and refracted |
| Planet azimuth and altitude | 0.05° | 0.008° | 0.0041° (Neptune) | JPL Horizons |
| Moon azimuth and altitude | 0.1° | 0.003° | 0.0014° | JPL Horizons |
| Star azimuth and altitude | 0.1° | 0.04° | 0.020° (Spica) | Hipparcos catalogue reduced with IAU SOFA |
| Planet and Moon magnitudes | 0.25 mag | 0.2 mag | 0.13 mag (Neptune) | JPL Horizons apparent magnitude |
| Sunrise, sunset, and twilight | 60 s | JPL 5 s; USNO 60 s | 3 s against JPL | JPL Horizons crossings, cross-checked against USNO |
| Lunar phase at a published phase instant | 0.05° of ecliptic longitude | 0.01° | 0.0052° | USNO lunar phases |
| Proper motion over the 8.75-year catalogue step | — | 3e-6° | 1.25e-6° (0.0045 arcsec, Rigil Kentaurus) | Hipparcos J1991.25 place |
| Sun constellation | exact match | exact match | — | IAU boundaries; three of the four instants are USNO season instants |

The altitude comparison is skipped for reference positions below -1 degree, for the refraction
reason above. The 60-second event tolerance sits at the tight end of the 1–2 minutes the
acceptance criteria allow, and the two implementations actually agree to within 3 seconds — part
of which is the reference's own, since the fixture crossing is interpolated from one-minute
samples.

The lunar-phase row is also the phase-wrap case: both new-moon fixtures land at an ecliptic
longitude just under 360 degrees, so the comparison has to wrap rather than subtract, and a raw
subtraction would report a 360-degree disagreement instead of the 0.0052 degrees above.

`sky()` costs about 0.2 ms once the JVM is warm, for all 26 stars and the eight event searches;
the first call costs about 15 ms while the engine's classes initialise. Measured on 2026-09-29
with Temurin 21.0.12.1 on macOS on ten cores, by warming 2000 calls and then averaging 1000. No
committed test measures it, so it is an observation rather than a bound. The wallpaper redraws
about once a second, so neither figure is a battery concern. The rotation matrix the star path
needs is already built once per call rather than once per star.

## What is not verified here

- **Nothing on a device.** These are JVM tests against published reference data. Rendering the
  layers, waking, and lock-screen behaviour are #5 and #6, and need the physical device.
- **Nothing outside 2026** — see the date range above.
- **The Moon's topocentric parallax at the horizon** is the engine's, not independently checked
  here beyond the polar day and night cases and the four published lunar-phase instants.
- **Nothing at exactly ±90 degrees latitude against an independent reference.**
  `skyIsDefinedAtBothPoles` checks valid output and selected absent solar events. It does not
  establish positional accuracy at the poles, where azimuth also needs a bearing convention.
- **Asteroid, comet, and rise/set-for-the-Moon** cases are out of scope for #4.

Run the tests with `./gradlew qualityGate`, or `./gradlew :app:testDebugUnitTest` for the tests
alone. The astronomy and model tests use plain JUnit; the complete suite also includes
Robolectric. Both Gradle commands require the configured Android SDK and JDK described in
[development.md](development.md#local-setup), and initial dependency downloads need network
access. No device is needed for these JVM tests.
