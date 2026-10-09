package io.github.godaniya.astronomicalclockswallpaper

import android.app.Activity
import android.widget.Button
import android.widget.SeekBar
import android.widget.TextView

/** Native accessible sliders; only explicit user adjustments write preferences. */
internal fun Activity.bindDisplayControls() {
    val store = DialDisplayStore(applicationContext)
    val controls =
        listOf(
            DisplayControl(
                slider = R.id.display_size,
                label = R.id.display_size_label,
                text = R.string.display_size,
                range = DialDisplaySettings.SIZE_RANGE,
                read = { it.size },
                write = { value, saved -> saved.copy(size = value) },
            ),
            DisplayControl(
                slider = R.id.display_horizontal,
                label = R.id.display_horizontal_label,
                text = R.string.display_horizontal,
                range = DialDisplaySettings.POSITION_RANGE,
                read = { it.horizontal },
                write = { value, saved -> saved.copy(horizontal = value) },
            ),
            DisplayControl(
                slider = R.id.display_vertical,
                label = R.id.display_vertical_label,
                text = R.string.display_vertical,
                range = DialDisplaySettings.POSITION_RANGE,
                read = { it.vertical },
                write = { value, saved -> saved.copy(vertical = value) },
            ),
            DisplayControl(
                slider = R.id.display_brightness,
                label = R.id.display_brightness_label,
                text = R.string.display_brightness,
                range = DialDisplaySettings.BRIGHTNESS_RANGE,
                read = { it.brightness },
                write = { value, saved -> saved.copy(brightness = value) },
            ),
        )

    fun refresh() {
        val saved = store.load()
        for (control in controls) {
            val slider = findViewById<SeekBar>(control.slider)
            slider.min = control.range.first
            slider.max = control.range.last
            slider.progress = control.read(saved)
            findViewById<TextView>(control.label).text = getString(control.text, slider.progress)
        }
    }
    refresh()
    for (control in controls) {
        findViewById<SeekBar>(control.slider).setOnSeekBarChangeListener(
            object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(seekBar: SeekBar, progress: Int, fromUser: Boolean) {
                    findViewById<TextView>(control.label).text = getString(control.text, progress)
                    if (fromUser) store.save(control.write(progress, store.load()))
                }

                override fun onStartTrackingTouch(seekBar: SeekBar) = Unit

                override fun onStopTrackingTouch(seekBar: SeekBar) = Unit
            },
        )
    }
    findViewById<Button>(R.id.reset_display).setOnClickListener {
        store.save(DialDisplaySettings())
        refresh()
    }
}

private data class DisplayControl(
    val slider: Int,
    val label: Int,
    val text: Int,
    val range: IntRange,
    val read: (DialDisplaySettings) -> Int,
    val write: (Int, DialDisplaySettings) -> DialDisplaySettings,
)
