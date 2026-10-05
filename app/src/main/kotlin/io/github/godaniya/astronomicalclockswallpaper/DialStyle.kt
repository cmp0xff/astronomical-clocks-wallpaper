package io.github.godaniya.astronomicalclockswallpaper

/**
 * Palette of colors for rendering the astronomical clock.
 *
 * The palette is a contract about contrast, not just taste: every [ink][DialPalette.gold] is drawn on
 * a known [surface][DialPalette.night], and the pair must clear WCAG 2.1 against that surface - 4.5:1
 * for text and 3:1 for graphics - unless a stroke bounds it (see [DialPalette.casing]). The dark
 * palette clears this by being dark throughout; the light palette keeps one dark surface, the night
 * sky region, and needs the casing for the thin lines that cross it. DialPaletteTest holds the
 * measured ratios: docs/orloj.md#palette-contrast records the full audit and the accepted exceptions.
 */
internal data class DialPalette(
    val background: Int,
    val gold: Int,
    val mutedGold: Int,
    val rim: Int,
    val sky: Int,
    val twilight: Int,
    val night: Int,
    val hand: Int,
    val moonIlluminated: Int,
    val moonShadow: Int,
    val zodiacBand: Int,
    val casing: Int,
)

/** Original Orloj-inspired palette; dimensions in the renderers are fractions of the sky radius. */
internal object DialStyle {
    // Font shaping needs ordinary-size text: subpixel textSize rounds glyph advances to zero.
    const val TEXT_UNITS = 1_000f

    // Pinned dark palette constants for backwards compatibility and tests
    const val BACKGROUND: Int = 0xFF101923.toInt()
    const val GOLD: Int = 0xFFD8B66A.toInt()
    const val MUTED_GOLD: Int = 0xFF887347.toInt()
    const val RIM: Int = 0xFF1C2C39.toInt()
    const val SKY: Int = 0xFF286078.toInt()
    const val TWILIGHT: Int = 0xFF9C6438.toInt()
    const val NIGHT: Int = 0xFF152433.toInt()
    const val HAND: Int = 0xFFF4E5B8.toInt()
    const val MOON_ILLUMINATED: Int = 0xFFE8EEF5.toInt()

    // The Moon marker rides the zodiac band, and the band is filled with the palette's own tone, so
    // the shadow must be its own colour: at NIGHT the unlit two-thirds of the disc vanished into its
    // background and only the gold rim separated a crescent from the band. This slate is 45 RGB units
    // from NIGHT and at least 42 from every other entry, so the disc reads as a sphere whether it is
    // over the band (zodiac on) or the plain grid (zodiac off).
    const val MOON_SHADOW: Int = 0xFF2C3E50.toInt()

    val DARK_PALETTE =
        DialPalette(
            background = BACKGROUND,
            gold = GOLD,
            mutedGold = MUTED_GOLD,
            rim = RIM,
            sky = SKY,
            twilight = TWILIGHT,
            night = NIGHT,
            hand = HAND,
            moonIlluminated = MOON_ILLUMINATED,
            moonShadow = MOON_SHADOW,
            zodiacBand = NIGHT,
            // The dark palette's plate is already dark, so a page-toned casing only outlines the thin
            // lines where they cross the mid-toned twilight band, which the gold inks cannot clear on
            // their own (mutedGold on TWILIGHT is 1.07:1). It is invisible against NIGHT.
            casing = BACKGROUND,
        )

    // Light appearance palette: ivory, bronze, and pale blue with recognizable twilight/night regions
    const val LIGHT_BACKGROUND: Int = 0xFFF7F4EB.toInt()
    const val LIGHT_GOLD: Int = 0xFF6E4D25.toInt()
    const val LIGHT_MUTED_GOLD: Int = 0xFF96734B.toInt()
    const val LIGHT_RIM: Int = 0xFFE8E2D2.toInt()
    const val LIGHT_SKY: Int = 0xFF89B2CC.toInt()
    const val LIGHT_TWILIGHT: Int = 0xFFC88B58.toInt()
    const val LIGHT_NIGHT: Int = 0xFF2C3E50.toInt()
    const val LIGHT_HAND: Int = 0xFF4E341B.toInt()

    // The light theme keeps the zodiac ring light: it sits just inside the ivory civil scale, and a
    // dark band there read as a stray dark ring in an otherwise light dial. It reuses the pale sand
    // rim tone, which the bronze inks clear comfortably (gold 5.90:1, sign names in `hand` 8.87:1),
    // and the night sky region stays the one dark area because there it is semantically the night.
    const val LIGHT_ZODIAC_BAND: Int = LIGHT_RIM

    // On the pale band the Moon must be dark-on-light, unlike the dark palette: an ivory disc would
    // vanish into the band the way the dark palette's shadow vanished into NIGHT. The shadow is
    // therefore the disc's visible mass (4.24:1 against the band) and the lit limb keeps the ivory
    // plate tone (4.98:1 against the shadow), bounded by the gold rim.
    const val LIGHT_MOON_ILLUMINATED: Int = 0xFFF7F4EB.toInt()
    const val LIGHT_MOON_SHADOW: Int = 0xFF5A6B7D.toInt()

    val LIGHT_PALETTE =
        DialPalette(
            background = LIGHT_BACKGROUND,
            gold = LIGHT_GOLD,
            mutedGold = LIGHT_MUTED_GOLD,
            rim = LIGHT_RIM,
            sky = LIGHT_SKY,
            twilight = LIGHT_TWILIGHT,
            night = LIGHT_NIGHT,
            hand = LIGHT_HAND,
            moonIlluminated = LIGHT_MOON_ILLUMINATED,
            moonShadow = LIGHT_MOON_SHADOW,
            zodiacBand = LIGHT_ZODIAC_BAND,
            // The night region is the one dark surface left, where the bronze inks measure 1.04:1
            // (hand) to 1.44:1 (gold). A page-toned casing under the thin lines that cross it reads
            // at 9.99:1 there and disappears over the pale plate, so the hand and the reference
            // circles stay legible on both.
            casing = LIGHT_BACKGROUND,
        )

    fun paletteFor(appearance: DialAppearance, isSystemInNightMode: Boolean): DialPalette {
        val palette =
            when (appearance) {
                DialAppearance.LIGHT -> LIGHT_PALETTE
                DialAppearance.DARK -> DARK_PALETTE
                DialAppearance.SYSTEM -> if (isSystemInNightMode) DARK_PALETTE else LIGHT_PALETTE
            }
        return palette
    }
}

/** Two-tone boundary for markers crossing plate regions when the zodiac band is hidden. */
internal object MarkerOutline {
    const val CASING_WIDTH = 0.014f
    const val INK_WIDTH = 0.008f
}
