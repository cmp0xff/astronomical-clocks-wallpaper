package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog

/** Ensures malformed and future records are diagnosed without repair, deletion, or legacy fallback. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class LocationStoreCorruptionTest {
    @Test
    fun malformedJsonIsPreserved() {
        val records = listOf("{", "", "null", "[]", "$VALID_RECORD trailing")
        for (record in records) {
            preferences().edit().putString("location", record).apply()
            assertUntouched()
        }
        assertTrue(ShadowLog.getLogsForTag("LocationStore").all { it.msg.contains("malformed") })
    }

    @Test
    fun wrongStoredTypeIsPreserved() {
        preferences().edit().putBoolean("location", true).apply()
        assertUntouched()
    }

    @Test
    fun malformedVersionIsPreserved() {
        for (version in listOf(null, JSONObject.NULL, "1", 1.5, true)) {
            val record = JSONObject(VALID_RECORD).put("version", version)
            preferences().edit().putString("location", record.toString()).apply()
            assertUntouched()
        }
        assertTrue(ShadowLog.getLogsForTag("LocationStore").all { it.msg.contains("malformed") })
    }

    @Test
    fun futureVersionsArePreserved() {
        preferences()
            .edit()
            .putString("latitude", "20.0")
            .putString("longitude", "30.0")
            .putString("source", "MANUAL")
            .apply()
        for (version in listOf(0, 2, -1)) {
            val record = JSONObject(VALID_RECORD).put("version", version)
            preferences().edit().putString("location", record.toString()).apply()
            assertUntouched()
        }
        assertTrue(ShadowLog.getLogsForTag("LocationStore").all { it.msg.contains("unsupported") })
    }

    @Test
    fun malformedFieldsArePreserved() {
        val invalidFields =
            listOf(
                "latitude" to "50.0",
                "latitude" to 91,
                "latitude" to null,
                "longitude" to "14.0",
                "longitude" to -181,
                "longitude" to false,
                "source" to "UNKNOWN",
                "source" to 42,
                "source" to null,
            )
        for ((key, value) in invalidFields) {
            for (zone in listOf(null, "Europe/Prague")) {
                val record = JSONObject(VALID_RECORD).put(key, value).put("zoneId", zone)
                preferences().edit().putString("location", record.toString()).apply()
                assertUntouched()
            }
        }
        assertTrue(ShadowLog.getLogsForTag("LocationStore").all { it.msg.contains("malformed") })
    }

    @Test
    fun unusableRecordWinsOverLegacy() {
        // A malformed string record and a wrong-typed record, each seeded alongside valid legacy keys.
        for (record in listOf<Any>("{", true)) {
            val editor =
                preferences()
                    .edit()
                    .putString("latitude", "20.0")
                    .putString("longitude", "30.0")
                    .putString("source", "MANUAL")
            when (record) {
                is String -> editor.putString("location", record)
                is Boolean -> editor.putBoolean("location", record)
            }
            editor.apply()
            val before = preferences().all
            val store = LocationStore(RuntimeEnvironment.getApplication()) { error("must not read device zone") }
            // The present record wins even when unusable, and legacy keys are never consumed.
            assertNull(store.load())
            store.migrateAndRepair()
            assertEquals(before, preferences().all)
            assertEquals(setOf("location", "latitude", "longitude", "source"), preferences().all.keys)
        }
    }

    @Test
    fun nonFiniteValuesArePreserved() {
        for (value in listOf("1e999", "-1e999", "NaN", "Infinity")) {
            val record = """{"version":1,"latitude":$value,"longitude":14.0,"source":"MANUAL"}"""
            preferences().edit().putString("location", record).apply()
            assertUntouched()
        }
    }

    private fun assertUntouched() {
        val before = preferences().all
        val store = LocationStore(RuntimeEnvironment.getApplication()) { error("must not read device zone") }
        assertNull(store.load())
        assertEquals(before, preferences().all)
        val readLog = ShadowLog.getLogsForTag("LocationStore").last()
        val logCount = ShadowLog.getLogsForTag("LocationStore").size
        store.migrateAndRepair()
        assertEquals(before, preferences().all)
        val logs = ShadowLog.getLogsForTag("LocationStore")
        assertEquals(logCount + 1, logs.size)
        assertEquals(readLog.msg, logs.last().msg)
        assertEquals(readLog.type, logs.last().type)
    }

    private fun preferences(): SharedPreferences =
        RuntimeEnvironment.getApplication().getSharedPreferences("observing_location", Context.MODE_PRIVATE)

    private companion object {
        const val VALID_RECORD = """{"version":1,"latitude":50.0,"longitude":14.0,"source":"MANUAL"}"""
    }
}
