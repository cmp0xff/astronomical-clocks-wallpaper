package io.github.godaniya.astronomicalclockswallpaper

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.os.Looper
import android.util.Log
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.ListView
import android.widget.TextView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowAlertDialog
import org.robolectric.shadows.ShadowLog
import java.time.ZoneId

/** Verifies manual coordinate resolution, estimate confirmation, picker overrides, and recreation. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class SettingsActivityManualTimezoneTest {
    private val application = RuntimeEnvironment.getApplication()

    @Test
    fun typingSuggestsNearestZone() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val button = activity.findViewById<Button>(R.id.select_timezone)

            enterCoordinates(activity = activity, latitude = "50.0875", longitude = "14.4206")
            assertEquals("Timezone: Europe/Prague", button.text.toString())

            enterCoordinates(activity = activity, latitude = "35.6762", longitude = "139.6503")
            assertEquals("Timezone: Asia/Tokyo", button.text.toString())
        }
    }

    @Test
    fun pickerOverridesAnchor() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val button = activity.findViewById<Button>(R.id.select_timezone)

            enterCoordinates(activity = activity, latitude = "50.0875", longitude = "14.4206")
            assertEquals("Timezone: Europe/Prague", button.text.toString())

            button.performClick()
            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            clickZone(dialog = dialog, zoneId = "Pacific/Honolulu")
            assertEquals("Timezone: Pacific/Honolulu", button.text.toString())

            // Editing coordinates after explicit choice must NOT overwrite user's selected timezone
            enterCoordinates(activity = activity, latitude = "40.7128", longitude = "-74.0060")
            assertEquals("Timezone: Pacific/Honolulu", button.text.toString())

            activity.findViewById<Button>(R.id.save_location).performClick()
            val saved = LocationStore(application).load()
            assertNotNull(saved)
            assertEquals(ZoneId.of("Pacific/Honolulu"), saved?.zoneId)
            assertEquals(ObservingLocation.Source.MANUAL, saved?.source)
            assertTrue(activity.findViewById<TextView>(R.id.location_current).text.contains("Pacific/Honolulu"))
        }
    }

    @Test
    fun explicitZoneSurvivesRecreate() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(activity = activity, latitude = "50.0875", longitude = "14.4206")

            activity.findViewById<Button>(R.id.select_timezone).performClick()
            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            clickZone(dialog = dialog, zoneId = "America/Chicago")

            controller.recreate()
            val recreated = controller.get()
            val button = recreated.findViewById<Button>(R.id.select_timezone)
            assertEquals("Timezone: America/Chicago", button.text.toString())

            // Saving after recreation preserves the selected timezone
            recreated.findViewById<Button>(R.id.save_location).performClick()
            val saved = LocationStore(application).load()
            assertEquals(ZoneId.of("America/Chicago"), saved?.zoneId)
        }
    }

    @Test
    fun savedLocationInitializesZone() {
        LocationStore(application).save(
            ObservingLocation(
                latitude = 48.8566,
                longitude = 2.3522,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.of("Europe/Paris"),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val button = activity.findViewById<Button>(R.id.select_timezone)
            assertEquals("Timezone: Europe/Paris", button.text.toString())

            // Entering new coordinates suggests the new location's anchor
            enterCoordinates(activity = activity, latitude = "-33.8688", longitude = "151.2093")
            assertEquals("Timezone: Australia/Sydney", button.text.toString())
        }
    }

    // A valid saved zone with no zone.tab anchor, which a migrated record can hold, must be shown
    // and highlighted rather than replaced by the alphabetically first entry.
    @Test
    fun pickerPreselectsNonAnchorZone() {
        LocationStore(application).save(
            ObservingLocation(
                latitude = 50.0875,
                longitude = 14.4206,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.of("Etc/GMT+2"),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.select_timezone).performClick()
            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog

            val list = dialog.findViewById<ListView>(R.id.timezone_list)
            val adapter = list.adapter
            val entries = (0 until adapter.count).map { adapter.getItem(it) }
            assertEquals("Etc/GMT+2", entries[list.checkedItemPosition])
        }
    }

    // An estimated zone is offered before it becomes the site's civil time: cancelling leaves the
    // site unsaved, and only accepting the estimate writes it.
    @Test
    fun estimateNotSavedUntilConfirmed() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(activity = activity, latitude = "45.5", longitude = "-120.25")
            activity.findViewById<Button>(R.id.save_location).performClick()

            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            assertTrue(shadowOf(dialog).message.toString().contains("America/Boise"))
            assertNull(LocationStore(application).load())

            clickDialogButton(dialog, AlertDialog.BUTTON_NEGATIVE)
            assertNull(LocationStore(application).load())

            activity.findViewById<Button>(R.id.save_location).performClick()
            val confirmed = ShadowAlertDialog.getLatestDialog() as AlertDialog
            clickDialogButton(confirmed, AlertDialog.BUTTON_POSITIVE)

            assertEquals(ZoneId.of("America/Boise"), LocationStore(application).load()?.zoneId)
        }
    }

    // The estimate dialog's Choose… path opens the picker and must hand the picked zone to the
    // save, overriding the estimate that prompted it.
    @Test
    fun chooseFromEstimateSavesChoice() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(activity = activity, latitude = "45.5", longitude = "-120.25")
            activity.findViewById<Button>(R.id.save_location).performClick()

            val estimateDialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            clickDialogButton(estimateDialog, AlertDialog.BUTTON_NEUTRAL)

            val picker = ShadowAlertDialog.getLatestDialog() as AlertDialog
            val targetZone = "Pacific/Honolulu"
            clickZone(dialog = picker, zoneId = targetZone)

            assertEquals(ZoneId.of(targetZone), LocationStore(application).load()?.zoneId)
            assertTrue(activity.findViewById<TextView>(R.id.location_current).text.contains(targetZone))
        }
    }

    // After Choose…, abandoning the picker saves nothing and must say so exactly once: the
    // confirmation's own listener stays quiet because isChosen was set on the Choose button.
    @Test
    fun abandonedPickerSavesNothing() {
        ShadowLog.clear()
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(activity = activity, latitude = "45.5", longitude = "-120.25")
            activity.findViewById<Button>(R.id.save_location).performClick()
            val estimate = ShadowAlertDialog.getLatestDialog() as AlertDialog
            clickDialogButton(estimate, AlertDialog.BUTTON_NEUTRAL) // Choose…
            val picker = ShadowAlertDialog.getLatestDialog() as AlertDialog
            clickDialogButton(picker, AlertDialog.BUTTON_NEGATIVE) // Cancel
        }
        val entries = ShadowLog.getLogsForTag("TimeZoneControls")
        assertEquals(1, entries.size)
        assertEquals(Log.INFO, entries.single().type)
        assertTrue(entries.single().msg.contains("without a selection"))
        assertNull(LocationStore(application).load())
    }

    // The owner's request end to end: a typed query narrows the offered rows and the narrowed row is
    // what the picker commits through Save.
    @Test
    fun filterNarrowsPickerChoices() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(activity = activity, latitude = "50.0875", longitude = "14.4206")
            activity.findViewById<Button>(R.id.select_timezone).performClick()
            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            val list = dialog.findViewById<ListView>(R.id.timezone_list)
            val fullCount = list.adapter.count

            typeFilter(dialog = dialog, query = "new york")
            assertEquals(1, list.adapter.count)
            assertTrue(fullCount > list.adapter.count)

            clickZone(dialog = dialog, zoneId = "America/New_York")
            activity.findViewById<Button>(R.id.save_location).performClick()
            assertEquals(ZoneId.of("America/New_York"), LocationStore(application).load()?.zoneId)
        }
    }

    // A second query must still narrow the full list. This catches an adapter that aliases the
    // source list: the first keystroke's clear() would empty the source for every later query.
    @Test
    fun secondQueryStillNarrows() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.select_timezone).performClick()
            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            val list = dialog.findViewById<ListView>(R.id.timezone_list)

            typeFilter(dialog = dialog, query = "pacific")
            assertTrue(list.adapter.count > 1)
            typeFilter(dialog = dialog, query = "new york")

            assertEquals(listOf("America/New_York"), (0 until list.adapter.count).map { list.adapter.getItem(it) })
        }
    }

    // A query that matches nothing empties the list, shows the no-match message, and commits nothing.
    @Test
    fun filterWithNoMatchShowsEmpty() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val button = activity.findViewById<Button>(R.id.select_timezone)
            val before = button.text.toString()
            button.performClick()
            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            val list = dialog.findViewById<ListView>(R.id.timezone_list)

            typeFilter(dialog = dialog, query = "not a real zone")
            assertEquals(0, list.adapter.count)
            assertEquals(View.VISIBLE, dialog.findViewById<View>(R.id.timezone_empty).visibility)
            // The highlight must not survive on a row the filter removed.
            assertEquals(-1, list.checkedItemPosition)
            assertEquals(before, button.text.toString())
            assertNull(LocationStore(application).load())
        }
    }

    // IME Done with a single remaining match picks it, so the search box can complete the choice
    // without a tap on the row.
    @Test
    fun enterSelectsSoleMatch() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.select_timezone).performClick()
            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            val list = dialog.findViewById<ListView>(R.id.timezone_list)

            typeFilter(dialog = dialog, query = "new york")
            assertEquals(1, list.adapter.count)
            dialog.findViewById<EditText>(R.id.timezone_filter).onEditorAction(EditorInfo.IME_ACTION_DONE)

            assertEquals(
                "Timezone: America/New_York",
                activity.findViewById<Button>(R.id.select_timezone).text.toString(),
            )
        }
    }

    // A dialog with a custom view has no AlertDialog.listView; the rows live in the inflated
    // R.id.timezone_list, and the click must resolve the position in that adapter.
    private fun clickZone(dialog: AlertDialog, zoneId: String) {
        val list = dialog.findViewById<ListView>(R.id.timezone_list)
        val adapter = list.adapter
        val index = (0 until adapter.count).first { adapter.getItem(it) == zoneId }
        shadowOf(list).performItemClick(index)
    }

    // Typing a literal filter query is a test input, not user-facing text.
    @SuppressLint("SetTextI18n")
    private fun typeFilter(dialog: AlertDialog, query: String) {
        dialog.findViewById<EditText>(R.id.timezone_filter).setText(query)
    }

    // A dialog dispatches its button click through a message on the main looper, so the write it
    // triggers has not happened until that looper drains.
    private fun clickDialogButton(dialog: AlertDialog, which: Int) {
        dialog.getButton(which).performClick()
        shadowOf(Looper.getMainLooper()).idle()
    }

    @SuppressLint("SetTextI18n")
    private fun enterCoordinates(activity: SettingsActivity, latitude: String, longitude: String) {
        activity.findViewById<EditText>(R.id.latitude_input).setText(latitude)
        activity.findViewById<EditText>(R.id.longitude_input).setText(longitude)
    }
}
