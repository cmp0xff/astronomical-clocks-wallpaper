package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.content.SharedPreferences
import android.util.Log

/** Persists the appearance theme choice separately from observing location and layer visibility. */
internal class AppearanceStore(context: Context) {
    private val preferences = context.getSharedPreferences("appearance_settings", Context.MODE_PRIVATE)

    fun load(): DialAppearance {
        val snapshot = preferences.all
        return when (val raw = snapshot[KEY_APPEARANCE]) {
            null -> {
                DialAppearance.SYSTEM
            }

            is String -> {
                // An absent key and an unrecognized value both resolve to SYSTEM, but only the
                // latter is a fault: it means corrupted storage or a value a newer build wrote, and
                // without this line a user who chose a theme would see it silently revert.
                val appearance = DialAppearance.fromString(raw)
                if (appearance == DialAppearance.SYSTEM && raw != DialAppearance.SYSTEM.name) {
                    Log.w(TAG, "ignoring unrecognized appearance value \"$raw\"; using system default")
                }
                appearance
            }

            else -> {
                Log.w(TAG, "ignoring malformed appearance setting $raw; using system default")
                DialAppearance.SYSTEM
            }
        }
    }

    fun save(appearance: DialAppearance) {
        preferences
            .edit()
            .putString(KEY_APPEARANCE, appearance.name)
            .apply()
    }

    fun registerListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {
        preferences.registerOnSharedPreferenceChangeListener(listener)
    }

    fun unregisterListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {
        preferences.unregisterOnSharedPreferenceChangeListener(listener)
    }

    companion object {
        const val KEY_APPEARANCE = "appearance"
        private const val TAG = "AppearanceStore"
    }
}
