package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.app.AlertDialog
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
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
import java.time.ZoneId

/** Exercises user-initiated recovery without permission-request loops or automatic location refresh. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class SettingsActivityPermissionRecoveryTest {
    private val application = RuntimeEnvironment.getApplication()

    @Test
    fun blockedDenialSurvivesRelaunch() {
        val stored =
            ObservingLocation(
                latitude = 50.0,
                longitude = 14.0,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.of("Europe/Prague"),
            )
        LocationStore(application).save(stored)
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            denyLocation(activity)
            assertEquals(activity.getString(R.string.location_permission_blocked), ShadowToast.getTextOfLatestToast())
            assertNull(ShadowAlertDialog.getLatestDialog())
        }
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            assertNull(shadowOf(activity).lastRequestedPermission)
            val recovery = dialog()
            assertEquals(activity.getString(R.string.location_permission_recovery), shadowOf(recovery).message)
            clickDialog(AlertDialog.BUTTON_NEGATIVE)
            assertFalse(recovery.isShowing)
            assertNull(shadowOf(activity).nextStartedActivity)
            assertTrue(activity.findViewById<EditText>(R.id.latitude_input).isEnabled)
            assertTrue(activity.findViewById<Button>(R.id.save_location).isEnabled)
            assertEquals(stored, LocationStore(application).load())
        }
    }

    @Test
    fun rationaleCancelAndContinue() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            denyLocation(activity)
            val originalRequest = shadowOf(activity).lastRequestedPermission
            shadowOf(activity.packageManager).setShouldShowRequestPermissionRationale(
                Manifest.permission.ACCESS_COARSE_LOCATION,
                true,
            )
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            assertEquals(activity.getString(R.string.location_permission_rationale), shadowOf(dialog()).message)
            clickDialog(AlertDialog.BUTTON_NEGATIVE)
            assertSame(originalRequest, shadowOf(activity).lastRequestedPermission)
            assertNull(LocationStore(application).load())
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            clickDialog(AlertDialog.BUTTON_POSITIVE)
            assertNotSame(originalRequest, shadowOf(activity).lastRequestedPermission)
        }
    }

    @Test
    fun settingsReturnNeedsRefresh() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val manager = application.getSystemService(Context.LOCATION_SERVICE) as LocationManager
            val locationShadow = shadowOf(manager)
            locationShadow.enableNetworkProvider()
            val cached = Location(LocationManager.NETWORK_PROVIDER)
            cached.latitude = 1.0
            cached.longitude = 2.0
            cached.elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
            locationShadow.simulateLocation(LocationManager.NETWORK_PROVIDER, cached)
            denyLocation(activity)
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            assertNull(shadowOf(activity).nextStartedActivity)
            clickDialog(AlertDialog.BUTTON_POSITIVE)
            val intent = shadowOf(activity).nextStartedActivity
            assertEquals(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, intent.action)
            assertEquals("package:${activity.packageName}", intent.dataString)

            controller
                .pause()
                .stop()
                .start()
                .resume()
                .visible()
            assertTrue(locationShadow.networkListeners().isEmpty())
            assertNull(LocationStore(application).load())
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            clickDialog(AlertDialog.BUTTON_POSITIVE)
            shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
            controller
                .pause()
                .stop()
                .start()
                .resume()
                .visible()
            assertTrue(locationShadow.networkListeners().isEmpty())
            activity.findViewById<Button>(R.id.refresh_location).performClick()
            assertEquals(1, locationShadow.networkListeners().size)

            // A later revocation must not inherit the old blocked-denial observation.
            shadowOf(application).denyPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
            val previousRequest = shadowOf(activity).lastRequestedPermission
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            assertNotSame(previousRequest, shadowOf(activity).lastRequestedPermission)
        }
    }

    @Test
    fun explicitRetryAfterReset() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            denyLocation(activity)
            val originalRequest = shadowOf(activity).lastRequestedPermission
            controller.recreate()
            val recreated = controller.get()
            recreated.findViewById<Button>(R.id.use_current_location).performClick()
            assertNull(shadowOf(recreated).lastRequestedPermission)
            clickDialog(AlertDialog.BUTTON_NEUTRAL)
            assertFalse(dialog().isShowing)
            assertNotSame(originalRequest, shadowOf(recreated).lastRequestedPermission)
            assertEquals(
                listOf(Manifest.permission.ACCESS_COARSE_LOCATION),
                shadowOf(recreated).lastRequestedPermission.requestedPermissions.toList(),
            )
        }
    }

    @Test
    fun missingAppSettingsIsReported() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            denyLocation(activity)
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            shadowOf(application).checkActivities(true)
            clickDialog(AlertDialog.BUTTON_POSITIVE)
            assertEquals(
                activity.getString(R.string.location_permission_settings_unavailable),
                ShadowToast.getTextOfLatestToast(),
            )
            assertTrue(
                ShadowLog.getLogsForTag("LocationPermissionControls").any {
                    it.msg == "app permission settings unavailable" && it.throwable != null
                },
            )
            assertTrue(activity.findViewById<Button>(R.id.save_location).isEnabled)
        }
    }

    @Test
    fun malformedObservationRepaired() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val preferences = permissionPreferences()
            preferences.edit().putString(LocationPermissionControls.KEY_NON_PROMPTABLE_DENIAL, "malformed").apply()

            val locationAction = activity.findViewById<Button>(R.id.use_current_location)
            locationAction.performClick()
            assertFalse(preferences.contains(LocationPermissionControls.KEY_NON_PROMPTABLE_DENIAL))
            locationAction.performClick()

            // The malformed value is repaired on the first tap, so the warning is emitted once
            // rather than on every subsequent tap.
            assertEquals(
                1,
                ShadowLog.getLogsForTag("LocationPermissionControls").count {
                    it.msg == "ignoring malformed permission denial observation"
                },
            )
            assertFalse(preferences.contains(LocationPermissionControls.KEY_NON_PROMPTABLE_DENIAL))
        }
    }

    @Test
    fun promptableDenialLeavesNoKey() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            shadowOf(application).denyPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            shadowOf(activity.packageManager).setShouldShowRequestPermissionRationale(
                Manifest.permission.ACCESS_COARSE_LOCATION,
                true,
            )

            activity.onRequestPermissionsResult(
                REQUEST_LOCATION_PERMISSION,
                arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION),
                intArrayOf(PackageManager.PERMISSION_DENIED),
            )

            assertEquals(activity.getString(R.string.location_permission_denied), ShadowToast.getTextOfLatestToast())
            assertFalse(permissionPreferences().contains(LocationPermissionControls.KEY_NON_PROMPTABLE_DENIAL))
        }
    }

    @Test
    fun destroyDismissesOpenDialog() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            denyLocation(activity)
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            val recovery = dialog()
            assertTrue(recovery.isShowing)

            controller.pause().stop().destroy()

            assertFalse(recovery.isShowing)
        }
    }

    private fun permissionPreferences() = application.getSharedPreferences("location_permission", Context.MODE_PRIVATE)

    private fun denyLocation(activity: SettingsActivity) {
        shadowOf(application).denyPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        activity.findViewById<Button>(R.id.use_current_location).performClick()
        assertEquals(
            listOf(Manifest.permission.ACCESS_COARSE_LOCATION),
            shadowOf(activity).lastRequestedPermission.requestedPermissions.toList(),
        )
        // requestPermissions() reaches the framework through startActivityForResult(), and Robolectric
        // keeps started activities on instrumentation shared across the whole test. Consume that
        // request intent here so a caller's nextStartedActivity() assertion sees only the activity it
        // intentionally starts.
        assertNotNull(shadowOf(activity).nextStartedActivity)
        activity.onRequestPermissionsResult(
            REQUEST_LOCATION_PERMISSION,
            arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION),
            intArrayOf(PackageManager.PERMISSION_DENIED),
        )
    }

    private fun dialog(): AlertDialog = ShadowAlertDialog.getLatestDialog() as AlertDialog

    private fun clickDialog(button: Int) {
        dialog().getButton(button).performClick()
        shadowOf(Looper.getMainLooper()).idle()
    }

    private companion object {
        const val REQUEST_LOCATION_PERMISSION = 1
    }
}
