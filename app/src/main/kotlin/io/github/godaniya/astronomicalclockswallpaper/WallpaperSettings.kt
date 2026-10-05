package io.github.godaniya.astronomicalclockswallpaper

/** One immutable settings snapshot used by a whole wallpaper frame. */
internal data class WallpaperSettings(
    val location: ObservingLocation?,
    val layers: DialLayers,
    val appearance: DialAppearance = DialAppearance.SYSTEM,
)
