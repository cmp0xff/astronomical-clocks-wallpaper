package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Bitmap
import android.graphics.Canvas
import android.util.Log
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowLog
import java.time.LocalTime
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.roundToInt
import kotlin.math.sin

/** Native bitmap probes anchor the 24-hour hand, geometric plate, and independent display layers. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class DialRendererTest {
    private val renderer = DialRenderer()
    private val prague = DialGeometry(localSiderealAngleDeg = 0.0, trueObliquityDeg = 23.44, latitudeDeg = 50.08)

    @Test
    fun paletteIsPinnedToLiteralArgb() {
        // The device's screenshot pipeline applies a colour transform, so a capture cannot
        // adjudicate the exact gold (docs/device-testing.md). Every other pixel assertion in this
        // class compares DialStyle to itself, so without this pin a palette edit passes the suite.
        assertEquals(0xFF101923.toInt(), DialStyle.BACKGROUND)
        assertEquals(0xFFD8B66A.toInt(), DialStyle.GOLD)
        assertEquals(0xFF887347.toInt(), DialStyle.MUTED_GOLD)
        assertEquals(0xFF1C2C39.toInt(), DialStyle.RIM)
        assertEquals(0xFF286078.toInt(), DialStyle.SKY)
        assertEquals(0xFF9C6438.toInt(), DialStyle.TWILIGHT)
        assertEquals(0xFF152433.toInt(), DialStyle.NIGHT)
        assertEquals(0xFFF4E5B8.toInt(), DialStyle.HAND)
        assertEquals(0xFFE8EEF5.toInt(), DialStyle.MOON_ILLUMINATED)
        assertEquals(0xFF2C3E50.toInt(), DialStyle.MOON_SHADOW)
    }

    @Test
    fun moonMarkerRendersOnEcliptic() {
        val geometryWithMoon = prague.copy(moonLongitudeDeg = 60.0, moonPhaseLongitudeDeg = 90.0)
        val projection = OrlojProjection(geometryWithMoon)
        val moonPoint = projection.moonPoint!!
        val enabled = render(geometry = geometryWithMoon, layers = DialLayers(isMoonEnabled = true))
        val disabled = render(geometry = geometryWithMoon, layers = DialLayers(isMoonEnabled = false))
        assertTrue(
            "Moon marker must be drawn when isMoonEnabled = true",
            changedPixelsNear(first = enabled, second = disabled, point = moonPoint) > 20,
        )
    }

    @Test
    fun moonSuppressedWhenIncomplete() {
        // The marker needs a longitude and a phase, and DialGeometry allows each to be absent on
        // its own. Every combination must paint nothing anywhere in the frame rather than inventing
        // a position: comparing whole frames catches a marker drawn at some other default place,
        // which probing a single point would miss.
        val incomplete =
            listOf(
                prague.copy(moonLongitudeDeg = null, moonPhaseLongitudeDeg = null),
                prague.copy(moonLongitudeDeg = 60.0, moonPhaseLongitudeDeg = null),
                prague.copy(moonLongitudeDeg = null, moonPhaseLongitudeDeg = 90.0),
            )
        for (geometry in incomplete) {
            val enabled = render(geometry = geometry, layers = DialLayers(isMoonEnabled = true))
            val disabled = render(geometry = geometry, layers = DialLayers(isMoonEnabled = false))
            assertTrue("Moon must be suppressed for $geometry", enabled.sameAs(disabled))
        }
    }

    @Test
    fun moonPhasePicksLitLimb() {
        // The lit limb is a function of moonPhaseLongitudeDeg, not of moonLongitudeDeg: both
        // renders below put the marker in the same place and differ only in the phase.
        val waxing = illuminatedLimbOffsetX(prague.copy(moonLongitudeDeg = 60.0, moonPhaseLongitudeDeg = 90.0))
        val waning = illuminatedLimbOffsetX(prague.copy(moonLongitudeDeg = 60.0, moonPhaseLongitudeDeg = 270.0))
        assertTrue("northern waxing phase must light the right limb, offset was $waxing", waxing > 0f)
        assertTrue("northern waning phase must light the left limb, offset was $waning", waning < 0f)
    }

    @Test
    fun moonMirrorFlipsLitLimb() {
        // The horizontal mirror in MoonRenderer is a claim about the bright limb's apparent side,
        // not a consequence of the plate inversion, so it needs a pixel assertion rather than a
        // smoke render: without the mirror both hemispheres light the same limb.
        val north = illuminatedLimbOffsetX(prague.copy(moonLongitudeDeg = 60.0, moonPhaseLongitudeDeg = 90.0))
        val south =
            illuminatedLimbOffsetX(
                prague.copy(latitudeDeg = -33.87, moonLongitudeDeg = 60.0, moonPhaseLongitudeDeg = 90.0),
            )
        assertTrue("northern first quarter must light the right limb, offset was $north", north > 0f)
        assertTrue("southern first quarter must light the left limb, offset was $south", south < 0f)
        // The two markers land on different sub-pixel offsets, so the mirrored offsets agree only
        // to about a pixel of rasterisation (measured: +2.25 px north, -3.30 px south) rather than
        // exactly. The signs above carry the claim; this only rejects a gross scale error.
        assertEquals("southern marker must mirror the northern one", -north, south, MIRROR_TOLERANCE)
    }

    @Test
    fun southernZodiacRenders() {
        val sydney = DialGeometry(localSiderealAngleDeg = 0.0, trueObliquityDeg = 23.44, latitudeDeg = -33.87)
        val sydneyProjection = OrlojProjection(sydney)
        val point = sydneyProjection.eclipticPoint(30.0)
        val enabled = render(geometry = sydney)
        val disabled = render(geometry = sydney, layers = DialLayers(isZodiacRingEnabled = false))
        assertTrue(
            "The zodiac ring must be drawn for a southern site",
            changedPixelsNear(first = enabled, second = disabled, point = point) > 20,
        )
        val rotatedSydney = sydney.copy(localSiderealAngleDeg = 90.0)
        val rotated = render(geometry = rotatedSydney)
        assertTrue(
            "The southern zodiac ring must rotate with sidereal time",
            changedPixelsNear(first = enabled, second = rotated, point = point) > 20,
        )
    }

    @Test
    fun zodiacLabelsAreCentred() {
        val sites = listOf(prague, prague.copy(latitudeDeg = -33.87))
        for (site in sites) {
            val projection = OrlojProjection(site)
            val noonBitmap = render(time = LocalTime.NOON, geometry = site)
            val midnightBitmap = render(time = LocalTime.MIDNIGHT, geometry = site)
            for (index in 0 until 12) {
                // Keep the civil hand away from the probed boundary and sign centre.
                val bitmap = if (index in 5..7) noonBitmap else midnightBitmap
                val boundary = projection.eclipticPoint(index * 30.0)
                val centre = projection.eclipticPoint(index * 30.0 + 15.0)
                assertTrue(
                    "Label must be present near sign centre $index for latitude ${site.latitudeDeg}",
                    hasLabelPixelNear(bitmap, centre),
                )
                assertFalse(
                    "Label must be absent near boundary $index for latitude ${site.latitudeDeg}",
                    hasLabelPixelNear(bitmap, boundary),
                )
            }
        }
    }

    @Test
    fun zodiacDividersSplitRing() {
        val sites = listOf(prague, prague.copy(latitudeDeg = -33.87))
        for (site in sites) {
            val projection = OrlojProjection(site)
            val noonBitmap = render(time = LocalTime.NOON, geometry = site)
            val midnightBitmap = render(time = LocalTime.MIDNIGHT, geometry = site)
            for (index in 0 until 12) {
                // Keep the civil hand away from the probed boundary.
                val bitmap = if (index in 5..7) noonBitmap else midnightBitmap
                val boundary = projection.eclipticPoint(index * 30.0)
                // Each divider runs along the ray from the dial centre through its boundary point, so it
                // crosses the offset ring obliquely rather than square to it. Samples run along that ray
                // at up to 7 px from the boundary point. The band half-span is 9.4-10.3 px there, so the
                // ends stay inside the night band and the samples can only find a divider. The star at
                // index 0 spans 5 px from its centre, so it cannot stand in for that divider.
                assertTrue(
                    "Divider $index must paint GOLD across the night band at its boundary " +
                        "for latitude ${site.latitudeDeg}",
                    dividerGoldSamples(bitmap, boundary) >= DIVIDER_GOLD_SAMPLES,
                )
                val inside = projection.eclipticPoint(index * 30.0 + 5.0)
                assertTrue(
                    "Interior of compartment $index must show NIGHT background for latitude ${site.latitudeDeg}",
                    containsColorNear(bitmap, inside, DialStyle.NIGHT),
                )
            }
        }
    }

    @Test
    fun vernalEquinoxStarRotates() {
        val projection = OrlojProjection(prague)
        val expectedEquinox = DialPoint(x = 0.0, y = -projection.equatorRadius)
        assertEquals(expectedEquinox.x, projection.eclipticPoint(0.0).x, 1e-9)
        assertEquals(expectedEquinox.y, projection.eclipticPoint(0.0).y, 1e-9)
        // The probe stays inside the night band, clear of the gold rim, which starts at
        // RING_INNER_WIDTH / 2 = 0.0375 sky radii from the ring centreline, about 9.4 px here.
        // Only the star and the divider sharing the equinox can put gold in a 4 px box, and the
        // divider alone adds 9 px against the star's further 17.
        assertTrue(
            "The 0° Aries star must add GOLD area at the projected vernal equinox",
            zodiacGoldAdded(prague, expectedEquinox) > STAR_GOLD_PIXELS,
        )
        val bitmap = render(time = LocalTime.MIDNIGHT, geometry = prague)
        val rotatedPrague = prague.copy(localSiderealAngleDeg = 90.0)
        val rotatedProjection = OrlojProjection(rotatedPrague)
        val expectedRotatedEquinox = DialPoint(x = rotatedProjection.equatorRadius, y = 0.0)
        assertEquals(expectedRotatedEquinox.x, rotatedProjection.eclipticPoint(0.0).x, 1e-9)
        assertEquals(expectedRotatedEquinox.y, rotatedProjection.eclipticPoint(0.0).y, 1e-9)
        val rotated = render(time = LocalTime.MIDNIGHT, geometry = rotatedPrague)
        assertTrue(
            "The vernal-equinox star must leave the unrotated equinox as sidereal time advances",
            goldAreaNear(bitmap, expectedEquinox, radius = STAR_PROBE_RADIUS) -
                goldAreaNear(rotated, expectedEquinox, radius = STAR_PROBE_RADIUS) > STAR_GOLD_PIXELS,
        )
        assertTrue(
            "The vernal-equinox star must arrive at the rotated equinox as sidereal time advances",
            zodiacGoldAdded(rotatedPrague, expectedRotatedEquinox) > STAR_GOLD_PIXELS,
        )
    }

    @Test
    fun southernEquinoxStarRotates() {
        val sydney = DialGeometry(localSiderealAngleDeg = 0.0, trueObliquityDeg = 23.44, latitudeDeg = -33.87)
        val sydneyProjection = OrlojProjection(sydney)
        // The equinox is the ecliptic point that meets the celestial equator, so it projects onto
        // the equator circle: on the meridian at local sidereal 0 and on the east-west axis at 90.
        // A southern site must not mirror either position, because the equator circle is not
        // mirrored. Both positions below are derived from the circle radius, not from the actual.
        val expectedEquinox = DialPoint(x = 0.0, y = -sydneyProjection.equatorRadius)
        assertEquals(expectedEquinox.x, sydneyProjection.eclipticPoint(0.0).x, 1e-9)
        assertEquals(expectedEquinox.y, sydneyProjection.eclipticPoint(0.0).y, 1e-9)
        assertTrue(
            "The star must add GOLD area at the independently expected southern projection",
            zodiacGoldAdded(sydney, expectedEquinox) > STAR_GOLD_PIXELS,
        )
        val sydneyBitmap = render(time = LocalTime.MIDNIGHT, geometry = sydney)
        val rotatedSydney = sydney.copy(localSiderealAngleDeg = 90.0)
        val rotatedProjection = OrlojProjection(rotatedSydney)
        val expectedRotatedEquinox = DialPoint(x = rotatedProjection.equatorRadius, y = 0.0)
        assertEquals(expectedRotatedEquinox.x, rotatedProjection.eclipticPoint(0.0).x, 1e-9)
        assertEquals(expectedRotatedEquinox.y, rotatedProjection.eclipticPoint(0.0).y, 1e-9)
        val rotated = render(time = LocalTime.MIDNIGHT, geometry = rotatedSydney)
        assertTrue(
            "The southern star must leave the unrotated equinox as sidereal time advances",
            goldAreaNear(sydneyBitmap, expectedEquinox, radius = STAR_PROBE_RADIUS) -
                goldAreaNear(rotated, expectedEquinox, radius = STAR_PROBE_RADIUS) > STAR_GOLD_PIXELS,
        )
        assertTrue(
            "The southern star must arrive at the rotated equinox as sidereal time advances",
            zodiacGoldAdded(rotatedSydney, expectedRotatedEquinox) > STAR_GOLD_PIXELS,
        )
    }

    @Test
    fun sunMarkerRendersAtPosition() {
        // The two renders differ only in sunLongitudeDeg, so every changed pixel is the marker:
        // the plate depends on latitude and obliquity alone through plateKey, and the zodiac ring
        // and civil hand are identical. A GOLD-presence probe would instead be satisfied by the
        // ring's own gold rims, and comparing against a Sun-off render would flip the whole
        // day/twilight/night plate — about 625 pixels of a 25x25 box — rather than the marker.
        val atFirst = prague.copy(sunLongitudeDeg = 90.0)
        val atSecond = prague.copy(sunLongitudeDeg = 210.0)
        val firstPoint = OrlojProjection(atFirst).eclipticPoint(90.0)
        val secondPoint = OrlojProjection(atSecond).eclipticPoint(210.0)
        val first = render(geometry = atFirst)
        val second = render(geometry = atSecond)
        assertTrue(
            "Sun marker must leave the 90° point",
            changedPixelsNear(first = first, second = second, point = firstPoint) > 20,
        )
        assertTrue(
            "Sun marker must appear at the 210° point",
            changedPixelsNear(first = first, second = second, point = secondPoint) > 20,
        )
    }

    @Test
    fun noSunMarkerWithoutLongitude() {
        // #27 requires missing-location behavior to be explicit and never to invent a sky
        // position. A null longitude must suppress the marker; a fabricated default would paint a
        // whole disc and its rays here. The equator circle also crosses this point and is GOLD, so
        // the probe takes a gold *area* delta against the same render with 0° set: the circle
        // contributes the same thin stroke to both, the invented marker hundreds of pixels more.
        val unknown = prague.copy(sunLongitudeDeg = null)
        val fabricated = prague.copy(sunLongitudeDeg = 0.0)
        val invented = OrlojProjection(fabricated).eclipticPoint(0.0)
        val layers = DialLayers(isZodiacRingEnabled = false)
        val withoutSun = render(time = LocalTime.MIDNIGHT, geometry = unknown, layers = layers)
        val withFabricatedSun = render(time = LocalTime.MIDNIGHT, geometry = fabricated, layers = layers)
        assertTrue(
            "An invented 0° longitude must add marker gold the unknown longitude does not",
            goldAreaNear(withFabricatedSun, invented) - goldAreaNear(withoutSun, invented) > 20,
        )
    }

    @Test
    fun noSunMarkerWhenSunLayerOff() {
        // When the Sun layer is disabled, the marker must not be drawn even if the geometry
        // carries a valid Sun longitude. With the zodiac ring disabled, the rest of the dial
        // (civil scale, hand, and blank plate grid) is independent of sunLongitudeDeg.
        // A Sun-off render with an explicit longitude must therefore match a Sun-off render
        // with unknown longitude pixel-for-pixel; if the layer toggle failed to suppress the
        // marker, the explicit longitude would paint gold marker pixels at its projected point.
        val withSun = prague.copy(sunLongitudeDeg = 90.0)
        val withoutSun = prague.copy(sunLongitudeDeg = null)
        val sunOff = DialLayers(isZodiacRingEnabled = false, isSunEnabled = false)
        val renderedWithSun = render(time = LocalTime.MIDNIGHT, geometry = withSun, layers = sunOff)
        val renderedWithoutSun = render(time = LocalTime.MIDNIGHT, geometry = withoutSun, layers = sunOff)
        assertTrue(
            "Disabling the Sun layer must leave no marker even when longitude is present",
            renderedWithSun.sameAs(renderedWithoutSun),
        )

        val sunPoint = OrlojProjection(withSun).eclipticPoint(90.0)
        val sunOn = DialLayers(isZodiacRingEnabled = false, isSunEnabled = true)
        val renderedOn = render(time = LocalTime.MIDNIGHT, geometry = withSun, layers = sunOn)
        assertTrue(
            "Enabling the Sun layer must draw the marker at the projected point",
            goldAreaNear(renderedOn, sunPoint) - goldAreaNear(renderedWithSun, sunPoint) > 20,
        )
    }

    @Test
    fun sunMarkerMovesWithSunLongitude() {
        val sun0 = prague.copy(sunLongitudeDeg = 0.0)
        val sun90 = prague.copy(sunLongitudeDeg = 90.0)
        val proj0 = OrlojProjection(sun0)
        val proj90 = OrlojProjection(sun90)
        val bmp0 = render(geometry = sun0)
        val bmp90 = render(geometry = sun90)
        assertTrue(
            "Sun marker should move away from 0° position when longitude is 90°",
            changedPixelsNear(first = bmp0, second = bmp90, point = proj0.eclipticPoint(0.0)) > 20,
        )
        assertTrue(
            "Sun marker should appear at 90° position",
            changedPixelsNear(first = bmp0, second = bmp90, point = proj90.eclipticPoint(90.0)) > 20,
        )
    }

    @Test
    fun sunRendersOnSouthernPlate() {
        // Same discriminator as the northern case, on the south-pole plate, where no Sun test had
        // isolating coverage before. Both renders share the plate, the ring, and the hand.
        val sydney = DialGeometry(localSiderealAngleDeg = 0.0, trueObliquityDeg = 23.44, latitudeDeg = -33.87)
        val atFirst = sydney.copy(sunLongitudeDeg = 90.0)
        val atSecond = sydney.copy(sunLongitudeDeg = 210.0)
        val firstPoint = OrlojProjection(atFirst).eclipticPoint(90.0)
        val secondPoint = OrlojProjection(atSecond).eclipticPoint(210.0)
        val first = render(geometry = atFirst)
        val second = render(geometry = atSecond)
        assertTrue(
            "Southern Sun marker must leave the 90° point",
            changedPixelsNear(first = first, second = second, point = firstPoint) > 20,
        )
        assertTrue(
            "Southern Sun marker must appear at the 210° point",
            changedPixelsNear(first = first, second = second, point = secondPoint) > 20,
        )
    }

    @Test
    fun civilHandMatchesCardinalHours() {
        val hours = listOf(12 to 0.0, 18 to 90.0, 0 to 180.0, 6 to 270.0)
        for ((hour, angle) in hours) {
            val bitmap = render(time = LocalTime.of(hour, 0))
            for ((_, probeAngle) in hours) {
                val color = if (probeAngle == angle) DialStyle.HAND else DialStyle.NIGHT
                assertEquals(color, pixelAt(bitmap, radialPoint(angleDeg = probeAngle, radius = 0.35)))
            }
            assertTrue(pixelAt(bitmap, radialPoint(angleDeg = angle, radius = 1.13)) != DialStyle.HAND)
        }
    }

    @Test
    fun civilHandIncludesMinutes() {
        val bitmap = render(time = LocalTime.of(15, 15, 36))
        // 3h 15m 36s since noon is 48.9 degrees on the 24-hour scale.
        assertEquals(DialStyle.HAND, pixelAt(bitmap, radialPoint(angleDeg = 48.9, radius = 0.35)))
        assertEquals(DialStyle.NIGHT, pixelAt(bitmap, radialPoint(angleDeg = 45.0, radius = 0.35)))
    }

    @Test
    fun romanNumeralsKeepGlyphSpacing() {
        val bitmap = render()
        // XII and XXIV must occupy several glyph advances, even though the plate uses unit radii.
        assertTrue(textSpan(bitmap, -1.205) > 24)
        assertTrue(textSpan(bitmap, 1.205) > 40)
    }

    @Test
    fun missingLocationOmitsGeometry() {
        val bitmap = render()
        assertEquals(DialStyle.NIGHT, pixelAt(bitmap, DialPoint(x = 0.1, y = -0.8)))
        assertEquals(DialStyle.NIGHT, pixelAt(bitmap, DialPoint(x = -0.7, y = 0.2)))
        assertEquals(DialStyle.NIGHT, pixelAt(bitmap, DialPoint(x = 0.1, y = 0.8)))
        assertFalse(containsColor(bitmap, DialStyle.SKY))
        assertFalse(containsColor(bitmap, DialStyle.TWILIGHT))
    }

    @Test
    fun pragueHasDayTwilightAndNight() {
        val bitmap = render(geometry = prague, layers = DialLayers(isZodiacRingEnabled = false))
        // These interior points have geometric altitudes about +52, -6, and -27 degrees.
        assertEquals(DialStyle.SKY, pixelAt(bitmap, DialPoint(x = 0.1, y = -0.8)))
        assertEquals(DialStyle.TWILIGHT, pixelAt(bitmap, DialPoint(x = -0.7, y = 0.2)))
        assertEquals(DialStyle.NIGHT, pixelAt(bitmap, DialPoint(x = 0.1, y = 0.8)))
    }

    @Test
    fun sunToggleKeepsPlainPlate() {
        val plain = DialLayers(isZodiacRingEnabled = false, isSunEnabled = false)
        val off = render(geometry = prague, layers = plain)
        assertEquals(DialStyle.NIGHT, pixelAt(off, DialPoint(x = 0.1, y = -0.8)))
        assertEquals(DialStyle.NIGHT, pixelAt(off, DialPoint(x = -0.7, y = 0.2)))
        assertFalse(containsColor(off, DialStyle.SKY))
        assertFalse(containsColor(off, DialStyle.TWILIGHT))
        // The toggle hides its own boundary strokes too, not just the fills. Probe contour points
        // that stay clear of the equator and tropic grid circles, with the zodiac disabled.
        val on = render(geometry = prague, layers = DialLayers(isZodiacRingEnabled = false))
        val horizon = DialPoint(x = 0.79, y = 0.22)
        val night = DialPoint(x = -0.57, y = 0.5)
        assertTrue(containsColorNear(on, horizon, DialStyle.GOLD))
        assertFalse(containsColorNear(off, horizon, DialStyle.GOLD))
        assertTrue(containsColorNear(on, night, DialStyle.MUTED_GOLD))
        assertFalse(containsColorNear(off, night, DialStyle.MUTED_GOLD))
    }

    @Test
    fun sunOffFramesLeaveNoStalePlate() {
        // The Sun-off path builds no plate at all, so the first Sun-on frame has to construct one.
        // Renders that never enabled the Sun must not leave the cache in a state that half-draws.
        val plain = DialLayers(isZodiacRingEnabled = false, isSunEnabled = false)
        val lit = DialLayers(isZodiacRingEnabled = false)
        val reused = DialRenderer()
        val firstOff = drawWith(reused, prague, plain)
        val secondOff = drawWith(reused, prague, plain)
        assertTrue(firstOff.sameAs(secondOff))
        assertTrue(drawWith(reused, prague, lit).sameAs(drawWith(DialRenderer(), prague, lit)))
    }

    @Test
    fun zodiacRemainsBelowHorizon() {
        val projection = OrlojProjection(prague)
        val point = projection.eclipticPoint(210.0)
        assertTrue(projection.altitudeDeg(point) < -18.0)
        val enabled = render(geometry = prague)
        val disabled = render(geometry = prague, layers = DialLayers(isZodiacRingEnabled = false))
        assertTrue(
            "The below-horizon sign must be drawn",
            changedPixelsNear(first = enabled, second = disabled, point = point) > 20,
        )
    }

    @Test
    fun zodiacRotatesWithSiderealTime() {
        val later = prague.copy(localSiderealAngleDeg = 90.0)
        val original = render(geometry = prague)
        val rotated = render(geometry = later)
        val sign = OrlojProjection(prague).eclipticPoint(210.0)
        assertTrue(changedPixelsNear(first = original, second = rotated, point = sign) > 20)
        // The background plate depends on latitude and obliquity, not sidereal rotation.
        val plateFirst = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        val plateSecond = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        OrlojPlateRenderer().draw(Canvas(plateFirst), OrlojProjection(prague), isSunEnabled = true)
        OrlojPlateRenderer().draw(Canvas(plateSecond), OrlojProjection(later), isSunEnabled = true)
        assertTrue(plateFirst.sameAs(plateSecond))
    }

    @Test
    fun landscapeUsesShortestSide() {
        val landscape = Bitmap.createBitmap(1200, SIZE, Bitmap.Config.ARGB_8888)
        renderer.renderDial(
            canvas = Canvas(landscape),
            state = clockState(LocalTime.NOON),
            geometry = prague,
            layers = DialLayers(isZodiacRingEnabled = false),
        )
        val centered = Bitmap.createBitmap(landscape, 200, 0, SIZE, SIZE)
        assertEquals(DialStyle.SKY, pixelAt(centered, DialPoint(x = 0.1, y = -0.8)))
        assertEquals(DialStyle.TWILIGHT, pixelAt(centered, DialPoint(x = -0.7, y = 0.2)))
        assertEquals(DialStyle.NIGHT, pixelAt(centered, DialPoint(x = 0.1, y = 0.8)))
        assertEquals(DialStyle.RIM, pixelAt(centered, DialPoint(x = 1.32, y = 0.0)))
        assertEquals(DialStyle.BACKGROUND, pixelAt(centered, DialPoint(x = 1.4, y = 0.0)))
        assertEquals(DialStyle.BACKGROUND, landscape.getPixel(1, SIZE / 2))
        assertEquals(DialStyle.BACKGROUND, landscape.getPixel(1198, SIZE / 2))
    }

    @Test
    fun degenerateFramesAreBounded() {
        // A degenerate surface keeps producing the same canvas until the framework recreates it, so
        // each skip must log once per episode rather than warn on every tick.
        val throttled = DialRenderer()
        val empty = Canvas()
        val tiny = Bitmap.createBitmap(20, 20, Bitmap.Config.ARGB_8888)
        val tinyCanvas = Canvas(tiny)
        ShadowLog.clear()
        repeat(3) { throttled.renderDial(empty, clockState(LocalTime.NOON)) }
        repeat(3) { throttled.renderDial(tinyCanvas, clockState(LocalTime.NOON)) }
        assertEquals(DialStyle.BACKGROUND, tiny.getPixel(10, 10))
        val warnings = ShadowLog.getLogsForTag("DialRenderer").filter { it.type == Log.WARN }
        assertEquals(2, warnings.size)
        assertTrue(warnings[0].msg.contains("empty canvas"))
        assertTrue(warnings[1].msg.contains("radius below minimum"))

        // One completed frame ends both episodes, so the next degenerate frame logs again.
        assertEquals(DialStyle.BACKGROUND, drawInto(throttled, prague).getPixel(1, 1))
        throttled.renderDial(empty, clockState(LocalTime.NOON))
        val entries = ShadowLog.getLogsForTag("DialRenderer")
        assertEquals(2, entries.count { it.type == Log.INFO })
        assertEquals(3, entries.count { it.type == Log.WARN })
    }

    @Test
    fun renderFailuresAreContained() {
        ShadowLog.clear()
        val containment = RenderFailureContainment()
        containment.containRenderFailure { throw IllegalArgumentException("invalid argument") }
        containment.containRenderFailure { throw IllegalStateException("invalid state") }
        val logs = ShadowLog.getLogsForTag("DialRenderer").filter { it.type == Log.ERROR }
        assertEquals(2, logs.size)
        assertTrue(logs[0].msg.contains("invalid argument"))
        assertTrue(logs[1].msg.contains("invalid state"))
        var hasDrawn = false
        containment.containRenderFailure { hasDrawn = true }
        assertTrue(hasDrawn)
    }

    @Test
    fun unrelatedFailuresPropagate() {
        assertThrows(UnsupportedOperationException::class.java) {
            RenderFailureContainment().containRenderFailure { throw UnsupportedOperationException("not contained") }
        }
    }

    @Test
    fun repeatedFailuresAreThrottled() {
        ShadowLog.clear()
        val containment = RenderFailureContainment()
        repeat(5) {
            containment.containRenderFailure {
                throw IllegalArgumentException("repeated argument")
            }
        }
        val logs = ShadowLog.getLogsForTag("DialRenderer").filter { it.type == Log.ERROR }
        assertEquals(1, logs.size)
        assertTrue(logs.single().msg.contains("repeated argument"))
    }

    @Test
    fun rendererReuseIsIndependent() {
        // A live engine keeps one DialRenderer for its lifetime while the saved site changes, and
        // OrlojPlateRenderer caches static plate geometry, so frames must be independent and reusable.
        val sydney = prague.copy(latitudeDeg = -33.87)
        val reused = DialRenderer()
        val firstPrague = drawInto(reused, prague)
        val secondPrague = drawInto(reused, prague)
        assertTrue(firstPrague.sameAs(secondPrague))
        val sydneyPass = drawInto(reused, sydney)
        assertTrue(firstPrague.sameAs(drawInto(DialRenderer(), prague)))
        assertTrue(sydneyPass.sameAs(drawInto(DialRenderer(), sydney)))
        assertTrue(firstPrague.sameAs(drawInto(reused, prague)))
    }

    private fun drawInto(target: DialRenderer, geometry: DialGeometry): Bitmap {
        val bitmap = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        target.renderDial(canvas = Canvas(bitmap), state = clockState(LocalTime.NOON), geometry = geometry)
        return bitmap
    }

    private fun render(
        time: LocalTime = LocalTime.NOON,
        geometry: DialGeometry? = null,
        layers: DialLayers = DialLayers(),
    ): Bitmap {
        val bitmap = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        renderer.renderDial(canvas = Canvas(bitmap), state = clockState(time), geometry = geometry, layers = layers)
        assertEquals(DialStyle.BACKGROUND, bitmap.getPixel(1, 1))
        return bitmap
    }

    private fun drawWith(target: DialRenderer, geometry: DialGeometry, layers: DialLayers): Bitmap {
        val bitmap = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        target
            .renderDial(
                canvas = Canvas(bitmap),
                state = clockState(LocalTime.NOON),
                geometry = geometry,
                layers = layers,
            )
        return bitmap
    }

    private fun pixelAt(bitmap: Bitmap, point: DialPoint): Int =
        bitmap.getPixel((CENTER + point.x * SKY_RADIUS).roundToInt(), (CENTER + point.y * SKY_RADIUS).roundToInt())

    private fun radialPoint(angleDeg: Double, radius: Double): DialPoint {
        val radians = Math.toRadians(angleDeg)
        return DialPoint(x = sin(radians) * radius, y = -cos(radians) * radius)
    }

    private fun containsColor(bitmap: Bitmap, color: Int): Boolean {
        val pixels = IntArray(bitmap.width * bitmap.height)
        bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        return color in pixels
    }

    private fun containsColorNear(bitmap: Bitmap, point: DialPoint, color: Int): Boolean {
        val x = (CENTER + point.x * SKY_RADIUS).roundToInt()
        val y = (CENTER + point.y * SKY_RADIUS).roundToInt()
        for (dx in -PROBE_RADIUS..PROBE_RADIUS) {
            for (dy in -PROBE_RADIUS..PROBE_RADIUS) {
                if (bitmap.getPixel(x + dx, y + dy) == color) return true
            }
        }
        return false
    }

    private fun hasLabelPixelNear(bitmap: Bitmap, point: DialPoint): Boolean {
        val px = (CENTER + point.x * SKY_RADIUS).roundToInt()
        val py = (CENTER + point.y * SKY_RADIUS).roundToInt()
        for (dx in -PROBE_RADIUS..PROBE_RADIUS) {
            for (dy in -PROBE_RADIUS..PROBE_RADIUS) {
                val c = bitmap.getPixel(px + dx, py + dy)
                val blue = c and 0xFF
                val red = c shr 16 and 0xFF
                if (red > 200 && blue > 130) return true
            }
        }
        return false
    }

    private fun goldAreaNear(bitmap: Bitmap, point: DialPoint, radius: Int = STAR_PROBE_RADIUS): Int {
        val px = (CENTER + point.x * SKY_RADIUS).roundToInt()
        val py = (CENTER + point.y * SKY_RADIUS).roundToInt()
        var count = 0
        for (dx in -radius..radius) {
            for (dy in -radius..radius) {
                val c = bitmap.getPixel(px + dx, py + dy)
                if (isGoldPixel(c)) count++
            }
        }
        return count
    }

    private fun dividerGoldSamples(bitmap: Bitmap, boundary: DialPoint): Int {
        val distance = hypot(x = boundary.x, y = boundary.y)
        val ux = boundary.x / distance
        val uy = boundary.y / distance
        return DIVIDER_SAMPLE_OFFSETS.count { offset ->
            val sample =
                DialPoint(
                    x = boundary.x + ux * offset / SKY_RADIUS,
                    y = boundary.y + uy * offset / SKY_RADIUS,
                )
            goldAreaNear(bitmap, sample, radius = DIVIDER_SAMPLE_RADIUS) > 0
        }
    }

    private fun zodiacGoldAdded(geometry: DialGeometry, point: DialPoint): Int {
        val plain = DialLayers(isSunEnabled = false)
        val withZodiac = render(time = LocalTime.MIDNIGHT, geometry = geometry, layers = plain)
        val withoutZodiac =
            render(
                time = LocalTime.MIDNIGHT,
                geometry = geometry,
                layers = plain.copy(isZodiacRingEnabled = false),
            )
        return goldAreaNear(withZodiac, point, radius = STAR_PROBE_RADIUS) -
            goldAreaNear(withoutZodiac, point, radius = STAR_PROBE_RADIUS)
    }

    private fun isGoldPixel(c: Int): Boolean {
        val red = c shr 16 and 0xFF
        val green = c shr 8 and 0xFF
        val blue = c and 0xFF
        val isYellow = red > 120 && green > 100
        val isWarm = red > blue && blue < 120
        return isYellow && isWarm
    }

    private fun textSpan(bitmap: Bitmap, normalizedY: Double): Int {
        val baseline = (CENTER + normalizedY * SKY_RADIUS).roundToInt()
        val columns =
            (360..440).filter { x ->
                (baseline - 20..baseline + 20).any { y -> bitmap.getPixel(x, y) == DialStyle.GOLD }
            }
        return if (columns.isEmpty()) 0 else columns.last() - columns.first() + 1
    }

    private fun illuminatedLimbOffsetX(geometry: DialGeometry): Float {
        val bitmap = render(geometry = geometry, layers = LAYERS_WITHOUT_SUN_AND_ZODIAC)
        val point = OrlojProjection(geometry).moonPoint ?: error("geometry carries no Moon longitude")
        val centreX = CENTER + point.x * SKY_RADIUS
        val centreY = CENTER + point.y * SKY_RADIUS
        var offsetSum = 0
        var illuminated = 0
        for (dx in -MOON_PROBE_RADIUS..MOON_PROBE_RADIUS) {
            for (dy in -MOON_PROBE_RADIUS..MOON_PROBE_RADIUS) {
                val pixel = bitmap.getPixel((centreX + dx).roundToInt(), (centreY + dy).roundToInt())
                if (pixel == DialStyle.MOON_ILLUMINATED) {
                    offsetSum += dx
                    illuminated++
                }
            }
        }
        assertTrue("expected illuminated Moon pixels at $point", illuminated > 0)
        // Positive offsets point at the right limb, negative at the left one.
        return offsetSum.toFloat() / illuminated
    }

    private fun changedPixelsNear(first: Bitmap, second: Bitmap, point: DialPoint): Int {
        val x = (CENTER + point.x * SKY_RADIUS).roundToInt()
        val y = (CENTER + point.y * SKY_RADIUS).roundToInt()
        var changed = 0
        for (dx in -PROBE_RADIUS..PROBE_RADIUS) {
            for (dy in -PROBE_RADIUS..PROBE_RADIUS) {
                if (first.getPixel(x + dx, y + dy) != second.getPixel(x + dx, y + dy)) {
                    changed++
                }
            }
        }
        return changed
    }

    private companion object {
        const val SIZE = 800
        const val CENTER = 400.0
        const val SKY_RADIUS = SIZE * 0.43 / 1.37
        const val PROBE_RADIUS = 12
        const val MOON_PROBE_RADIUS = 10
        const val MIRROR_TOLERANCE = 2.0f
        val LAYERS_WITHOUT_SUN_AND_ZODIAC = DialLayers(isZodiacRingEnabled = false, isSunEnabled = false)
        val DIVIDER_SAMPLE_OFFSETS = listOf(-7.0, -4.0, 0.0, 4.0, 7.0)
        const val DIVIDER_SAMPLE_RADIUS = 1
        const val DIVIDER_GOLD_SAMPLES = 4
        const val STAR_PROBE_RADIUS = 4
        const val STAR_GOLD_PIXELS = 13
    }
}
