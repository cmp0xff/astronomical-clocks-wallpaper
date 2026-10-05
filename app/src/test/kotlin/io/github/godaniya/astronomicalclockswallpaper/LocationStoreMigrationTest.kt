package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.content.SharedPreferences
import android.os.Looper
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog
import java.time.ZoneId

/** Exercises one-time device-zone capture for legacy coordinates and incomplete version-one records. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class LocationStoreMigrationTest {
    @Test
    fun legacyCapturesZoneOnce() {
        saveLegacy()
        var deviceZone = ZoneId.of("Europe/Prague")
        var zoneReads = 0
        val store =
            LocationStore(RuntimeEnvironment.getApplication()) {
                zoneReads++
                deviceZone
            }
        val expected =
            ObservingLocation(
                latitude = 50.0,
                longitude = 14.0,
                source = ObservingLocation.Source.MANUAL,
                zoneId = deviceZone,
            )
        // Purity check: load() reads legacy coordinates without mutating SharedPreferences.
        val beforeRead = preferences().all
        assertEquals(expected, store.load())
        assertEquals(beforeRead, preferences().all)

        // Explicit migration: migrateAndRepair() persists the versioned record once.
        store.migrateAndRepair()
        assertEquals(expected, store.load())
        assertEquals(setOf("location"), preferences().all.keys)
        deviceZone = ZoneId.of("Asia/Tokyo")
        val migrated = preferences().all
        store.migrateAndRepair()
        assertEquals(expected, store.load())
        assertEquals(expected, LocationStore(RuntimeEnvironment.getApplication()) { deviceZone }.load())
        assertEquals(migrated, preferences().all)
        assertEquals(2, zoneReads)
    }

    @Test
    fun legacyKeepsCoordinateBounds() {
        preferences()
            .edit()
            .putString("latitude", "-90.0")
            .putString("longitude", "180.0")
            .putString("source", "CURRENT_COARSE")
            .apply()
        val zone = ZoneId.of("UTC")
        val store = LocationStore(RuntimeEnvironment.getApplication()) { zone }
        val expected =
            ObservingLocation(
                latitude = -90.0,
                longitude = 180.0,
                source = ObservingLocation.Source.CURRENT_COARSE,
                zoneId = zone,
            )
        val beforeRead = preferences().all
        assertEquals(expected, store.load())
        assertEquals(beforeRead, preferences().all)
        store.migrateAndRepair()
        assertEquals(expected, store.load())
        assertEquals(setOf("location"), preferences().all.keys)
    }

    @Test
    fun malformedLegacyIsPreserved() {
        val store = LocationStore(RuntimeEnvironment.getApplication()) { error("must not read device zone") }
        val malformed =
            listOf(
                "latitude" to "not a number",
                "latitude" to "NaN",
                "latitude" to "91.0",
                "longitude" to "Infinity",
                "longitude" to "-181.0",
                "source" to "UNKNOWN",
            )
        for ((key, value) in malformed) {
            saveLegacy()
            preferences().edit().putString(key, value).apply()
            assertUntouched(store)
        }
        assertTrue(ShadowLog.getLogsForTag("LocationStore").all { it.msg.contains("malformed") })
    }

    @Test
    fun invalidLegacyFieldsPreserved() {
        val store = LocationStore(RuntimeEnvironment.getApplication()) { error("must not read device zone") }
        for (key in listOf("latitude", "longitude", "source")) {
            saveLegacy()
            preferences().edit().remove(key).apply()
            assertUntouched(store)
            saveLegacy()
            preferences().edit().putBoolean(key, true).apply()
            assertUntouched(store)
        }
    }

    @Test
    fun invalidZonesRepairedOnce() {
        // Exact diagnostics: an absent key is told apart from a present but unusable value, which is echoed back.
        val invalidZones =
            listOf<Pair<Any?, String>>(
                null to "repaired absent observing location timezone",
                "" to "repaired invalid observing location timezone ''",
                "No/Such_Zone" to "repaired invalid observing location timezone 'No/Such_Zone'",
                JSONObject.NULL to "repaired invalid observing location timezone 'null'",
                42 to "repaired invalid observing location timezone '42'",
                false to "repaired invalid observing location timezone 'false'",
            )
        for ((invalidZone, expectedDiagnosis) in invalidZones) {
            val expectedRepair = "$expectedDiagnosis; using Australia/Sydney"
            ShadowLog.clear()
            val record = JSONObject(VALID_RECORD).put("zoneId", invalidZone).put("retainedField", "keep")
            preferences().edit().putString("location", record.toString()).apply()
            var zoneReads = 0
            val store =
                LocationStore(RuntimeEnvironment.getApplication()) {
                    zoneReads++
                    ZoneId.of("Australia/Sydney")
                }
            val expected =
                ObservingLocation(
                    latitude = 50.0,
                    longitude = 14.0,
                    source = ObservingLocation.Source.MANUAL,
                    zoneId = ZoneId.of("Australia/Sydney"),
                )
            // Pure reads use the fallback without persisting it or logging a repair.
            val beforeRead = preferences().all
            assertEquals(expected, store.load())
            assertEquals(beforeRead, preferences().all)
            assertEquals(1, zoneReads)
            assertTrue(ShadowLog.getLogsForTag("LocationStore").isEmpty())

            // Explicit repair: migrateAndRepair() updates SharedPreferences once and logs the specific diagnosis.
            store.migrateAndRepair()
            assertEquals(expected, store.load())
            assertEquals(2, zoneReads)
            val repaired = JSONObject(requireNotNull(preferences().getString("location", null)))
            assertEquals("Australia/Sydney", repaired.getString("zoneId"))
            assertEquals("keep", repaired.getString("retainedField"))
            assertEquals(listOf(expectedRepair), ShadowLog.getLogsForTag("LocationStore").map { it.msg })

            // Repeated maintenance must not write again or consult the device zone.
            val afterRepair = preferences().all
            shadowOf(Looper.getMainLooper()).idle()
            var changes = 0
            val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> changes++ }
            preferences().registerOnSharedPreferenceChangeListener(listener)
            assertEquals(expected, store.load())
            store.migrateAndRepair()
            assertEquals(expected, store.load())
            assertEquals(2, zoneReads)
            assertEquals(afterRepair, preferences().all)
            shadowOf(Looper.getMainLooper()).idle()
            assertEquals(0, changes)
            assertEquals(listOf(expectedRepair), ShadowLog.getLogsForTag("LocationStore").map { it.msg })
            preferences().unregisterOnSharedPreferenceChangeListener(listener)
        }
    }

    @Test
    fun currentRecordOverridesLegacy() {
        saveLegacy()
        val record = JSONObject(VALID_RECORD).put("latitude", -30.0).put("zoneId", "Pacific/Auckland")
        preferences().edit().putString("location", record.toString()).apply()
        val before = preferences().all
        val store = LocationStore(RuntimeEnvironment.getApplication()) { error("must not read device zone") }
        store.migrateAndRepair()
        store.migrateAndRepair()
        assertTrue(ShadowLog.getLogsForTag("LocationStore").isEmpty())
        assertNotNull(store.load())
        assertEquals(-30.0, requireNotNull(store.load()).latitude, 0.0)
        assertEquals(ZoneId.of("Pacific/Auckland"), requireNotNull(store.load()).zoneId)
        assertEquals(before, preferences().all)
    }

    private fun saveLegacy() {
        preferences()
            .edit()
            .clear()
            .putString("latitude", "50.0")
            .putString("longitude", "14.0")
            .putString("source", "MANUAL")
            .apply()
    }

    private fun assertUntouched(store: LocationStore) {
        val before = preferences().all
        assertNull(store.load())
        assertEquals(before, preferences().all)
        store.migrateAndRepair()
        assertEquals(before, preferences().all)
    }

    private fun preferences(): SharedPreferences =
        RuntimeEnvironment.getApplication().getSharedPreferences("observing_location", Context.MODE_PRIVATE)

    private companion object {
        const val VALID_RECORD = """{"version":1,"latitude":50.0,"longitude":14.0,"source":"MANUAL"}"""
    }
}
