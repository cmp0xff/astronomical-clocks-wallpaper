package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.time.ZoneId

/** Verifies that AstronomicalClocksApplication migrates and repairs LocationStore on startup. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class AstronomicalClocksApplicationTest {
    @Test
    fun onCreateMigratesLegacy() {
        val app = RuntimeEnvironment.getApplication() as AstronomicalClocksApplication
        val prefs = app.getSharedPreferences("observing_location", Context.MODE_PRIVATE)
        prefs
            .edit()
            .putString("latitude", "50.0")
            .putString("longitude", "14.0")
            .putString("source", "MANUAL")
            .apply()

        val expectedZone = ZoneId.systemDefault()
        app.onCreate()

        assertEquals(setOf("location"), prefs.all.keys)
        val loaded = LocationStore(app).load()
        assertEquals(50.0, loaded?.latitude ?: 0.0, 0.0)
        assertEquals(14.0, loaded?.longitude ?: 0.0, 0.0)
        assertEquals(ObservingLocation.Source.MANUAL, loaded?.source)
        assertEquals(expectedZone, loaded?.zoneId)
        val migrated = JSONObject(requireNotNull(prefs.getString("location", null)))
        assertEquals(expectedZone.id, migrated.getString("zoneId"))
    }

    @Test
    fun onCreateRepairsInvalidZone() {
        val app = RuntimeEnvironment.getApplication() as AstronomicalClocksApplication
        val prefs = app.getSharedPreferences("observing_location", Context.MODE_PRIVATE)
        val record =
            JSONObject()
                .put("version", 1)
                .put("latitude", 50.0)
                .put("longitude", 14.0)
                .put("source", "CURRENT_COARSE")
                .put("zoneId", "Bad/Zone")
                .put("extraField", 42)
                .toString()
        prefs
            .edit()
            .putString("location", record)
            .apply()

        val expectedZone = ZoneId.systemDefault()
        app.onCreate()

        val repairedJson = JSONObject(requireNotNull(prefs.getString("location", null)))
        assertEquals(1, repairedJson.getInt("version"))
        assertEquals(42, repairedJson.getInt("extraField"))
        assertEquals(expectedZone.id, repairedJson.getString("zoneId"))
        val loaded = LocationStore(app).load()
        assertEquals(50.0, loaded?.latitude ?: 0.0, 0.0)
        assertEquals(14.0, loaded?.longitude ?: 0.0, 0.0)
        assertEquals(ObservingLocation.Source.CURRENT_COARSE, loaded?.source)
        assertEquals(expectedZone, loaded?.zoneId)
    }
}
