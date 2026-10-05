package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.util.TypedValue
import android.widget.RadioButton
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class SettingsActivityAppearanceTest {
    private val application = RuntimeEnvironment.getApplication()

    @Before
    fun setUp() {
        application
            .getSharedPreferences("appearance_settings", Context.MODE_PRIVATE)
            .edit()
            .clear()
            .apply()
    }

    @Test
    fun defaultSelectionIsSystem() {
        val controller = Robolectric.buildActivity(SettingsActivity::class.java).setup()
        val activity = controller.get()
        val systemRadio = activity.findViewById<RadioButton>(R.id.appearance_system)
        assertTrue(systemRadio.isChecked)
    }

    @Test
    fun selectingLightPersistsChoice() {
        val controller = Robolectric.buildActivity(SettingsActivity::class.java).setup()
        val activity = controller.get()
        val lightRadio = activity.findViewById<RadioButton>(R.id.appearance_light)
        lightRadio.performClick()

        val store = AppearanceStore(application)
        assertEquals(DialAppearance.LIGHT, store.load())
    }

    @Test
    fun selectingDarkPersistsChoice() {
        val controller = Robolectric.buildActivity(SettingsActivity::class.java).setup()
        val activity = controller.get()
        val darkRadio = activity.findViewById<RadioButton>(R.id.appearance_dark)
        darkRadio.performClick()

        val store = AppearanceStore(application)
        assertEquals(DialAppearance.DARK, store.load())
    }

    @Test
    fun savedChoiceSurvivesRecreate() {
        val store = AppearanceStore(application)
        store.save(DialAppearance.DARK)

        val controller = Robolectric.buildActivity(SettingsActivity::class.java).setup()
        val darkRadio = controller.get().findViewById<RadioButton>(R.id.appearance_dark)
        assertTrue(darkRadio.isChecked)

        val recreated = controller.recreate().get()
        val recreatedDarkRadio = recreated.findViewById<RadioButton>(R.id.appearance_dark)
        assertTrue(recreatedDarkRadio.isChecked)
    }

    // The radio selection alone does not prove the screen re-themed: this checks the window background
    // the theme actually resolved to, so dropping setTheme(), swapping the LIGHT/DARK style names, or
    // applying a theme that never reaches the window all fail here.
    @Test
    fun savedThemeReachesTheWindow() {
        AppearanceStore(application).save(DialAppearance.DARK)
        val darkActivity = Robolectric.buildActivity(SettingsActivity::class.java).setup().get()
        assertEquals(0xFF111923.toInt(), windowBackgroundOf(darkActivity))

        AppearanceStore(application).save(DialAppearance.LIGHT)
        val lightActivity = Robolectric.buildActivity(SettingsActivity::class.java).setup().get()
        assertEquals(0xFFF7F4EB.toInt(), windowBackgroundOf(lightActivity))
    }

    // Reads the activity's own resolved theme, which is what applyAppearanceTheme() sets before
    // super.onCreate. That catches a missing setTheme and a swapped LIGHT/DARK mapping; it does not
    // distinguish the order of setTheme relative to super.onCreate, which the theme object alone
    // cannot show.
    private fun windowBackgroundOf(activity: SettingsActivity): Int {
        val value = TypedValue()
        activity.theme.resolveAttribute(android.R.attr.windowBackground, value, true)
        return value.data
    }
}
