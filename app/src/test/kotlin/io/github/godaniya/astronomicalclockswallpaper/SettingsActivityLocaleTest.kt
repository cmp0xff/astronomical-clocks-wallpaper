package io.github.godaniya.astronomicalclockswallpaper

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.SharedPreferences
import android.os.Looper
import android.widget.Button
import android.widget.EditText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowAlertDialog
import org.robolectric.shadows.ShadowToast
import java.time.ZoneId
import java.util.Locale

/** Parses the keyboard's decimal format without accepting grouping or partially valid input. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36], qualifiers = "de-rDE")
class SettingsActivityLocaleTest {
    @Test
    fun germanCoordinatesArePersisted() {
        assertPersisted(latitude = "45,5", longitude = "-120,25", expectedLatitude = 45.5, expectedLongitude = -120.25)
    }

    @Test
    @Config(qualifiers = "ar-rEG")
    fun arabicDigitsArePersisted() {
        assertPersisted(
            latitude = "٤٥٫٥",
            longitude = "١٢٠٫٢٥",
            expectedLatitude = 45.5,
            expectedLongitude = 120.25,
            expectedLocale = Locale.forLanguageTag("ar-EG"),
        )
    }

    @Test
    fun positiveSignIsAccepted() {
        assertPersisted(latitude = "+45,5", longitude = "-120,25", expectedLatitude = 45.5, expectedLongitude = -120.25)
    }

    @Test
    fun repeatedDecimalIsRejected() {
        assertRejected(latitude = "45,5,1", longitude = "-120,25", bypassKeyboard = true)
    }

    @Test
    fun trailingTextIsRejected() {
        assertRejected(latitude = "45,5north", longitude = "-120,25", bypassKeyboard = true)
    }

    @Test
    fun dotCoordinatesArePersisted() {
        // '.' is the canonical coordinate separator and an alias for the German comma.
        assertPersisted(latitude = "8.5", longitude = "-120.25", expectedLatitude = 8.5, expectedLongitude = -120.25)
    }

    @Test
    @Config(qualifiers = "ar-rEG")
    fun arabicDotCoordinatesPersist() {
        assertPersisted(
            latitude = "45.5",
            longitude = "120.5",
            expectedLatitude = 45.5,
            expectedLongitude = 120.5,
            expectedLocale = Locale.forLanguageTag("ar-EG"),
        )
    }

    @Test
    fun invalidLongitudeIsRejected() {
        assertRejected(latitude = "45,5", longitude = "181,0")
    }

    @Test
    fun germanSeededRoundTrip() {
        assertSeededRoundTrip(
            initialLatitude = 50.1081234567,
            initialLongitude = 0.0001234,
        )
    }

    @Test
    fun commaInputKeepsCurrentSite() {
        val application = RuntimeEnvironment.getApplication()
        val store = LocationStore(application)
        store.save(
            ObservingLocation(
                latitude = 50.1081234567,
                longitude = 14.4206019876,
                source = ObservingLocation.Source.CURRENT_COARSE,
                zoneId = ZoneId.of("Europe/Prague"),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(activity, latitude = "50,1081234567", longitude = "14,4206019876")
            var writes = 0
            val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> writes++ }
            store.registerListener(listener)

            activity.findViewById<Button>(R.id.save_location).performClick()

            assertEquals(0, writes)
            assertEquals(
                ObservingLocation.Source.CURRENT_COARSE,
                requireNotNull(LocationStore(activity).load()).source,
            )
            assertEquals(activity.getString(R.string.location_unchanged), ShadowToast.getTextOfLatestToast())
            store.unregisterListener(listener)
        }
    }

    @Test
    @Config(qualifiers = "ar-rEG")
    fun arabicSeededRoundTrip() {
        assertSeededRoundTrip(
            initialLatitude = 30.0444123456,
            initialLongitude = 0.0001234,
        )
    }

    @SuppressLint("SetTextI18n")
    private fun assertSeededRoundTrip(initialLatitude: Double, initialLongitude: Double) {
        val application = RuntimeEnvironment.getApplication()
        LocationStore(application).save(
            ObservingLocation(
                latitude = initialLatitude,
                longitude = initialLongitude,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.systemDefault(),
            ),
        )
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            // Click save without changing fields - round trip must preserve exact Double values
            activity.findViewById<Button>(R.id.save_location).performClick()
            val loaded = LocationStore(activity).load()
            assertEquals(initialLatitude, loaded?.latitude ?: 0.0, 0.0)
            assertEquals(initialLongitude, loaded?.longitude ?: 0.0, 0.0)

            // Edit latitude, leave seeded longitude (< 1e-3) untouched, save again
            activity.findViewById<EditText>(R.id.latitude_input).setText("45.0")
            activity.findViewById<Button>(R.id.save_location).performClick()
            acceptEstimatedZone()
            val edited = LocationStore(activity).load()
            assertEquals(45.0, edited?.latitude ?: 0.0, 0.0)
            assertEquals(initialLongitude, edited?.longitude ?: 0.0, 0.0)
        }
    }

    private fun assertPersisted(
        latitude: String,
        longitude: String,
        expectedLatitude: Double,
        expectedLongitude: Double,
        expectedLocale: Locale = Locale.GERMANY,
    ) {
        val expectedZoneId = TimeZoneLookup.lookup(latitude = expectedLatitude, longitude = expectedLongitude)
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            enterCoordinates(
                activity = activity,
                latitude = latitude,
                longitude = longitude,
                expectedLocale = expectedLocale,
            )
            activity.findViewById<Button>(R.id.save_location).performClick()
            acceptEstimatedZone()
            assertEquals(
                ObservingLocation(
                    latitude = expectedLatitude,
                    longitude = expectedLongitude,
                    source = ObservingLocation.Source.MANUAL,
                    zoneId = expectedZoneId,
                ),
                LocationStore(activity).load(),
            )
        }
    }

    private fun assertRejected(latitude: String, longitude: String, bypassKeyboard: Boolean = false) {
        Robolectric.buildActivity(SettingsActivity::class.java).use { controller ->
            val activity = controller.setup().get()
            if (bypassKeyboard) {
                // Inject malformed restored/input text without the keyboard removing invalid characters first.
                activity.findViewById<EditText>(R.id.latitude_input).keyListener = null
            }
            enterCoordinates(activity, latitude, longitude)
            activity.findViewById<Button>(R.id.save_location).performClick()
            assertNull(LocationStore(activity).load())
            assertEquals(activity.getString(R.string.location_invalid), ShadowToast.getTextOfLatestToast())
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

    private fun enterCoordinates(
        activity: SettingsActivity,
        latitude: String,
        longitude: String,
        expectedLocale: Locale = Locale.GERMANY,
    ) {
        val latitudeInput = activity.findViewById<EditText>(R.id.latitude_input)
        val longitudeInput = activity.findViewById<EditText>(R.id.longitude_input)
        assertEquals(expectedLocale, latitudeInput.textLocale)
        assertEquals(expectedLocale, longitudeInput.textLocale)
        latitudeInput.setText(latitude)
        longitudeInput.setText(longitude)
        assertEquals(latitude, latitudeInput.text.toString())
        assertEquals(longitude, longitudeInput.text.toString())
    }
}
