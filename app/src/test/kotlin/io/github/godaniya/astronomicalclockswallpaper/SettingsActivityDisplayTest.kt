package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.view.KeyEvent
import android.widget.Button
import android.widget.SeekBar
import android.widget.TextView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class SettingsActivityDisplayTest {
    @Test
    fun adjustRecreateReset() {
        val context = RuntimeEnvironment.getApplication()
        val store = DialDisplayStore(context)
        DialSettingsStore(context)
            .save(DialLayers(isZodiacRingEnabled = false, isSunEnabled = false, isMoonEnabled = false))
        AppearanceStore(context).save(DialAppearance.DARK)
        val otherStores = listOf("observing_location", "dial_settings", "appearance_settings")
        val originals = otherStores.associateWith { context.getSharedPreferences(it, Context.MODE_PRIVATE).all }
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            var activity = controller.setup().get()
            val size = activity.findViewById<SeekBar>(R.id.display_size)
            assertTrue(size.isEnabled)
            assertEquals(50, size.min)
            assertEquals(115, size.max)
            repeat(20) {
                size.onKeyDown(KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DPAD_RIGHT))
            }
            assertEquals(115, store.load().size)
            controller.recreate()
            activity = controller.get()
            assertEquals(115, activity.findViewById<SeekBar>(R.id.display_size).progress)
            assertEquals(
                activity.getString(R.string.display_size, 115),
                activity
                    .findViewById<TextView>(R.id.display_size_label)
                    .text,
            )
            activity.findViewById<Button>(R.id.reset_display).performClick()
            assertEquals(DialDisplaySettings(), store.load())
            assertEquals(100, activity.findViewById<SeekBar>(R.id.display_size).progress)
        }
        for ((name, original) in originals) {
            assertEquals(original, context.getSharedPreferences(name, Context.MODE_PRIVATE).all)
        }
    }
}
