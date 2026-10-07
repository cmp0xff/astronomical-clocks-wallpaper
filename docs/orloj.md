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
bearing from the dial centre is the Sun's hour angle, with noon at the top of the Roman scale.

### Sun marker, local apparent solar time, and civil offset

Measuring angles clockwise from the top vertical axis (the negative y-axis pointing to XII on the 24-hour
Roman scale), any projected ecliptic point's bearing is `atan2(x, −y) ≡ H`. For the Sun marker, whose
geometric hour angle approximates the apparent solar hour angle, the bearing is an approximation to
**Local Apparent Solar Time**. The engine uses a geocentric geometric Sun position without annual
aberration or topocentric solar parallax, so it is not an exact apparent-Sun position:

- At local apparent noon the apparent hour angle is `0°`, so the ideal marker points at XII; the
  geometric marker can differ slightly because of the omitted effects above. Independent solar-transit
  fixtures check that difference against a `0.02°` tolerance.
- As the Sun moves through the afternoon (`H > 0`), the marker advances clockwise past I, II, III...
- At midnight (`H = 180°`), the marker reaches XXIV at the bottom of the dial.

The civil hand and the Sun marker indicate two distinct, intentional physical quantities:
the civil hand tracks the saved observing site's civil timezone (including daylight saving time), while
the Sun marker approximates local apparent solar time. In hours, the predicted civil-to-apparent offset is:

```text
civil − apparent = UTC offset (incl. DST) − east longitude/15° − equation of time
```

Here, the equation of time is apparent solar time minus local mean solar time; it oscillates by
approximately ±16 minutes over the year due to Earth's orbital eccentricity and axial tilt. The Sun
marker uses the engine's geometric position, so the measured angular offset has a small additional
ephemeris/frame residual.

On the historical Prague Orloj, the Roman scale is described as reading local Prague civil time (CET),
the zodiac turns at the sidereal rate over fixed day/twilight/night regions, and the Sun sits on a
single arm with the civil hand. That reading is a mechanical inference from published descriptions of
the instrument rather than an independently verified measurement; it follows the *Prague astronomical
clock* entry in [Sources](#sources), and mechanical detail beyond those descriptions is unverified.
Prague (14.42°E) lies within 0.58° of the 15°E Central European Time meridian, so that longitude term
alone is small — about 2.3 minutes of Earth rotation — but the equation of time dominates it, swinging
the standard-time (CET, UTC+1) offset to roughly ±16 minutes over the year. At arbitrary global sites,
or under daylight saving time (such as CEST, UTC+2), the civil hand and Sun marker legitimately diverge
by an hour or more. That divergence is the astronomical reading of the instrument, not a synchronization
defect.

The day, twilight, and night altitude regions depend solely on latitude magnitude `|φ|`, and the
zodiac's sidereal rotation depends solely on UTC instant and site longitude. Neither depends on the
civil timezone or DST. Changing the saved site's civil timezone rotates the civil hand alone, keeping the
shading and astronomical markers strictly invariant.

#### Legibility aid decision

Whether to introduce a secondary solar arm, second hand, or dial tick at the Sun's bearing was
considered. In accordance with historical Orloj fidelity and visual minimalism, **no extra hand or arm is
added by default**. The radiant golden Sun marker riding the ecliptic ring already provides an unambiguous
bearing against the outer Roman numeral scale, and an extra arm would clutter the dial face.

Each engine listens for location and layer changes, maintains one immutable settings snapshot,
and draws each frame from one instant. Updates take effect on the next visible tick. Hidden
engines do not start rendering, and destroyed engines unregister both preference listeners.
Rendering stays at one frame per second while visible.

## Palette contrast

The dial is drawn from a `DialPalette`, and the dark and light palettes are a contract about contrast
rather than only taste. Every information-bearing ink - the sign names, the civil numerals, the hand,
the Sun and Moon markers, and the zodiac ring - must clear
[WCAG 2.1](https://www.w3.org/TR/WCAG21/) against the surface it is drawn on: 4.5:1 for text, 3:1 for
graphics. Contrast is the WCAG relative-luminance ratio, `(L_lighter + 0.05) / (L_darker + 0.05)`, and
`DialPaletteTest.inksClearTheirSurfaces` asserts it.

Two consequences shape the palettes.

- The **dark** palette is dark on every surface, so its bright cream and gold inks clear whatever they
  meet without help.
- The **light** palette deliberately keeps one dark surface, the night sky region, because there the
  dark tone *is* the meaning. The civil hand, the two reference circles, and the Sun and Moon markers
  with the zodiac ring disabled cross it while also crossing the pale plate. No single ink clears
  3:1 against both, so each carries a **casing** -
  an underlay stroke in the palette's page tone, drawn wider than the ink. The casing stands out over
  the dark region and disappears over the pale one, the way a map line is cased where it crosses
  varied ground.

The table is the audit the test encodes; the ratios are relative-luminance contrast against the stated
surface.

| Ink on surface | Dark | Light | Minimum |
| --- | --- | --- | --- |
| Sign names (`hand`) on the zodiac band | 12.57 | 8.87 | 4.5 |
| Civil numerals (`gold`) on the rim | 7.37 | 5.90 | 4.5 |
| Zodiac ring, dividers, and star (`gold`) on the band | 8.12 | 5.90 | 3.0 |
| Moon rim (`gold`) on the band | 8.12 | 5.90 | 3.0 |
| Moon lit limb (`moonIlluminated`) against the moon shadow | 9.40 | 4.98 | 3.0 |
| Moon shadow against the band | 1.44 (see below) | 4.24 | 3.0 |
| Hand over the night region, by ink or casing | 12.57 | 9.99 | 3.0 |
| Ring-disabled marker outline on daylight, by ink or casing | 5.52 | 5.08 | 3.0 |
| Ring-disabled marker outline on twilight, by ink or casing | 3.90 | 3.98 | 3.0 |
| Ring-disabled marker outline on night, by ink or casing | 12.57 | 9.99 | 3.0 |

With the zodiac ring disabled, both markers receive a `casing` outer stroke (0.014 sky-radius
units) and a `hand` inner stroke (0.008), before their existing fill and rim. The two-tone boundary
clears 3:1 against every plate region by at least one of those strokes; `DialPaletteTest` checks all
three regions in both palettes. `MarkerContrastTest` uses differential native Canvas probes on
API 26 and 36 to show that the strokes actually reach the night and twilight backgrounds for the
Sun and new Moon. This changes only the marker boundary when the ring is hidden, preserving positions,
phase, hemisphere conventions, and band-visible drawing.

At full Moon the light illuminated disc is close in tone to the light zodiac band. Its gold rim
still clears 5.90:1 against that band, so its boundary remains distinguishable even without shadow
in the disc. A differential native Canvas test checks this full-Moon boundary in both palettes.
The fill itself is not required to contrast with the band when its contrasting rim defines the marker.

These host checks do not establish physical-device visibility for the new ring-disabled outlines;
the physical-device pass records that visibility (see
[2026-10-04-feat-31-appearance.md](testing/reports/2026-10-04-feat-31-appearance.md#appearance-review-fixes-device-pass)).

Two accepted exceptions, both recorded here rather than worked around:

- **The dark Moon's unlit limb** is `MOON_SHADOW` at 1.44:1 against the band. The disc does not read
  by that tone but by its gold rim (8.12:1) and its lit limb (9.40:1), which is what makes it a
  sphere; `MOON_SHADOW`'s own comment records its separation from the other seven dark tones. The
  light palette's shadow needs no such exemption, because there the disc is dark on a pale band.
- **The graticule** - the tropic and equator circles - is a deliberately faint reference line drawn
  across every region, and the casing lifts most of its crossings but not all. Still below 3:1 with
  the casing applied: the dark theme's `mutedGold` on the daylight sky (1.51:1 by ink, 2.56:1 cased),
  and the light theme's `gold` on twilight (2.65:1, 2.62:1 cased), `mutedGold` on twilight (1.50:1,
  2.62:1 cased), and `mutedGold` on the daylight sky (1.92:1, 2.05:1 cased). Every other crossing
  clears the minimum by ink or by casing - `gold` on the dark twilight is 2.52:1 by ink but 3.62:1
  cased, the dark `mutedGold` on twilight 1.07:1 but 3.62:1 cased, and both graticule inks reach
  9.99:1 over the light theme's night region. Clearing the remainder would need both mid tones
  re-derived for every surface they cross, which would collapse the tonal difference between the two
  circles and lose the faint-line character the reference is drawn with. The information-bearing
  contract above is unaffected.

The `zodiacBand` split exists for the light palette's sake: the sign names sit in the band, and filling
it with the plate's pale sand tone keeps a light dial light, while the night sky region stays dark
because it is the night. The dark palette fills both with `NIGHT`, so the split leaves its rendering
unchanged.

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
value as geometric and aberration-free. Independent solar-noon fixtures from JPL Horizons validate
the Sun marker's bearing against the Roman scale at local apparent noon, and that it advances at the
15°/h apparent solar rate an hour later, across northern and southern sites under both DST and
non-DST conditions, documented in
[`ApparentSolarTimeFixture.kt`](../app/src/test/kotlin/io/github/godaniya/astronomicalclockswallpaper/ApparentSolarTimeFixture.kt).
Analytic projection
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
lit-lock-screen correctness for the Sun marker (#27) are documented in [2026-10-04-feat-27-sun-marker.md](testing/reports/2026-10-04-feat-27-sun-marker.md);
frame cost and battery behavior remain unrun and are tracked in #6.
