package io.github.godaniya.astronomicalclockswallpaper

import android.app.Activity
import android.widget.CheckBox

/** Initializes the layer controls from saved preferences and persists each explicit change. */
internal fun Activity.bindDialLayers(hasLocation: Boolean) {
    val store = DialSettingsStore(applicationContext)
    val zodiac = findViewById<CheckBox>(R.id.zodiac_ring)
    val sun = findViewById<CheckBox>(R.id.sun_layer)
    val moon = findViewById<CheckBox>(R.id.moon_layer)
    val saved = store.load()
    zodiac.isChecked = saved.isZodiacRingEnabled
    sun.isChecked = saved.isSunEnabled
    moon.isChecked = saved.isMoonEnabled
    zodiac.isEnabled = hasLocation
    sun.isEnabled = hasLocation
    moon.isEnabled = hasLocation
    zodiac.setOnCheckedChangeListener { _, checked ->
        store.save(store.load().copy(isZodiacRingEnabled = checked))
    }
    sun.setOnCheckedChangeListener { _, checked ->
        store.save(store.load().copy(isSunEnabled = checked))
    }
    moon.setOnCheckedChangeListener { _, checked ->
        store.save(store.load().copy(isMoonEnabled = checked))
    }
}

/** Updates whether the astronomical dial layers can be enabled based on observing location availability. */
internal fun Activity.updateDialLayersAvailability(hasLocation: Boolean) {
    findViewById<CheckBox>(R.id.zodiac_ring)?.isEnabled = hasLocation
    findViewById<CheckBox>(R.id.sun_layer)?.isEnabled = hasLocation
    findViewById<CheckBox>(R.id.moon_layer)?.isEnabled = hasLocation
}
