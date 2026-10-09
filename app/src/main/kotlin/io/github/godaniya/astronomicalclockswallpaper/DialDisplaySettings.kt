package io.github.godaniya.astronomicalclockswallpaper

/** Integer percentages controlling composition and whole-wallpaper dimming. */
internal data class DialDisplaySettings(
    val size: Int = 100,
    val horizontal: Int = 50,
    val vertical: Int = 50,
    val brightness: Int = 100,
) {
    init {
        require(size in SIZE_RANGE)
        require(horizontal in POSITION_RANGE && vertical in POSITION_RANGE)
        require(brightness in BRIGHTNESS_RANGE)
    }

    companion object {
        val SIZE_RANGE = 50..115
        val POSITION_RANGE = 0..100
        val BRIGHTNESS_RANGE = 80..100
    }
}
