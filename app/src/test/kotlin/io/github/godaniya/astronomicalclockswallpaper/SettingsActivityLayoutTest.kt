package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Rect
import android.view.View
import android.view.ViewGroup
import android.widget.ScrollView
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** Exercises the settings controls in a short window, including doubled text size. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36], qualifiers = "w320dp-h240dp-mdpi")
class SettingsActivityLayoutTest {
    @Test
    fun controlsScrollInShortViewport() {
        assertControlsReachable(fontScale = 1.0F)
    }

    @Test
    fun controlsScrollWithLargeText() {
        assertControlsReachable(fontScale = 2.0F)
    }

    private fun assertControlsReachable(fontScale: Float) {
        RuntimeEnvironment.setFontScale(fontScale)
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val scroll = activity.findViewById<ScrollView>(R.id.settings_scroll)
            scroll.measure(
                View.MeasureSpec.makeMeasureSpec(VIEWPORT_WIDTH, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(VIEWPORT_HEIGHT, View.MeasureSpec.EXACTLY),
            )
            scroll.layout(0, 0, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
            assertTrue("Short settings window must allow scrolling", scroll.canScrollVertically(1))

            listOf(
                R.id.use_current_location,
                R.id.refresh_location,
                R.id.latitude_input,
                R.id.longitude_input,
                R.id.select_timezone,
                R.id.save_location,
                R.id.appearance_system,
                R.id.appearance_light,
                R.id.appearance_dark,
                R.id.zodiac_ring,
                R.id.sun_layer,
                R.id.moon_layer,
                R.id.open_preview,
            ).forEach { id ->
                val control = activity.findViewById<View>(id)
                val content = scroll.getChildAt(0) as ViewGroup
                val bounds = Rect(0, 0, control.width, control.height)
                content.offsetDescendantRectToMyCoords(control, bounds)
                val controlTop = content.top + bounds.top
                scroll.scrollTo(0, controlTop - scroll.paddingTop)
                assertTrue("Control $id must have visible height", control.height > 0)
                assertTrue(
                    "Control $id must scroll below the viewport top",
                    controlTop >= scroll.scrollY + scroll.paddingTop,
                )
                assertTrue(
                    "Control $id must scroll above the viewport bottom",
                    content.top + bounds.bottom <= scroll.scrollY + scroll.height - scroll.paddingBottom,
                )
            }
        }
    }

    private companion object {
        const val VIEWPORT_WIDTH = 320
        const val VIEWPORT_HEIGHT = 240
    }
}
