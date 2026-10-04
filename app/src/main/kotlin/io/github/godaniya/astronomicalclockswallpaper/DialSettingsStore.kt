package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.content.SharedPreferences
import android.util.Log

/** Persists dial layer visibility separately from the versioned observing location. */
internal class DialSettingsStore(context: Context) {
    private val preferences = context.getSharedPreferences("dial_settings", Context.MODE_PRIVATE)

    fun load(): DialLayers {
        val snapshot = preferences.all
        return DialLayers(
            isZodiacRingEnabled = enabled(snapshot, KEY_ZODIAC_RING),
            isSunEnabled = enabled(snapshot, KEY_SUN),
            isMoonEnabled = enabled(snapshot, KEY_MOON),
        )
    }

    private fun enabled(snapshot: Map<String, *>, key: String): Boolean {
        val value = snapshot[key]
        if (value != null && value !is Boolean) {
            Log.w("DialSettingsStore", "ignoring malformed dial setting $key; using enabled")
        }
        return value != false
    }

    fun save(layers: DialLayers) {
        preferences
            .edit()
            .putBoolean(KEY_ZODIAC_RING, layers.isZodiacRingEnabled)
            .putBoolean(KEY_SUN, layers.isSunEnabled)
            .putBoolean(KEY_MOON, layers.isMoonEnabled)
            .apply()
    }

    fun registerListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {
        preferences.registerOnSharedPreferenceChangeListener(listener)
    }

    fun unregisterListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {
        preferences.unregisterOnSharedPreferenceChangeListener(listener)
    }

    private companion object {
        const val KEY_ZODIAC_RING = "zodiac_ring"
        const val KEY_SUN = "sun"
        const val KEY_MOON = "moon"
    }
}
