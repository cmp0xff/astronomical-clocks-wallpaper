package io.github.godaniya.astronomicalclockswallpaper

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.ZoneId
import java.time.zone.ZoneRulesException

/**
 * Tests offline coordinate-to-timezone nearest-anchor lookup, alias fallback, and zone lists.
 *
 * Robolectric supplies `android.util.Log`, which `resolveZone` writes to when the device tzdb
 * rejects an identifier. The legacy-tzdb cases inject a `ZoneId` factory that rejects the six
 * identifiers newer than tzdata 2017a, the oldest tzdb an API 26 device can carry, instead of
 * asking the much newer host JDK, which cannot see that gap.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class TimeZoneLookupTest {
    @Test
    fun referenceLocationsResolveZones() {
        assertZone(latitude = 50.0875, longitude = 14.4206, expectedZone = "Europe/Prague")
        assertZone(latitude = -33.8688, longitude = 151.2093, expectedZone = "Australia/Sydney")
        assertZone(latitude = 35.6762, longitude = 139.6503, expectedZone = "Asia/Tokyo")
        assertZone(latitude = 40.7128, longitude = -74.0060, expectedZone = "America/New_York")
        assertZone(latitude = 34.0522, longitude = -118.2437, expectedZone = "America/Los_Angeles")
        assertZone(latitude = 51.5074, longitude = -0.1278, expectedZone = "Europe/London")
        assertZone(latitude = 78.2232, longitude = 15.6267, expectedZone = "Arctic/Longyearbyen")
        assertZone(latitude = -54.8019, longitude = -68.3030, expectedZone = "America/Argentina/Ushuaia")
        assertZone(latitude = 31.2304, longitude = 121.4737, expectedZone = "Asia/Shanghai")
        assertZone(latitude = 22.5726, longitude = 88.3639, expectedZone = "Asia/Kolkata")
    }

    @Test
    fun disputedPlacesResolveAnchor() {
        // Taipei
        assertZone(latitude = 25.0330, longitude = 121.5654, expectedZone = "Asia/Taipei")
        // Crimea / Simferopol
        assertZone(latitude = 44.9521, longitude = 34.1025, expectedZone = "Europe/Simferopol")
        // Jerusalem
        assertZone(latitude = 31.7683, longitude = 35.2137, expectedZone = "Asia/Jerusalem")
        // Cyprus / Nicosia
        assertZone(latitude = 35.1856, longitude = 33.3823, expectedZone = "Asia/Nicosia")
        // Western Sahara / El Aaiún
        assertZone(latitude = 27.1253, longitude = -13.1625, expectedZone = "Africa/El_Aaiun")
        // Kashmir / Srinagar (nearest tzdb anchor point is Kabul; user can override via picker)
        assertZone(latitude = 34.0837, longitude = 74.7973, expectedZone = "Asia/Kabul")
    }

    @Test
    fun antimeridianNormalizes() {
        assertZone(latitude = -17.7134, longitude = 178.0650, expectedZone = "Pacific/Fiji")
        assertZone(latitude = -21.1789, longitude = -175.1982, expectedZone = "Pacific/Tongatapu")
        // Points just east and west of antimeridian near Fiji resolve to Pacific/Fiji
        assertZone(latitude = -18.0, longitude = 179.9, expectedZone = "Pacific/Fiji")
        assertZone(latitude = -18.0, longitude = -179.9, expectedZone = "Pacific/Fiji")
    }

    @Test
    fun availableZoneIdsAreSorted() {
        val zones = TimeZoneLookup.AVAILABLE_ZONE_IDS
        assertTrue("Zone list must contain standard IANA anchors", zones.size > 300)
        assertEquals("Zone list must be sorted alphabetically", zones.sorted(), zones)
        assertEquals("Zone list must contain no duplicates", zones.distinct(), zones)
    }

    // A device whose tzdb predates 2022b knows a Kyiv coordinate only as Europe/Kiev, and the
    // lookup must degrade to those rules rather than throw.
    @Test
    fun legacyTzdbFallsBackToAlias() {
        assertEquals(
            ZoneId.of("Europe/Kiev"),
            TimeZoneLookup.lookup(latitude = 50.4333, longitude = 30.5167, zoneOf = ::legacyZoneOf),
        )
    }

    // When neither the identifier nor its alias resolves, the nearest resolvable anchor must win
    // rather than the lookup failing outright.
    @Test
    fun legacyTzdbSkipsToNextAnchor() {
        val withoutKyiv = { id: String ->
            if (id == "Europe/Kyiv" || id == "Europe/Kiev") {
                throw ZoneRulesException("unknown time zone id: $id")
            }
            ZoneId.of(id)
        }
        assertEquals(
            ZoneId.of("Europe/Chisinau"),
            TimeZoneLookup.lookup(latitude = 50.4333, longitude = 30.5167, zoneOf = withoutKyiv),
        )
    }

    @Test
    fun unresolvableZoneIdReturnsNull() {
        assertNull(TimeZoneLookup.resolveZone(id = "Not/AZone"))
    }

    // The picker's filter ignores case and the `/`, `_`, and space separators, and a blank query
    // leaves the offered list intact rather than emptying it.
    @Test
    fun filterIgnoresCaseAndSeparators() {
        val ids = listOf("America/New_York", "Etc/GMT+2", "Etc/GMT-2", "Asia/Tokyo")
        assertEquals(listOf("America/New_York"), TimeZoneLookup.filterZoneIds(ids = ids, query = "new york"))
        assertEquals(listOf("America/New_York"), TimeZoneLookup.filterZoneIds(ids = ids, query = "AMERICA/NEW_YORK"))
        assertEquals(ids, TimeZoneLookup.filterZoneIds(ids = ids, query = ""))
        assertEquals(ids, TimeZoneLookup.filterZoneIds(ids = ids, query = "   "))
        assertEquals(emptyList<String>(), TimeZoneLookup.filterZoneIds(ids = ids, query = "Nowhere"))
    }

    // Signs stay significant, so a signed-offset query reaches only the offset it names.
    @Test
    fun filterKeepsSignsSignificant() {
        val ids = listOf("Etc/GMT+2", "Etc/GMT-2")
        assertEquals(listOf("Etc/GMT+2"), TimeZoneLookup.filterZoneIds(ids = ids, query = "gmt+2"))
        assertEquals(listOf("Etc/GMT-2"), TimeZoneLookup.filterZoneIds(ids = ids, query = "gmt-2"))
    }

    // A device must not lose any anchor from the picker: an identifier its tzdb rejects still has
    // the alias map behind it, so the offered list is unchanged.
    @Test
    fun bundledIdsResolveOnLegacyTzdb() {
        assertEquals(
            TimeZoneLookup.AVAILABLE_ZONE_IDS,
            TimeZoneLookup.resolvableZoneIds(zoneOf = ::legacyZoneOf),
        )
    }

    private fun assertZone(latitude: Double, longitude: Double, expectedZone: String) {
        val resolved = TimeZoneLookup.lookup(latitude = latitude, longitude = longitude)
        assertEquals(ZoneId.of(expectedZone), resolved)
    }

    // Stands in for a device tzdb as old as tzdata 2017a. Each identifier below was checked to be
    // absent from 2017a's zone definitions; every alias target is present there.
    private fun legacyZoneOf(id: String): ZoneId {
        if (id in LEGACY_TZDB_MISSING_IDS) throw ZoneRulesException("unknown time zone id: $id")
        return ZoneId.of(id)
    }

    private companion object {
        val LEGACY_TZDB_MISSING_IDS =
            setOf(
                "America/Ciudad_Juarez",
                "America/Coyhaique",
                "America/Nuuk",
                "Asia/Qostanay",
                "Europe/Kyiv",
                "Pacific/Kanton",
            )
    }
}
