package io.github.godaniya.astronomicalclockswallpaper

/** Persistent choice for the wallpaper and settings appearance. */
internal enum class DialAppearance {
    SYSTEM,
    LIGHT,
    DARK,
    ;

    companion object {
        fun fromString(value: String?): DialAppearance {
            val appearance =
                when (value) {
                    LIGHT.name -> LIGHT
                    DARK.name -> DARK
                    else -> SYSTEM
                }
            return appearance
        }
    }
}
