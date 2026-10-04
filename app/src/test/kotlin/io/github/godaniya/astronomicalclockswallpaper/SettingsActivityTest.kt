package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.annotation.SuppressLint
import android.app.WallpaperManager
import android.content.ComponentName
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Looper
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowToast
import java.time.ZoneId
import java.util.TimeZone

/** Exercises launcher and preview behavior, permission results, and manual/current location persistence. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class SettingsActivityTest {
    private val originalTimezone = TimeZone.getDefault()

    @After
    fun restoreTimezone() {
        TimeZone.setDefault(originalTimezone)
    }

    @Test
    fun previewTargetsWallpaper() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            assertTrue(activity.findViewById<Button>(R.id.open_preview).performClick())
            val intent = shadowOf(activity).nextStartedActivity
            assertEquals(WallpaperManager.ACTION_CHANGE_LIVE_WALLPAPER, intent.action)
            // The typed overload requires API 33; this test also runs on API 26.
            @Suppress("DEPRECATION")
            val component = intent.getParcelableExtra<ComponentName>(WallpaperManager.EXTRA_LIVE_WALLPAPER_COMPONENT)
            assertEquals(
                ComponentName(activity, AstronomicalClocksWallpaperService::class.java),
                component,
            )
            controller.pause().stop().destroy()
        }
    }

    @Test
    @SuppressLint("SetTextI18n")
    fun manualCoordsSurviveRecreate() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<EditText>(R.id.latitude_input).setText("45.5")
            activity.findViewById<EditText>(R.id.longitude_input).setText("-120.25")
            activity.findViewById<Button>(R.id.save_location).performClick()
            val expected = "45.5000, -120.2500 (manual)\nTimezone: ${ZoneId.systemDefault().id}"
            assertEquals(expected, activity.findViewById<TextView>(R.id.location_current).text.toString())
            controller.recreate()
            assertEquals(
                expected,
                controller
                    .get()
                    .findViewById<TextView>(R.id.location_current)
                    .text
                    .toString(),
            )
        }
    }

    @Test
    @SuppressLint("SetTextI18n")
    fun refreshPreservesOnFailure() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<EditText>(R.id.latitude_input).setText("10.0")
            activity.findViewById<EditText>(R.id.longitude_input).setText("20.0")
            activity.findViewById<Button>(R.id.save_location).performClick()
            val expected = "10.0000, 20.0000 (manual)\nTimezone: ${ZoneId.systemDefault().id}"

            shadowOf(activity.application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
            val locationManager = activity.getSystemService(Context.LOCATION_SERVICE) as LocationManager
            shadowOf(locationManager).setProviderEnabled(LocationManager.NETWORK_PROVIDER, false)
            activity.findViewById<Button>(R.id.refresh_location).performClick()

            assertEquals(expected, activity.findViewById<TextView>(R.id.location_current).text.toString())
            assertEquals(
                ObservingLocation(
                    latitude = 10.0,
                    longitude = 20.0,
                    source = ObservingLocation.Source.MANUAL,
                    zoneId = ZoneId.systemDefault(),
                ),
                LocationStore(activity).load(),
            )
        }
    }

    @Test
    fun useCurrentLocationSavesCoarse() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            shadowOf(activity.application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
            val locationManager = activity.getSystemService(Context.LOCATION_SERVICE) as LocationManager
            shadowOf(locationManager).enableNetworkProvider()
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            shadowOf(locationManager).simulateLocation(
                LocationManager.NETWORK_PROVIDER,
                location(latitude = 37.42, longitude = -122.08),
            )
            shadowOf(Looper.getMainLooper()).idle()
            assertEquals(
                "37.4200, -122.0800 (current)\nTimezone: ${ZoneId.systemDefault().id}",
                activity.findViewById<TextView>(R.id.location_current).text.toString(),
            )
            assertEquals(
                ObservingLocation(
                    latitude = 37.42,
                    longitude = -122.08,
                    source = ObservingLocation.Source.CURRENT_COARSE,
                    zoneId = ZoneId.systemDefault(),
                ),
                LocationStore(activity).load(),
            )
        }
    }

    @Test
    fun coordinateDisplaySignOfZero() {
        val application = RuntimeEnvironment.getApplication()
        LocationStore(application).save(
            ObservingLocation(
                latitude = -0.00004,
                longitude = 0.00004,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.systemDefault(),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            // The negative value rounds to zero; formatting it without rounding first would keep
            // the sign and render "-0.0000".
            assertEquals(
                "0.0000, 0.0000 (manual)\nTimezone: ${ZoneId.systemDefault().id}",
                activity.findViewById<TextView>(R.id.location_current).text.toString(),
            )
        }
    }

    @Test
    fun denyCallbackShowsToast() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.onRequestPermissionsResult(
                REQUEST_LOCATION_PERMISSION,
                arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION),
                intArrayOf(PackageManager.PERMISSION_DENIED),
            )
            assertEquals(activity.getString(R.string.location_permission_denied), ShadowToast.getTextOfLatestToast())
            assertNull(LocationStore(activity).load())
        }
    }

    @Test
    @SuppressLint("SetTextI18n")
    fun invalidManualInputRejected() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<EditText>(R.id.latitude_input).setText("91")
            activity.findViewById<EditText>(R.id.longitude_input).setText("20")
            activity.findViewById<Button>(R.id.save_location).performClick()
            assertEquals(activity.getString(R.string.location_invalid), ShadowToast.getTextOfLatestToast())
            assertNull(LocationStore(activity).load())
        }
    }

    @Test
    @SuppressLint("SetTextI18n")
    fun layerTogglesNeedLocation() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            val zodiac = activity.findViewById<CheckBox>(R.id.zodiac_ring)
            val sun = activity.findViewById<CheckBox>(R.id.sun_layer)
            assertFalse(zodiac.isEnabled)
            assertFalse(sun.isEnabled)

            activity.findViewById<EditText>(R.id.latitude_input).setText("50.0")
            activity.findViewById<EditText>(R.id.longitude_input).setText("14.4")
            activity.findViewById<Button>(R.id.save_location).performClick()

            assertTrue(zodiac.isEnabled)
            assertTrue(sun.isEnabled)
        }
    }

    @Test
    fun openSettingsSeedsStoredCoords() {
        val application = RuntimeEnvironment.getApplication()
        val highPrecisionLat = 50.0874981234
        val highPrecisionLng = 14.4206019876
        LocationStore(application).save(
            ObservingLocation(
                latitude = highPrecisionLat,
                longitude = highPrecisionLng,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.systemDefault(),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            assertEquals(
                "50.0874981234",
                activity.findViewById<EditText>(R.id.latitude_input).text.toString(),
            )
            assertEquals(
                "14.4206019876",
                activity.findViewById<EditText>(R.id.longitude_input).text.toString(),
            )
            assertEquals(
                "50.0875, 14.4206 (manual)\nTimezone: ${ZoneId.systemDefault().id}",
                activity.findViewById<TextView>(R.id.location_current).text.toString(),
            )
        }
    }

    @Test
    fun seededNegativeZeroNormalizes() {
        val application = RuntimeEnvironment.getApplication()
        LocationStore(application).save(
            ObservingLocation(
                latitude = -0.0,
                longitude = 14.4206,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.systemDefault(),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            assertEquals("0.0", activity.findViewById<EditText>(R.id.latitude_input).text.toString())

            activity.findViewById<Button>(R.id.save_location).performClick()

            val saved = requireNotNull(LocationStore(activity).load())
            assertEquals(0.0, saved.latitude, 0.0)
            assertEquals(14.4206, saved.longitude, 0.0)
        }
    }

    @Test
    fun unchangedSaveDoesNotWrite() {
        val application = RuntimeEnvironment.getApplication()
        val store = LocationStore(application)
        store.save(
            ObservingLocation(
                latitude = 50.0875,
                longitude = 14.4206,
                source = ObservingLocation.Source.CURRENT_COARSE,
                zoneId = ZoneId.of("Europe/Prague"),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            var writes = 0
            val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> writes++ }
            store.registerListener(listener)

            activity.findViewById<Button>(R.id.save_location).performClick()

            assertEquals(0, writes)
            assertEquals(activity.getString(R.string.location_unchanged), ShadowToast.getTextOfLatestToast())
            store.unregisterListener(listener)
        }
    }

    @Test
    fun unchangedSaveSkipsRepeatRepair() {
        val application = RuntimeEnvironment.getApplication()
        val prefs = application.getSharedPreferences("observing_location", Context.MODE_PRIVATE)
        val record =
            JSONObject()
                .put("version", 1)
                .put("latitude", 50.0875)
                .put("longitude", 14.4206)
                .put("source", "CURRENT_COARSE")
                .put("zoneId", "Invalid/Zone_Name")
        prefs.edit().putString("location", record.toString()).apply()

        val store = LocationStore(application)
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            var writes = 0
            val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> writes++ }
            store.registerListener(listener)

            activity.findViewById<Button>(R.id.save_location).performClick()

            assertEquals(0, writes)
            store.unregisterListener(listener)
            val raw = prefs.getString("location", null)
            val json = JSONObject(requireNotNull(raw))
            assertEquals(ZoneId.systemDefault().id, json.getString("zoneId"))
            assertEquals("CURRENT_COARSE", json.getString("source"))
        }
    }

    @Test
    fun negativeZeroSkipsEditedBranch() {
        val application = RuntimeEnvironment.getApplication()
        LocationStore(application).save(
            ObservingLocation(
                latitude = -0.0,
                longitude = 14.4206,
                source = ObservingLocation.Source.CURRENT_COARSE,
                zoneId = ZoneId.of("Europe/Prague"),
            ),
        )
        TimeZone.setDefault(TimeZone.getTimeZone("Pacific/Auckland"))
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            assertEquals("0.0", activity.findViewById<EditText>(R.id.latitude_input).text.toString())

            activity.findViewById<Button>(R.id.save_location).performClick()

            val saved = requireNotNull(LocationStore(activity).load())
            assertEquals(ObservingLocation.Source.CURRENT_COARSE, saved.source)
            assertEquals(ZoneId.of("Europe/Prague"), saved.zoneId)
            assertTrue(activity.findViewById<TextView>(R.id.location_current).text.contains("(current)"))
            assertTrue(activity.findViewById<TextView>(R.id.location_current).text.contains("Europe/Prague"))
        }
    }

    @Test
    @SuppressLint("SetTextI18n")
    fun editingOnePreservesUntouched() {
        val application = RuntimeEnvironment.getApplication()
        val originalLat = 50.0874981234
        val originalLng = 14.4206019876
        LocationStore(application).save(
            ObservingLocation(
                latitude = originalLat,
                longitude = originalLng,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.systemDefault(),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            activity.findViewById<EditText>(R.id.latitude_input).setText("51.5074")
            activity.findViewById<Button>(R.id.save_location).performClick()

            val updated = LocationStore(activity).load()
            assertEquals(51.5074, updated?.latitude ?: 0.0, 0.0)
            assertEquals(originalLng, updated?.longitude ?: 0.0, 0.0)
        }
    }

    @Test
    fun locationFixSeedsInputs() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            shadowOf(activity.application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
            val locationManager = activity.getSystemService(Context.LOCATION_SERVICE) as LocationManager
            shadowOf(locationManager).enableNetworkProvider()
            val fixLat = 37.4220012345
            val fixLng = -122.0840054321
            activity.findViewById<Button>(R.id.use_current_location).performClick()
            shadowOf(locationManager).simulateLocation(
                LocationManager.NETWORK_PROVIDER,
                location(latitude = fixLat, longitude = fixLng),
            )
            shadowOf(Looper.getMainLooper()).idle()

            assertEquals(
                "37.4220012345",
                activity.findViewById<EditText>(R.id.latitude_input).text.toString(),
            )
            assertEquals(
                "-122.0840054321",
                activity.findViewById<EditText>(R.id.longitude_input).text.toString(),
            )
            val stored = LocationStore(activity).load()
            assertEquals(fixLat, stored?.latitude ?: 0.0, 0.0)
            assertEquals(fixLng, stored?.longitude ?: 0.0, 0.0)
        }
    }

    @Test
    fun emptyStoreLeavesFieldsEmpty() {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            assertTrue(activity.findViewById<EditText>(R.id.latitude_input).text.isEmpty())
            assertTrue(activity.findViewById<EditText>(R.id.longitude_input).text.isEmpty())
            assertEquals(
                activity.getString(R.string.location_unset),
                activity.findViewById<TextView>(R.id.location_current).text.toString(),
            )
        }
    }

    @Test
    @SuppressLint("SetTextI18n")
    fun recreateKeepsUnsavedEdits() {
        val application = RuntimeEnvironment.getApplication()
        LocationStore(application).save(
            ObservingLocation(
                latitude = 10.0,
                longitude = 20.0,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.systemDefault(),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            assertEquals("10.0", activity.findViewById<EditText>(R.id.latitude_input).text.toString())
            assertEquals("20.0", activity.findViewById<EditText>(R.id.longitude_input).text.toString())
            activity.findViewById<EditText>(R.id.latitude_input).setText("99.9")
            controller.recreate()
            assertEquals(
                "99.9",
                controller
                    .get()
                    .findViewById<EditText>(R.id.latitude_input)
                    .text
                    .toString(),
            )
        }
    }

    private fun location(latitude: Double, longitude: Double): Location {
        val location = Location(LocationManager.NETWORK_PROVIDER)
        location.setLatitude(latitude)
        location.setLongitude(longitude)
        return location
    }

    private companion object {
        const val REQUEST_LOCATION_PERMISSION = 1
    }
}
