package io.github.godaniya.astronomicalclockswallpaper

/** Original Orloj-inspired palette; dimensions in the renderers are fractions of the sky radius. */
internal object DialStyle {
    // Font shaping needs ordinary-size text: subpixel textSize rounds glyph advances to zero.
    const val TEXT_UNITS = 1_000f
    const val BACKGROUND: Int = 0xFF101923.toInt()
    const val GOLD: Int = 0xFFD8B66A.toInt()
    const val MUTED_GOLD: Int = 0xFF887347.toInt()
    const val RIM: Int = 0xFF1C2C39.toInt()
    const val SKY: Int = 0xFF286078.toInt()
    const val TWILIGHT: Int = 0xFF9C6438.toInt()
    const val NIGHT: Int = 0xFF152433.toInt()
    const val HAND: Int = 0xFFF4E5B8.toInt()
    const val MOON_ILLUMINATED: Int = 0xFFE8EEF5.toInt()

    // The Moon marker rides the zodiac band, and ZodiacRenderer fills that band with NIGHT, so the
    // shadow tone must be its own colour: at NIGHT the unlit two-thirds of the disc vanished into
    // its background and only the gold rim separated a crescent from the band. This slate is 45
    // RGB units from NIGHT and at least 42 from every other entry, so the disc reads as a sphere
    // whether it is over the band (zodiac on) or the plain grid (zodiac off).
    const val MOON_SHADOW: Int = 0xFF2C3E50.toInt()
}
