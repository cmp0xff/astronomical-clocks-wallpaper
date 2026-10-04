package io.github.godaniya.astronomicalclockswallpaper

/** Persistent choices for the Orloj foundation layers. */
internal data class DialLayers(
    val isZodiacRingEnabled: Boolean = true,
    val isSunEnabled: Boolean = true,
    val isMoonEnabled: Boolean = true,
)
