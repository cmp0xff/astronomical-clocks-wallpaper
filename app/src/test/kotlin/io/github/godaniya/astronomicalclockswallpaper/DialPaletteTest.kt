package io.github.godaniya.astronomicalclockswallpaper

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DialPaletteTest {
    @Test
    fun darkPaletteIsPinned() {
        assertEquals(0xFF101923.toInt(), DialStyle.DARK_PALETTE.background)
        assertEquals(0xFFD8B66A.toInt(), DialStyle.DARK_PALETTE.gold)
        assertEquals(0xFF887347.toInt(), DialStyle.DARK_PALETTE.mutedGold)
        assertEquals(0xFF1C2C39.toInt(), DialStyle.DARK_PALETTE.rim)
        assertEquals(0xFF286078.toInt(), DialStyle.DARK_PALETTE.sky)
        assertEquals(0xFF9C6438.toInt(), DialStyle.DARK_PALETTE.twilight)
        assertEquals(0xFF152433.toInt(), DialStyle.DARK_PALETTE.night)
        assertEquals(0xFFF4E5B8.toInt(), DialStyle.DARK_PALETTE.hand)
        assertEquals(0xFFE8EEF5.toInt(), DialStyle.DARK_PALETTE.moonIlluminated)
        assertEquals(0xFF2C3E50.toInt(), DialStyle.DARK_PALETTE.moonShadow)
        assertEquals(0xFF152433.toInt(), DialStyle.DARK_PALETTE.zodiacBand)
        assertEquals(0xFF101923.toInt(), DialStyle.DARK_PALETTE.casing)
    }

    @Test
    fun lightPaletteIsPinned() {
        assertEquals(0xFFF7F4EB.toInt(), DialStyle.LIGHT_PALETTE.background)
        assertEquals(0xFF6E4D25.toInt(), DialStyle.LIGHT_PALETTE.gold)
        assertEquals(0xFF96734B.toInt(), DialStyle.LIGHT_PALETTE.mutedGold)
        assertEquals(0xFFE8E2D2.toInt(), DialStyle.LIGHT_PALETTE.rim)
        assertEquals(0xFF89B2CC.toInt(), DialStyle.LIGHT_PALETTE.sky)
        assertEquals(0xFFC88B58.toInt(), DialStyle.LIGHT_PALETTE.twilight)
        assertEquals(0xFF2C3E50.toInt(), DialStyle.LIGHT_PALETTE.night)
        assertEquals(0xFF4E341B.toInt(), DialStyle.LIGHT_PALETTE.hand)
        assertEquals(0xFFF7F4EB.toInt(), DialStyle.LIGHT_PALETTE.moonIlluminated)
        assertEquals(0xFF5A6B7D.toInt(), DialStyle.LIGHT_PALETTE.moonShadow)
        assertEquals(0xFFE8E2D2.toInt(), DialStyle.LIGHT_PALETTE.zodiacBand)
        assertEquals(0xFFF7F4EB.toInt(), DialStyle.LIGHT_PALETTE.casing)
    }

    @Test
    fun lightRegionsAreDistinct() {
        val light = DialStyle.LIGHT_PALETTE
        assertNotEquals(light.sky, light.twilight)
        assertNotEquals(light.twilight, light.night)
        assertNotEquals(light.night, light.sky)
        assertNotEquals(light.background, light.gold)
        assertNotEquals(light.background, light.hand)
    }

    @Test
    fun paletteResolutionFollowsMode() {
        assertEquals(DialStyle.LIGHT_PALETTE, DialStyle.paletteFor(DialAppearance.LIGHT, isSystemInNightMode = true))
        assertEquals(DialStyle.LIGHT_PALETTE, DialStyle.paletteFor(DialAppearance.LIGHT, isSystemInNightMode = false))

        assertEquals(DialStyle.DARK_PALETTE, DialStyle.paletteFor(DialAppearance.DARK, isSystemInNightMode = true))
        assertEquals(DialStyle.DARK_PALETTE, DialStyle.paletteFor(DialAppearance.DARK, isSystemInNightMode = false))

        assertEquals(DialStyle.DARK_PALETTE, DialStyle.paletteFor(DialAppearance.SYSTEM, isSystemInNightMode = true))
        assertEquals(DialStyle.LIGHT_PALETTE, DialStyle.paletteFor(DialAppearance.SYSTEM, isSystemInNightMode = false))
    }

    // The palette contract: every information-bearing ink clears WCAG 2.1 against the surface it is
    // drawn on - 4.5:1 for text, 3:1 for graphics. A line crossing surfaces at opposite luminance
    // extremes (the light theme's pale plate and its dark night region) may instead be carried by the
    // casing, which is why the hand row reads the better of the ink and the casing. The decorative
    // graticule is exempt and is not asserted here; docs/orloj.md#palette-contrast records its
    // measured ratios along with the rest of the audit.
    @Test
    fun inksClearTheirSurfaces() {
        for (palette in listOf(DialStyle.DARK_PALETTE, DialStyle.LIGHT_PALETTE)) {
            val checks =
                listOf(
                    ContrastCase(
                        label = "sign names on the zodiac band",
                        ink = palette.hand,
                        surface = palette.zodiacBand,
                        minimum = TEXT_MINIMUM,
                    ),
                    ContrastCase(
                        label = "civil numerals on the rim",
                        ink = palette.gold,
                        surface = palette.rim,
                        minimum = TEXT_MINIMUM,
                    ),
                    ContrastCase(
                        label = "zodiac ring and star on the band",
                        ink = palette.gold,
                        surface = palette.zodiacBand,
                        minimum = GRAPHICS_MINIMUM,
                    ),
                    ContrastCase(
                        label = "moon rim on the band",
                        ink = palette.gold,
                        surface = palette.zodiacBand,
                        minimum = GRAPHICS_MINIMUM,
                    ),
                    ContrastCase(
                        label = "moon lit limb against the shadow",
                        ink = palette.moonIlluminated,
                        surface = palette.moonShadow,
                        minimum = GRAPHICS_MINIMUM,
                    ),
                    ContrastCase(
                        label = "hand over the night region",
                        ink = palette.hand,
                        surface = palette.night,
                        minimum = GRAPHICS_MINIMUM,
                        casing = palette.casing,
                    ),
                )
            for (surface in listOf(palette.sky, palette.twilight, palette.night)) {
                assertAtLeast(
                    ContrastCase(
                        label = "ring-disabled marker outline on plate",
                        ink = palette.hand,
                        surface = surface,
                        minimum = GRAPHICS_MINIMUM,
                        casing = palette.casing,
                    ),
                )
            }
            for (check in checks) {
                assertAtLeast(check)
            }
        }
    }

    /** One ink over one surface; [casing] lets the line be read by its underlay where the ink cannot. */
    private data class ContrastCase(
        val label: String,
        val ink: Int,
        val surface: Int,
        val minimum: Double,
        val casing: Int? = null,
    )

    private fun assertAtLeast(check: ContrastCase) {
        for (brightness in DialDisplaySettings.BRIGHTNESS_RANGE) {
            val ratios =
                listOfNotNull(
                    contrastRatio(first = dim(check.ink, brightness), second = dim(check.surface, brightness)),
                    check.casing?.let { casing ->
                        contrastRatio(
                            first = dim(casing, brightness),
                            second = dim(check.surface, brightness),
                        )
                    },
                )
            val best = ratios.max()
            assertTrue("${check.label} at $brightness needs ${check.minimum}:1, was $best", best >= check.minimum)
        }
    }

    private fun dim(color: Int, brightness: Int): Int {
        val alpha = ((100 - brightness) * 255f / 100).toInt()

        fun channel(shift: Int): Int = ((color shr shift and 255) * (255 - alpha) / 255.0).toInt()
        return 255 shl 24 or (channel(16) shl 16) or (channel(8) shl 8) or channel(0)
    }

    private fun contrastRatio(first: Int, second: Int): Double {
        val a = relativeLuminance(first)
        val b = relativeLuminance(second)
        return (maxOf(a, b) + 0.05) / (minOf(a, b) + 0.05)
    }

    private fun relativeLuminance(color: Int): Double {
        fun channel(shift: Int): Double {
            val value = (color shr shift and 0xFF) / 255.0
            return if (value <= 0.04045) value / 12.92 else Math.pow((value + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channel(16) + 0.7152 * channel(8) + 0.0722 * channel(0)
    }

    private companion object {
        const val TEXT_MINIMUM = 4.5
        const val GRAPHICS_MINIMUM = 3.0
    }
}
