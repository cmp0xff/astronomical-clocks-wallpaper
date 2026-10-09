package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.content.SharedPreferences
import android.util.Log

/** Pure reads and explicit writes, independent of site, layers and appearance. */
internal class DialDisplayStore(context: Context) {
    private val preferences = context.getSharedPreferences("dial_display", Context.MODE_PRIVATE)

    fun load(): DialDisplaySettings {
        val snapshot = preferences.all
        val defaults = DialDisplaySettings()
        return DialDisplaySettings(
            size =
                percentage(
                    snapshot = snapshot,
                    key = "size",
                    default = defaults.size,
                    range = DialDisplaySettings.SIZE_RANGE,
                ),
            horizontal =
                percentage(
                    snapshot = snapshot,
                    key = "horizontal",
                    default = defaults.horizontal,
                    range = DialDisplaySettings.POSITION_RANGE,
                ),
            vertical =
                percentage(
                    snapshot = snapshot,
                    key = "vertical",
                    default = defaults.vertical,
                    range = DialDisplaySettings.POSITION_RANGE,
                ),
            brightness =
                percentage(
                    snapshot = snapshot,
                    key = "brightness",
                    default = defaults.brightness,
                    range = DialDisplaySettings.BRIGHTNESS_RANGE,
                ),
        )
    }

    private fun percentage(snapshot: Map<String, *>, key: String, default: Int, range: IntRange): Int {
        val value = snapshot[key]
        return if (value == null) {
            default
        } else if (value !is Number || !value.toDouble().isFinite() || value.toDouble() % 1.0 != 0.0) {
            Log.w("DialDisplayStore", "ignoring malformed display setting $key; using $default")
            default
        } else {
            value
                .toDouble()
                .coerceIn(minimumValue = range.first.toDouble(), maximumValue = range.last.toDouble())
                .toInt()
        }
    }

    fun save(settings: DialDisplaySettings) {
        preferences
            .edit()
            .putInt("size", settings.size)
            .putInt("horizontal", settings.horizontal)
            .putInt("vertical", settings.vertical)
            .putInt("brightness", settings.brightness)
            .apply()
    }

    fun registerListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {
        preferences.registerOnSharedPreferenceChangeListener(listener)
    }

    fun unregisterListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {
        preferences.unregisterOnSharedPreferenceChangeListener(listener)
    }
}
