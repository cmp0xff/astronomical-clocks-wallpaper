package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener
import java.time.DateTimeException
import java.time.ZoneId

/** Persists a versioned location record, migrating older coordinates without changing their meaning. */
internal class LocationStore(context: Context, private val deviceZone: () -> ZoneId = ZoneId::systemDefault) {
    private val preferences: SharedPreferences =
        context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    fun load(): ObservingLocation? {
        // One snapshot also avoids getString's ClassCastException for wrongly typed stored data.
        val stored = preferences.all
        return if (KEY_RECORD in stored) loadRecord(stored[KEY_RECORD]) else loadLegacy(stored)
    }

    private fun loadRecord(raw: Any?): ObservingLocation? {
        val record = parseRecord(raw)
        return when {
            record == null || record.opt(KEY_VERSION) !is Int -> {
                Log.w(TAG, "ignoring malformed observing location record")
                null
            }

            record.opt(KEY_VERSION) != RECORD_VERSION -> {
                Log.w(TAG, "ignoring unsupported observing location version")
                null
            }

            else -> {
                readLocation(
                    rawLatitude = (record.opt(KEY_LATITUDE) as? Number)?.toDouble(),
                    rawLongitude = (record.opt(KEY_LONGITUDE) as? Number)?.toDouble(),
                    sourceText = record.opt(KEY_SOURCE) as? String,
                ) { resolveZone(record) }
            }
        }
    }

    private fun resolveZone(record: JSONObject): ZoneId = storedZoneOrNull(record) ?: deviceZone()

    private fun loadLegacy(stored: Map<String, *>): ObservingLocation? {
        if (LEGACY_KEYS.none(stored::containsKey)) return null
        return readLocation(
            rawLatitude = (stored[KEY_LATITUDE] as? String)?.toDoubleOrNull(),
            rawLongitude = (stored[KEY_LONGITUDE] as? String)?.toDoubleOrNull(),
            sourceText = stored[KEY_SOURCE] as? String,
            zone = deviceZone,
        )
    }

    fun migrateAndRepair() {
        val stored = preferences.all
        if (KEY_RECORD in stored) {
            repairRecord(stored[KEY_RECORD])
        } else {
            val location = loadLegacy(stored)
            if (location != null) {
                save(location)
            }
        }
    }

    private fun repairRecord(raw: Any?) {
        val record = parseRecord(raw)
        when {
            record == null || record.opt(KEY_VERSION) !is Int -> {
                Log.w(TAG, "ignoring malformed observing location record")
            }

            record.opt(KEY_VERSION) != RECORD_VERSION -> {
                Log.w(TAG, "ignoring unsupported observing location version")
            }

            else -> {
                val storedZone = storedZoneOrNull(record)
                // opt() returns null exactly when the key is absent; a stored JSON null is JSONObject.NULL.
                val rejectedZone = record.opt(KEY_ZONE_ID)
                val location =
                    readLocation(
                        rawLatitude = (record.opt(KEY_LATITUDE) as? Number)?.toDouble(),
                        rawLongitude = (record.opt(KEY_LONGITUDE) as? Number)?.toDouble(),
                        sourceText = record.opt(KEY_SOURCE) as? String,
                    ) { storedZone ?: deviceZone() }
                if (location != null && storedZone == null) {
                    record.put(KEY_ZONE_ID, location.zoneId.id)
                    persistRecord(record)
                    val diagnosis =
                        if (rejectedZone == null) {
                            "absent observing location timezone"
                        } else {
                            "invalid observing location timezone '$rejectedZone'"
                        }
                    Log.w(TAG, "repaired $diagnosis; using ${location.zoneId.id}")
                }
            }
        }
    }

    private fun readLocation(
        rawLatitude: Double?,
        rawLongitude: Double?,
        sourceText: String?,
        zone: () -> ZoneId,
    ): ObservingLocation? {
        val latitude = rawLatitude?.takeIf(ObservingLocation::isValidLatitude)
        val longitude = rawLongitude?.takeIf(ObservingLocation::isValidLongitude)
        val source = ObservingLocation.Source.entries.firstOrNull { it.name == sourceText }
        if (latitude == null || longitude == null || source == null) {
            Log.w(TAG, "ignoring malformed observing location coordinates or source")
            return null
        }
        return ObservingLocation(latitude = latitude, longitude = longitude, source = source, zoneId = zone())
    }

    fun save(location: ObservingLocation) {
        val record =
            JSONObject()
                .put(KEY_VERSION, RECORD_VERSION)
                .put(KEY_LATITUDE, location.latitude)
                .put(KEY_LONGITUDE, location.longitude)
                .put(KEY_SOURCE, location.source.name)
                .put(KEY_ZONE_ID, location.zoneId.id)
        persistRecord(record)
    }

    private fun persistRecord(record: JSONObject) {
        val editor = preferences.edit().putString(KEY_RECORD, record.toString())
        LEGACY_KEYS.forEach(editor::remove)
        editor.apply()
    }

    fun registerListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {
        preferences.registerOnSharedPreferenceChangeListener(listener)
    }

    fun unregisterListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) {
        preferences.unregisterOnSharedPreferenceChangeListener(listener)
    }

    private companion object {
        const val PREFERENCES_NAME = "observing_location"
        const val KEY_RECORD = "location"
        const val KEY_VERSION = "version"
        const val KEY_LATITUDE = "latitude"
        const val KEY_LONGITUDE = "longitude"
        const val KEY_SOURCE = "source"
        const val KEY_ZONE_ID = "zoneId"
        const val RECORD_VERSION = 1
        val LEGACY_KEYS = listOf(KEY_LATITUDE, KEY_LONGITUDE, KEY_SOURCE)
        const val TAG = "LocationStore"

        fun parseRecord(raw: Any?): JSONObject? {
            if (raw !is String) return null
            return try {
                val parser = JSONTokener(raw)
                val record = parser.nextValue() as? JSONObject
                record?.takeIf { parser.nextClean() == '\u0000' }
            } catch (_: JSONException) {
                null
            }
        }

        fun storedZoneOrNull(record: JSONObject): ZoneId? {
            val zone =
                try {
                    (record.opt(KEY_ZONE_ID) as? String)?.let(ZoneId::of)
                } catch (_: DateTimeException) {
                    null
                }
            return zone
        }
    }
}
