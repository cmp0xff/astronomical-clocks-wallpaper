package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.widget.CheckBox
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog
import java.time.ZoneId

/** Verifies default layer choices, persistence, and the Settings controls that change them. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class DialSettingsStoreTest {
    private val application = RuntimeEnvironment.getApplication()

    @Test
    fun layersDefaultToEnabled() {
        assertEquals(
            DialLayers(isZodiacRingEnabled = true, isSunEnabled = true),
            DialSettingsStore(application)
                .load(),
        )
    }

    @Test
    fun choicesSurviveNewStore() {
        for (isZodiacEnabled in listOf(false, true)) {
            for (isSun in listOf(false, true)) {
                val expected =
                    DialLayers(
                        isZodiacRingEnabled = isZodiacEnabled,
                        isSunEnabled = isSun,
                    )
                DialSettingsStore(application).save(expected)
                assertEquals(expected, DialSettingsStore(application).load())
            }
        }
    }

    @Test
    fun staleDayAndNightKeyIsIgnored() {
        // The layer was renamed from day_and_night to sun before release, so a stored old key is
        // simply unread and the toggle returns to its enabled default.
        application
            .getSharedPreferences("dial_settings", Context.MODE_PRIVATE)
            .edit()
            .putBoolean("day_and_night", false)
            .apply()
        assertEquals(DialLayers(), DialSettingsStore(application).load())
    }

    @Test
    fun badSettingKeepsOtherChoice() {
        val preferences = application.getSharedPreferences("dial_settings", Context.MODE_PRIVATE)
        preferences
            .edit()
            .putString("zodiac_ring", "broken")
            .putBoolean("sun", false)
            .apply()
        assertEquals(
            DialLayers(isZodiacRingEnabled = true, isSunEnabled = false),
            DialSettingsStore(application)
                .load(),
        )
        assertEquals("broken", preferences.getString("zodiac_ring", null))
        assertEquals(1, ShadowLog.getLogsForTag("DialSettingsStore").size)
    }

    @Test
    fun settingsTogglesPersist() {
        LocationStore(application).save(
            ObservingLocation(
                latitude = 50.0875,
                longitude = 14.4206,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.systemDefault(),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val zodiac = activity.findViewById<CheckBox>(R.id.zodiac_ring)
            val sun = activity.findViewById<CheckBox>(R.id.sun_layer)
            assertTrue(zodiac.isEnabled)
            assertTrue(sun.isEnabled)
            assertTrue(zodiac.isChecked)
            assertTrue(sun.isChecked)
            zodiac.performClick()
            assertEquals(
                DialLayers(isZodiacRingEnabled = false, isSunEnabled = true),
                DialSettingsStore(activity)
                    .load(),
            )
            sun.performClick()
            controller.recreate()
            val recreated = controller.get()
            assertTrue(recreated.findViewById<CheckBox>(R.id.zodiac_ring).isEnabled)
            assertTrue(recreated.findViewById<CheckBox>(R.id.sun_layer).isEnabled)
            assertFalse(recreated.findViewById<CheckBox>(R.id.zodiac_ring).isChecked)
            assertFalse(recreated.findViewById<CheckBox>(R.id.sun_layer).isChecked)
            recreated.findViewById<CheckBox>(R.id.zodiac_ring).performClick()
            assertEquals(
                DialLayers(isZodiacRingEnabled = true, isSunEnabled = false),
                DialSettingsStore(activity)
                    .load(),
            )
        }
    }
}
