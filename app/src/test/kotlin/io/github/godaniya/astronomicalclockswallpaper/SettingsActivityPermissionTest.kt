package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.app.AlertDialog
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Looper
import android.os.SystemClock
import android.widget.Button
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotSame
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
import org.robolectric.shadows.ShadowToast

/** Checks permission-result edge cases and preserves the requested cache policy across recreation. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class SettingsActivityPermissionTest {
    private val application = RuntimeEnvironment.getApplication()
    private val locationManager = application.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private val locationShadow = shadowOf(locationManager)

    @Test
    fun recreatedRefreshIgnoresCache() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val originalActivity = controller.setup().get()
            seedRecentCache()
            requestPermission(originalActivity, R.id.refresh_location)
            val recreatedActivity = controller.recreate().get()

            grantPermission(recreatedActivity)

            assertNull(LocationStore(application).load())
            assertEquals(1, locationShadow.networkListeners().size)
            locationShadow
                .simulateLocation(LocationManager.NETWORK_PROVIDER, location(latitude = 37.42, longitude = -122.08))
            shadowOf(Looper.getMainLooper()).idle()
            acceptEstimatedZone()
            assertEquals(
                ObservingLocation(
                    latitude = 37.42,
                    longitude = -122.08,
                    source = ObservingLocation.Source.CURRENT_COARSE,
                    zoneId = TimeZoneLookup.lookup(latitude = 37.42, longitude = -122.08),
                ),
                LocationStore(application).load(),
            )
        }
    }

    @Test
    fun recreatedUseCurrentAllowsCache() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val originalActivity = controller.setup().get()
            seedRecentCache()
            requestPermission(originalActivity, R.id.use_current_location)
            val recreatedActivity = controller.recreate().get()

            grantPermission(recreatedActivity)

            acceptEstimatedZone()
            assertEquals(
                ObservingLocation(
                    latitude = 1.0,
                    longitude = 2.0,
                    source = ObservingLocation.Source.CURRENT_COARSE,
                    zoneId = TimeZoneLookup.lookup(latitude = 1.0, longitude = 2.0),
                ),
                LocationStore(application).load(),
            )
            assertTrue(locationShadow.networkListeners().isEmpty())
        }
    }

    @Test
    fun unknownPermissionIsIgnored() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            seedRecentCache()
            shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)

            activity.onRequestPermissionsResult(
                REQUEST_LOCATION_PERMISSION + 1,
                arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION),
                intArrayOf(PackageManager.PERMISSION_GRANTED),
            )

            assertNull(LocationStore(application).load())
            assertTrue(locationShadow.networkListeners().isEmpty())
            assertEquals(0, ShadowToast.shownToastCount())
        }
    }

    @Test
    fun emptyResultIsInterrupted() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            requestPermission(activity, R.id.use_current_location)

            activity.onRequestPermissionsResult(REQUEST_LOCATION_PERMISSION, emptyArray(), intArrayOf())

            assertNull(LocationStore(application).load())
            assertTrue(locationShadow.networkListeners().isEmpty())
            assertEquals(
                activity.getString(R.string.location_permission_interrupted),
                ShadowToast.getTextOfLatestToast(),
            )
            assertTrue(
                ShadowLog.getLogsForTag("SettingsActivity").any {
                    it.msg == "location permission request interrupted; no acquisition started"
                },
            )
            // No denial observation is recorded, so the next tap issues a fresh request instead of
            // showing the recovery dialog.
            val originalRequest = shadowOf(activity).lastRequestedPermission
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            assertNotSame(originalRequest, shadowOf(activity).lastRequestedPermission)
        }
    }

    @Test
    fun unrelatedGrantIsIgnored() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            requestPermission(activity, R.id.use_current_location)
            activity.onRequestPermissionsResult(
                REQUEST_LOCATION_PERMISSION,
                arrayOf(Manifest.permission.CAMERA),
                intArrayOf(PackageManager.PERMISSION_GRANTED),
            )
            assertNull(LocationStore(application).load())
            assertTrue(locationShadow.networkListeners().isEmpty())
            assertEquals(0, ShadowToast.shownToastCount())
            assertTrue(
                ShadowLog.getLogsForTag("SettingsActivity").any {
                    it.msg == "ignoring location permission result for unrelated permissions"
                },
            )
            val originalRequest = shadowOf(activity).lastRequestedPermission
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            assertNotSame(originalRequest, shadowOf(activity).lastRequestedPermission)
        }
    }

    @Test
    fun unrelatedKeepsFreshPending() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            seedRecentCache()
            requestPermission(activity, R.id.refresh_location)
            activity.onRequestPermissionsResult(
                REQUEST_LOCATION_PERMISSION,
                arrayOf(Manifest.permission.CAMERA),
                intArrayOf(PackageManager.PERMISSION_GRANTED),
            )
            assertEquals(0, ShadowToast.shownToastCount())

            grantPermission(activity)

            // Only a still-fresh pending request asks for a new fix; the cached path registers no
            // listener, so a preserved pending freshness is what the listener count witnesses.
            assertEquals(1, locationShadow.networkListeners().size)
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

    private fun seedRecentCache() {
        locationShadow.enableNetworkProvider()
        locationShadow.simulateLocation(LocationManager.NETWORK_PROVIDER, location(latitude = 1.0, longitude = 2.0))
    }

    private fun requestPermission(activity: SettingsActivity, buttonId: Int) {
        shadowOf(application).denyPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        activity.findViewById<Button>(buttonId).performClick()
        val request = shadowOf(activity).lastRequestedPermission
        assertEquals(REQUEST_LOCATION_PERMISSION, request.requestCode)
        assertArrayEquals(arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION), request.requestedPermissions)
    }

    private fun grantPermission(activity: SettingsActivity) {
        shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        activity.onRequestPermissionsResult(
            REQUEST_LOCATION_PERMISSION,
            arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION),
            intArrayOf(PackageManager.PERMISSION_GRANTED),
        )
    }

    private fun location(latitude: Double, longitude: Double): Location =
        Location(LocationManager.NETWORK_PROVIDER).apply {
            this.latitude = latitude
            this.longitude = longitude
            elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
        }

    private companion object {
        const val REQUEST_LOCATION_PERMISSION = 1
    }
}
