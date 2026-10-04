package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.Context
import android.location.Location
import android.location.LocationManager
import android.os.Looper
import android.widget.Button
import android.widget.EditText
import android.widget.ListView
import android.widget.TextView
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowAlertDialog
import java.time.ZoneId
import java.util.TimeZone

/** Resolves each fix to its own geographic zone, and keeps a confirmed zone across refreshes,
 *  recreation, and phone-timezone changes. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class SettingsActivityTimezoneTest {
    private val originalTimezone = TimeZone.getDefault()
    private val application = RuntimeEnvironment.getApplication()

    @Before
    fun setInitialTimezone() {
        TimeZone.setDefault(TimeZone.getTimeZone("Europe/Prague"))
    }

    @After
    fun restoreTimezone() {
        TimeZone.setDefault(originalTimezone)
    }

    // Current location acquisition resolves the acquired fix's geographic timezone even when
    // the phone timezone is configured differently.
    @Test
    fun acquisitionResolvesGeoZone() {
        shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        val manager = application.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val locationShadow = shadowOf(manager)
        locationShadow.enableNetworkProvider()
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            assertEquals(1, locationShadow.networkListeners().size)

            TimeZone.setDefault(TimeZone.getTimeZone("Pacific/Auckland"))
            val fix = Location(LocationManager.NETWORK_PROVIDER)
            fix.latitude = 37.42
            fix.longitude = -122.08
            locationShadow.simulateLocation(LocationManager.NETWORK_PROVIDER, fix)
            shadowOf(Looper.getMainLooper()).idle()

            acceptEstimatedZone()

            assertSavedTimezone(activity, "America/Los_Angeles")
        }
    }

    // Acquiring a new location updates both coordinates and geographic timezone to the new fix,
    // and neither is affected by subsequent phone-timezone changes.
    @Test
    fun acquisitionIndependentOfPhone() {
        shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        val manager = application.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val locationShadow = shadowOf(manager)
        locationShadow.enableNetworkProvider()
        LocationStore(application).save(
            ObservingLocation(
                latitude = 50.0875,
                longitude = 14.4206,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.of("Europe/Prague"),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            // Set phone to a distinct timezone before acquisition
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Kolkata"))
            for (buttonId in listOf(R.id.use_current_location, R.id.refresh_location)) {
                val fix = Location(LocationManager.NETWORK_PROVIDER)
                fix.latitude = 37.42
                fix.longitude = -122.08
                activity.findViewById<Button>(buttonId).performClick()
                locationShadow.simulateLocation(LocationManager.NETWORK_PROVIDER, fix)
                shadowOf(Looper.getMainLooper()).idle()

                acceptEstimatedZone()
                assertSavedTimezone(activity, "America/Los_Angeles")
                val saved = LocationStore(application).load()
                assertEquals(37.42, saved?.latitude)
                assertEquals(-122.08, saved?.longitude)
                assertEquals(ObservingLocation.Source.CURRENT_COARSE, saved?.source)

                // Phone-timezone changes after acquisition alter neither coordinates nor timezone
                TimeZone.setDefault(TimeZone.getTimeZone("Pacific/Auckland"))
                assertSavedTimezone(activity, "America/Los_Angeles")

                // Save untouched must preserve CURRENT_COARSE and America/Los_Angeles
                activity.findViewById<Button>(R.id.save_location).performClick()
                assertSavedTimezone(activity, "America/Los_Angeles")
                val savedAfterSave = LocationStore(application).load()
                assertEquals(37.42, savedAfterSave?.latitude)
                assertEquals(-122.08, savedAfterSave?.longitude)
                assertEquals(ObservingLocation.Source.CURRENT_COARSE, savedAfterSave?.source)
            }
        }
    }

    @Test
    fun coldOpenSavePreservesCurrent() {
        LocationStore(application).save(
            ObservingLocation(
                latitude = 50.0875,
                longitude = 14.4206,
                source = ObservingLocation.Source.CURRENT_COARSE,
                zoneId = ZoneId.of("Europe/Prague"),
            ),
        )
        TimeZone.setDefault(TimeZone.getTimeZone("Pacific/Auckland"))
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.save_location).performClick()

            assertSavedTimezone(activity, "Europe/Prague")
            val saved = LocationStore(application).load()
            assertEquals(ObservingLocation.Source.CURRENT_COARSE, saved?.source)
            assertEquals(ZoneId.of("Europe/Prague"), saved?.zoneId)
            assertTrue(activity.findViewById<TextView>(R.id.location_current).text.contains("(current)"))
        }
    }

    @Test
    @SuppressLint("SetTextI18n")
    fun editedCoordsResolveAnchorZone() {
        LocationStore(application).save(
            ObservingLocation(
                latitude = 50.0875,
                longitude = 14.4206,
                source = ObservingLocation.Source.CURRENT_COARSE,
                zoneId = ZoneId.of("Europe/Prague"),
            ),
        )
        TimeZone.setDefault(TimeZone.getTimeZone("Pacific/Auckland"))
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<EditText>(R.id.latitude_input).setText("51.5074")
            activity.findViewById<Button>(R.id.save_location).performClick()
            acceptEstimatedZone()

            assertSavedTimezone(activity, "Europe/Berlin")
            val saved = LocationStore(application).load()
            assertEquals(ObservingLocation.Source.MANUAL, saved?.source)
            assertEquals(ZoneId.of("Europe/Berlin"), saved?.zoneId)
            assertTrue(activity.findViewById<TextView>(R.id.location_current).text.contains("(manual)"))
        }
    }

    @Test
    fun manualEntryResolvesAnchorZone() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(activity, latitude = "45.5", longitude = "-120.25")
            TimeZone.setDefault(TimeZone.getTimeZone("America/New_York"))
            activity.findViewById<Button>(R.id.save_location).performClick()
            acceptEstimatedZone()

            assertSavedTimezone(activity, "America/Boise")
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Tokyo"))
            controller.recreate()
            assertSavedTimezone(controller.get(), "America/Boise")
        }
    }

    @Test
    fun manualEntryAllowsExplicitZone() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(activity, latitude = "45.5", longitude = "-120.25")
            activity.findViewById<Button>(R.id.select_timezone).performClick()

            val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
            val targetZone = "America/New_York"
            clickZone(dialog = dialog, zoneId = targetZone)

            activity.findViewById<Button>(R.id.save_location).performClick()
            assertSavedTimezone(activity, targetZone)
        }
    }

    @Test
    fun explicitSavedZoneIsDisplayed() {
        LocationStore(application).save(
            ObservingLocation(
                latitude = -33.87,
                longitude = 151.21,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.of("Australia/Sydney"),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            assertSavedTimezone(activity, "Australia/Sydney")
            assertEquals(
                activity.getString(R.string.location_timezone_help),
                activity.findViewById<TextView>(R.id.location_timezone_help).text.toString(),
            )
        }
    }

    // A zone that came from the nearest-anchor lookup is presented as an estimate and must be
    // confirmed before it is written; accepting it is the path these tests exercise. The dialog
    // dispatches its button click through a message on the main looper, so the write it triggers
    // has not happened until that looper drains.
    private fun acceptEstimatedZone() {
        val dialog = ShadowAlertDialog.getLatestDialog() as AlertDialog
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).performClick()
        shadowOf(Looper.getMainLooper()).idle()
    }

    private fun enterCoordinates(activity: SettingsActivity, latitude: String, longitude: String) {
        activity.findViewById<EditText>(R.id.latitude_input).setText(latitude)
        activity.findViewById<EditText>(R.id.longitude_input).setText(longitude)
    }

    // A dialog with a custom view has no AlertDialog.listView; the rows live in the inflated
    // R.id.timezone_list, and the click must resolve the position in that adapter.
    private fun clickZone(dialog: AlertDialog, zoneId: String) {
        val list = dialog.findViewById<ListView>(R.id.timezone_list)
        val adapter = list.adapter
        val index = (0 until adapter.count).first { adapter.getItem(it) == zoneId }
        shadowOf(list).performItemClick(index)
    }

    private fun assertSavedTimezone(activity: SettingsActivity, expectedZone: String) {
        val saved = LocationStore(application).load()
        assertNotNull(saved)
        assertEquals(ZoneId.of(expectedZone), saved?.zoneId)
        assertTrue(activity.findViewById<TextView>(R.id.location_current).text.contains(expectedZone))
    }
}
