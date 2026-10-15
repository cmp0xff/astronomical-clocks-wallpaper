package io.github.godaniya.astronomicalclockswallpaper

internal data class TabletSurface(val name: String, val width: Int, val height: Int) {
    val aspectRatio: Float
        get() = width.toFloat() / height.toFloat()
}

internal object TabletSurfaces {
    val LANDSCAPE_16_10 = TabletSurface(name = "16:10 landscape", width = 2560, height = 1600)
    val LANDSCAPE_4_3 = TabletSurface(name = "4:3 landscape", width = 2048, height = 1536)
    val PORTRAIT_10_16 = TabletSurface(name = "10:16 portrait", width = 1600, height = 2560)
    val FOLDABLE_UNFOLDED = TabletSurface(name = "foldable unfolded", width = 2208, height = 1768)
    val FOLDABLE_FOLDED = TabletSurface(name = "foldable folded", width = 1080, height = 2400)

    val ALL =
        listOf(
            LANDSCAPE_16_10,
            LANDSCAPE_4_3,
            PORTRAIT_10_16,
            FOLDABLE_UNFOLDED,
            FOLDABLE_FOLDED,
        )
}
