package io.github.godaniya.astronomicalclockswallpaper

/** One render's appearance, controls and engine-resolved usable rectangle. */
internal data class DialRenderStyle(
    val palette: DialPalette,
    val display: DialDisplaySettings,
    val viewport: DialViewport,
)
