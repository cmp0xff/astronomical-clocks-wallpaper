package io.github.godaniya.astronomicalclockswallpaper

import android.app.Activity
import android.widget.RadioButton
import android.widget.RadioGroup

/** Applies the saved appearance theme before activity layout inflation. */
internal fun Activity.applyAppearanceTheme() {
    val store = AppearanceStore(applicationContext)
    when (store.load()) {
        DialAppearance.LIGHT -> setTheme(R.style.AppTheme_Light)
        DialAppearance.DARK -> setTheme(R.style.AppTheme_Dark)
        DialAppearance.SYSTEM -> setTheme(R.style.AppTheme)
    }
}

/** Initializes the appearance selection controls and persists user changes. */
internal fun Activity.bindAppearanceControls() {
    val store = AppearanceStore(applicationContext)
    val group = findViewById<RadioGroup>(R.id.appearance_group)
    val systemButton = findViewById<RadioButton>(R.id.appearance_system)
    val lightButton = findViewById<RadioButton>(R.id.appearance_light)
    val darkButton = findViewById<RadioButton>(R.id.appearance_dark)

    when (store.load()) {
        DialAppearance.SYSTEM -> systemButton.isChecked = true
        DialAppearance.LIGHT -> lightButton.isChecked = true
        DialAppearance.DARK -> darkButton.isChecked = true
    }

    group.setOnCheckedChangeListener { _, checkedId ->
        val newAppearance =
            when (checkedId) {
                R.id.appearance_light -> DialAppearance.LIGHT
                R.id.appearance_dark -> DialAppearance.DARK
                else -> DialAppearance.SYSTEM
            }
        if (store.load() != newAppearance) {
            store.save(newAppearance)
            recreate()
        }
    }
}
