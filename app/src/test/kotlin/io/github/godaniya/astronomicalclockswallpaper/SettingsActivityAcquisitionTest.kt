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
import android.widget.TextView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
import org.robolectric.shadows.ShadowToast
import java.time.Duration

/** Checks that a completed manual choice or destroyed activity cannot receive an old acquisition. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class SettingsActivityAcquisitionTest {
    private val application = RuntimeEnvironment.getApplication()
    private val locationManager = application.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private val locationShadow = shadowOf(locationManager)

    @Before
    fun enableLocation() {
        shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        locationShadow.enableNetworkProvider()
    }

    @Test
    fun manualSaveDiscardsQueuedFix() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            val listener = locationShadow.networkListeners().single()

            saveManualLocation(activity)
            assertTrue(locationShadow.networkListeners().isEmpty())
            // An update already queued by Android can still reach the listener after removeUpdates.
            listener.onLocationChanged(location())
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(11))

            assertManualLocation(activity)
            assertEquals(1, ShadowToast.shownToastCount())
            assertEquals(activity.getString(R.string.location_saved), ShadowToast.getTextOfLatestToast())
        }
    }

    @Test
    fun unchangedSaveKeepsAcquisition() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            val listener = locationShadow.networkListeners().single()
            listener.onLocationChanged(location())
            shadowOf(Looper.getMainLooper()).idle()

            acceptEstimatedZone()
            val stored = requireNotNull(LocationStore(activity).load())
            assertEquals(ObservingLocation.Source.CURRENT_COARSE, stored.source)

            // Trigger another refresh so a network listener is active
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            val refreshListener = locationShadow.networkListeners().single()

            // Save without editing coordinates
            activity.findViewById<Button>(R.id.save_location).performClick()
            assertTrue(locationShadow.networkListeners().isEmpty())

            // Queued fix does not overwrite
            val lateFix = Location(LocationManager.NETWORK_PROVIDER)
            lateFix.latitude = 50.0
            lateFix.longitude = 14.0
            refreshListener.onLocationChanged(lateFix)
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(11))

            val afterSave = requireNotNull(LocationStore(activity).load())
            assertEquals(ObservingLocation.Source.CURRENT_COARSE, afterSave.source)
            assertEquals(stored.latitude, afterSave.latitude, 0.0)
            assertEquals(stored.longitude, afterSave.longitude, 0.0)
            assertTrue(activity.findViewById<TextView>(R.id.location_current).text.contains("(current)"))
        }
    }

    @Test
    fun invalidInputKeepsAcquisition() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            activity.findViewById<Button>(R.id.save_location).performClick()

            assertEquals(activity.getString(R.string.location_invalid), ShadowToast.getTextOfLatestToast())
            assertEquals(1, locationShadow.networkListeners().size)
            locationShadow.simulateLocation(LocationManager.NETWORK_PROVIDER, location())
            shadowOf(Looper.getMainLooper()).idle()
            acceptEstimatedZone()

            assertEquals(
                ObservingLocation(
                    latitude = 37.42,
                    longitude = -122.08,
                    source = ObservingLocation.Source.CURRENT_COARSE,
                    zoneId = TimeZoneLookup.lookup(latitude = 37.42, longitude = -122.08),
                ),
                LocationStore(activity).load(),
            )
        }
    }

    @Test
    fun destroyDiscardsLateAcquisition() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            val listener = locationShadow.networkListeners().single()

            controller.pause().stop().destroy()
            assertTrue(locationShadow.networkListeners().isEmpty())
            listener.onLocationChanged(location())
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(11))

            assertNull(LocationStore(application).load())
            assertEquals(0, ShadowToast.shownToastCount())
        }
    }

    // Literal coordinates are test input, not user-facing text.
    @SuppressLint("SetTextI18n")
    private fun saveManualLocation(activity: SettingsActivity) {
        activity.findViewById<EditText>(R.id.latitude_input).setText("45.5")
        activity.findViewById<EditText>(R.id.longitude_input).setText("-120.25")
        activity.findViewById<Button>(R.id.save_location).performClick()
        acceptEstimatedZone()
    }

    private fun assertManualLocation(activity: SettingsActivity) {
        val expectedZone = TimeZoneLookup.lookup(latitude = 45.5, longitude = -120.25)
        assertEquals(
            ObservingLocation(
                latitude = 45.5,
                longitude = -120.25,
                source = ObservingLocation.Source.MANUAL,
                zoneId = expectedZone,
            ),
            LocationStore(activity).load(),
        )
        assertEquals(
            "45.5000, -120.2500 (manual)\nTimezone: ${expectedZone.id}",
            activity.findViewById<TextView>(R.id.location_current).text.toString(),
        )
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

    private fun location(): Location {
        val location = Location(LocationManager.NETWORK_PROVIDER)
        location.latitude = 37.42
        location.longitude = -122.08
        return location
    }
}
