package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.content.SharedPreferences
import android.os.Looper
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.ZoneId

/** Checks complete location records, explicit timezone preservation, and preference notifications. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class LocationStoreTest {
    @Test
    fun explicitZonesRoundTrip() {
        val store = LocationStore(RuntimeEnvironment.getApplication()) { error("must not read device zone") }
        for (zoneName in listOf("Europe/Prague", "Australia/Sydney", "+05:45", "UTC")) {
            val location = observingLocation(ZoneId.of(zoneName))
            store.save(location)
            val before = preferences().all
            store.migrateAndRepair()
            store.migrateAndRepair()
            assertEquals(location, LocationStore(RuntimeEnvironment.getApplication()).load())
            assertEquals(before, preferences().all)
        }
    }

    @Test
    fun emptyDoesNotCaptureZone() {
        val store = LocationStore(RuntimeEnvironment.getApplication()) { error("must not read device zone") }
        assertNull(store.load())
        store.migrateAndRepair()
        assertTrue(preferences().all.isEmpty())
    }

    @Test
    fun loadPureDoesNotPersist() {
        val record =
            JSONObject()
                .put("version", 1)
                .put("latitude", 50.0)
                .put("longitude", 14.0)
                .put("source", "CURRENT_COARSE")
                .put("zoneId", "Invalid/Zone")
                .toString()
        preferences().edit().putString("location", record).apply()

        var zoneReads = 0
        val store =
            LocationStore(RuntimeEnvironment.getApplication()) {
                zoneReads++
                ZoneId.of("Europe/Prague")
            }

        val beforeRead = preferences().all
        val loaded = store.load()
        assertEquals(beforeRead, preferences().all)
        assertEquals(ZoneId.of("Europe/Prague"), loaded?.zoneId)
        assertEquals(1, zoneReads)

        val raw = preferences().getString("location", null)
        val json = JSONObject(requireNotNull(raw))
        assertEquals("Invalid/Zone", json.getString("zoneId"))
    }

    @Test
    fun loadLegacyDoesNotPersist() {
        preferences()
            .edit()
            .putString("latitude", "50.0")
            .putString("longitude", "14.0")
            .putString("source", "MANUAL")
            .apply()

        var zoneReads = 0
        val store =
            LocationStore(RuntimeEnvironment.getApplication()) {
                zoneReads++
                ZoneId.of("Europe/Prague")
            }

        val beforeRead = preferences().all
        val loaded = store.load()
        assertEquals(beforeRead, preferences().all)
        assertEquals(ZoneId.of("Europe/Prague"), loaded?.zoneId)
        assertEquals(50.0, loaded?.latitude ?: 0.0, 0.0)
        assertEquals(1, zoneReads)

        assertEquals(setOf("latitude", "longitude", "source"), preferences().all.keys)
    }

    @Test
    fun savesOneVersionedRecord() {
        preferences()
            .edit()
            .putString("latitude", "10.0")
            .putString("longitude", "20.0")
            .putString("source", "MANUAL")
            .apply()
        val location = observingLocation(ZoneId.of("America/New_York"))
        LocationStore(RuntimeEnvironment.getApplication()).save(location)
        assertEquals(setOf("location"), preferences().all.keys)
        val record = JSONObject(requireNotNull(preferences().getString("location", null)))
        assertEquals(1, record.getInt("version"))
        assertEquals(location.latitude, record.getDouble("latitude"), 0.0)
        assertEquals(location.longitude, record.getDouble("longitude"), 0.0)
        assertEquals(location.source.name, record.getString("source"))
        assertEquals(location.zoneId.id, record.getString("zoneId"))
    }

    @Test
    fun saveReplacesMalformedRecord() {
        preferences()
            .edit()
            .putBoolean("location", true)
            .putBoolean("unrelated", true)
            .apply()
        val store = LocationStore(RuntimeEnvironment.getApplication())
        val location = observingLocation(ZoneId.of("Europe/Prague"))
        assertNull(store.load())
        store.save(location)
        assertEquals(location, store.load())
        assertTrue(preferences().getBoolean("unrelated", false))
    }

    @Test
    fun listenersSeeCompleteRecords() {
        val observerStore = LocationStore(RuntimeEnvironment.getApplication())
        val savingStore = LocationStore(RuntimeEnvironment.getApplication())
        val observed = mutableListOf<ObservingLocation?>()
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> observed.add(observerStore.load()) }
        observerStore.registerListener(listener)
        val first = observingLocation(ZoneId.of("Europe/Prague"))
        savingStore.save(first)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(listOf(first), observed)
        observerStore.unregisterListener(listener)
        savingStore.save(first.copy(zoneId = ZoneId.of("Asia/Tokyo")))
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(listOf(first), observed)
    }

    @Test
    fun migrationNotifiesAtomically() {
        preferences()
            .edit()
            .putString("latitude", "10.0")
            .putString("longitude", "20.0")
            .putString("source", "MANUAL")
            .apply()
        val snapshots = mutableListOf<Map<String, *>>()
        val store = LocationStore(RuntimeEnvironment.getApplication())
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { prefs, _ -> snapshots.add(prefs.all) }
        store.registerListener(listener)
        store.save(observingLocation(ZoneId.of("Europe/Prague")))
        shadowOf(Looper.getMainLooper()).idle()
        assertFalse(snapshots.isEmpty())
        assertTrue(snapshots.all { it.keys == setOf("location") })
        store.unregisterListener(listener)
    }

    private fun preferences(): SharedPreferences =
        RuntimeEnvironment.getApplication().getSharedPreferences("observing_location", Context.MODE_PRIVATE)

    private fun observingLocation(zoneId: ZoneId): ObservingLocation = SAVED_SITE.copy(zoneId = zoneId)

    private companion object {
        val SAVED_SITE =
            ObservingLocation(
                latitude = 12.5,
                longitude = -77.0,
                source = ObservingLocation.Source.CURRENT_COARSE,
                zoneId = ZoneId.of("UTC"),
            )
    }
}
