package io.github.godaniya.astronomicalclockswallpaper

import android.util.Log
import java.time.DateTimeException
import java.time.ZoneId
import java.util.Locale
import kotlin.math.cos

/**
 * Canonical anchor point for an IANA timezone from the public domain tzdb (zone.tab).
 *
 * Coordinates are expressed in degrees. [zoneId] is a canonical IANA timezone identifier
 * defined in RFC 6557 / ICANN and recognized by [ZoneId.of].
 */
internal data class TimeZoneAnchor(val zoneId: String, val latitude: Double, val longitude: Double)

/**
 * Offline nearest-anchor timezone resolver for manual geographic coordinates.
 *
 * Derived from the IANA Time Zone Database (tzdb) zone table, which is in the public domain.
 * This lookup computes the nearest anchor coordinate to determine the primary civil timezone
 * for any (latitude, longitude) without network access, third-party SDKs, or geopolitical
 * cartographic claims.
 */
internal object TimeZoneLookup {
    private const val FULL_TURN_DEGREES = 360.0
    private const val HALF_TURN_DEGREES = 180.0
    private const val UTC_ZONE_ID = "UTC"
    private const val TAG = "TimeZoneLookup"

    val CANONICAL_ANCHORS: List<TimeZoneAnchor> =
        listOf(
            TimeZoneAnchor(zoneId = "Africa/Abidjan", latitude = 5.3167, longitude = -4.0333),
            TimeZoneAnchor(zoneId = "Africa/Accra", latitude = 5.55, longitude = -0.2167),
            TimeZoneAnchor(zoneId = "Africa/Addis_Ababa", latitude = 9.0333, longitude = 38.7),
            TimeZoneAnchor(zoneId = "Africa/Algiers", latitude = 36.7833, longitude = 3.05),
            TimeZoneAnchor(zoneId = "Africa/Asmara", latitude = 15.3333, longitude = 38.8833),
            TimeZoneAnchor(zoneId = "Africa/Bamako", latitude = 12.65, longitude = -8.0),
            TimeZoneAnchor(zoneId = "Africa/Bangui", latitude = 4.3667, longitude = 18.5833),
            TimeZoneAnchor(zoneId = "Africa/Banjul", latitude = 13.4667, longitude = -16.65),
            TimeZoneAnchor(zoneId = "Africa/Bissau", latitude = 11.85, longitude = -15.5833),
            TimeZoneAnchor(zoneId = "Africa/Blantyre", latitude = -15.7833, longitude = 35.0),
            TimeZoneAnchor(zoneId = "Africa/Brazzaville", latitude = -4.2667, longitude = 15.2833),
            TimeZoneAnchor(zoneId = "Africa/Bujumbura", latitude = -3.3833, longitude = 29.3667),
            TimeZoneAnchor(zoneId = "Africa/Cairo", latitude = 30.05, longitude = 31.25),
            TimeZoneAnchor(zoneId = "Africa/Casablanca", latitude = 33.65, longitude = -7.5833),
            TimeZoneAnchor(zoneId = "Africa/Ceuta", latitude = 35.8833, longitude = -5.3167),
            TimeZoneAnchor(zoneId = "Africa/Conakry", latitude = 9.5167, longitude = -13.7167),
            TimeZoneAnchor(zoneId = "Africa/Dakar", latitude = 14.6667, longitude = -17.4333),
            TimeZoneAnchor(zoneId = "Africa/Dar_es_Salaam", latitude = -6.8, longitude = 39.2833),
            TimeZoneAnchor(zoneId = "Africa/Djibouti", latitude = 11.6, longitude = 43.15),
            TimeZoneAnchor(zoneId = "Africa/Douala", latitude = 4.05, longitude = 9.7),
            TimeZoneAnchor(zoneId = "Africa/El_Aaiun", latitude = 27.15, longitude = -13.2),
            TimeZoneAnchor(zoneId = "Africa/Freetown", latitude = 8.5, longitude = -13.25),
            TimeZoneAnchor(zoneId = "Africa/Gaborone", latitude = -24.65, longitude = 25.9167),
            TimeZoneAnchor(zoneId = "Africa/Harare", latitude = -17.8333, longitude = 31.05),
            TimeZoneAnchor(zoneId = "Africa/Johannesburg", latitude = -26.25, longitude = 28.0),
            TimeZoneAnchor(zoneId = "Africa/Juba", latitude = 4.85, longitude = 31.6167),
            TimeZoneAnchor(zoneId = "Africa/Kampala", latitude = 0.3167, longitude = 32.4167),
            TimeZoneAnchor(zoneId = "Africa/Khartoum", latitude = 15.6, longitude = 32.5333),
            TimeZoneAnchor(zoneId = "Africa/Kigali", latitude = -1.95, longitude = 30.0667),
            TimeZoneAnchor(zoneId = "Africa/Kinshasa", latitude = -4.3, longitude = 15.3),
            TimeZoneAnchor(zoneId = "Africa/Lagos", latitude = 6.45, longitude = 3.4),
            TimeZoneAnchor(zoneId = "Africa/Libreville", latitude = 0.3833, longitude = 9.45),
            TimeZoneAnchor(zoneId = "Africa/Lome", latitude = 6.1333, longitude = 1.2167),
            TimeZoneAnchor(zoneId = "Africa/Luanda", latitude = -8.8, longitude = 13.2333),
            TimeZoneAnchor(zoneId = "Africa/Lubumbashi", latitude = -11.6667, longitude = 27.4667),
            TimeZoneAnchor(zoneId = "Africa/Lusaka", latitude = -15.4167, longitude = 28.2833),
            TimeZoneAnchor(zoneId = "Africa/Malabo", latitude = 3.75, longitude = 8.7833),
            TimeZoneAnchor(zoneId = "Africa/Maputo", latitude = -25.9667, longitude = 32.5833),
            TimeZoneAnchor(zoneId = "Africa/Maseru", latitude = -29.4667, longitude = 27.5),
            TimeZoneAnchor(zoneId = "Africa/Mbabane", latitude = -26.3, longitude = 31.1),
            TimeZoneAnchor(zoneId = "Africa/Mogadishu", latitude = 2.0667, longitude = 45.3667),
            TimeZoneAnchor(zoneId = "Africa/Monrovia", latitude = 6.3, longitude = -10.7833),
            TimeZoneAnchor(zoneId = "Africa/Nairobi", latitude = -1.2833, longitude = 36.8167),
            TimeZoneAnchor(zoneId = "Africa/Ndjamena", latitude = 12.1167, longitude = 15.05),
            TimeZoneAnchor(zoneId = "Africa/Niamey", latitude = 13.5167, longitude = 2.1167),
            TimeZoneAnchor(zoneId = "Africa/Nouakchott", latitude = 18.1, longitude = -15.95),
            TimeZoneAnchor(zoneId = "Africa/Ouagadougou", latitude = 12.3667, longitude = -1.5167),
            TimeZoneAnchor(zoneId = "Africa/Porto-Novo", latitude = 6.4833, longitude = 2.6167),
            TimeZoneAnchor(zoneId = "Africa/Sao_Tome", latitude = 0.3333, longitude = 6.7333),
            TimeZoneAnchor(zoneId = "Africa/Tripoli", latitude = 32.9, longitude = 13.1833),
            TimeZoneAnchor(zoneId = "Africa/Tunis", latitude = 36.8, longitude = 10.1833),
            TimeZoneAnchor(zoneId = "Africa/Windhoek", latitude = -22.5667, longitude = 17.1),
            TimeZoneAnchor(zoneId = "America/Adak", latitude = 51.88, longitude = -176.6581),
            TimeZoneAnchor(zoneId = "America/Anchorage", latitude = 61.2181, longitude = -149.9003),
            TimeZoneAnchor(zoneId = "America/Anguilla", latitude = 18.2, longitude = -63.0667),
            TimeZoneAnchor(zoneId = "America/Antigua", latitude = 17.05, longitude = -61.8),
            TimeZoneAnchor(zoneId = "America/Araguaina", latitude = -7.2, longitude = -48.2),
            TimeZoneAnchor(zoneId = "America/Argentina/Buenos_Aires", latitude = -34.6, longitude = -58.45),
            TimeZoneAnchor(zoneId = "America/Argentina/Catamarca", latitude = -28.4667, longitude = -65.7833),
            TimeZoneAnchor(zoneId = "America/Argentina/Cordoba", latitude = -31.4, longitude = -64.1833),
            TimeZoneAnchor(zoneId = "America/Argentina/Jujuy", latitude = -24.1833, longitude = -65.3),
            TimeZoneAnchor(zoneId = "America/Argentina/La_Rioja", latitude = -29.4333, longitude = -66.85),
            TimeZoneAnchor(zoneId = "America/Argentina/Mendoza", latitude = -32.8833, longitude = -68.8167),
            TimeZoneAnchor(zoneId = "America/Argentina/Rio_Gallegos", latitude = -51.6333, longitude = -69.2167),
            TimeZoneAnchor(zoneId = "America/Argentina/Salta", latitude = -24.7833, longitude = -65.4167),
            TimeZoneAnchor(zoneId = "America/Argentina/San_Juan", latitude = -31.5333, longitude = -68.5167),
            TimeZoneAnchor(zoneId = "America/Argentina/San_Luis", latitude = -33.3167, longitude = -66.35),
            TimeZoneAnchor(zoneId = "America/Argentina/Tucuman", latitude = -26.8167, longitude = -65.2167),
            TimeZoneAnchor(zoneId = "America/Argentina/Ushuaia", latitude = -54.8, longitude = -68.3),
            TimeZoneAnchor(zoneId = "America/Aruba", latitude = 12.5, longitude = -69.9667),
            TimeZoneAnchor(zoneId = "America/Asuncion", latitude = -25.2667, longitude = -57.6667),
            TimeZoneAnchor(zoneId = "America/Atikokan", latitude = 48.7586, longitude = -91.6217),
            TimeZoneAnchor(zoneId = "America/Bahia", latitude = -12.9833, longitude = -38.5167),
            TimeZoneAnchor(zoneId = "America/Bahia_Banderas", latitude = 20.8, longitude = -105.25),
            TimeZoneAnchor(zoneId = "America/Barbados", latitude = 13.1, longitude = -59.6167),
            TimeZoneAnchor(zoneId = "America/Belem", latitude = -1.45, longitude = -48.4833),
            TimeZoneAnchor(zoneId = "America/Belize", latitude = 17.5, longitude = -88.2),
            TimeZoneAnchor(zoneId = "America/Blanc-Sablon", latitude = 51.4167, longitude = -57.1167),
            TimeZoneAnchor(zoneId = "America/Boa_Vista", latitude = 2.8167, longitude = -60.6667),
            TimeZoneAnchor(zoneId = "America/Bogota", latitude = 4.6, longitude = -74.0833),
            TimeZoneAnchor(zoneId = "America/Boise", latitude = 43.6136, longitude = -116.2025),
            TimeZoneAnchor(zoneId = "America/Cambridge_Bay", latitude = 69.1139, longitude = -105.0528),
            TimeZoneAnchor(zoneId = "America/Campo_Grande", latitude = -20.45, longitude = -54.6167),
            TimeZoneAnchor(zoneId = "America/Cancun", latitude = 21.0833, longitude = -86.7667),
            TimeZoneAnchor(zoneId = "America/Caracas", latitude = 10.5, longitude = -66.9333),
            TimeZoneAnchor(zoneId = "America/Cayenne", latitude = 4.9333, longitude = -52.3333),
            TimeZoneAnchor(zoneId = "America/Cayman", latitude = 19.3, longitude = -81.3833),
            TimeZoneAnchor(zoneId = "America/Chicago", latitude = 41.85, longitude = -87.65),
            TimeZoneAnchor(zoneId = "America/Chihuahua", latitude = 28.6333, longitude = -106.0833),
            TimeZoneAnchor(zoneId = "America/Ciudad_Juarez", latitude = 31.7333, longitude = -106.4833),
            TimeZoneAnchor(zoneId = "America/Costa_Rica", latitude = 9.9333, longitude = -84.0833),
            TimeZoneAnchor(zoneId = "America/Coyhaique", latitude = -45.5667, longitude = -72.0667),
            TimeZoneAnchor(zoneId = "America/Creston", latitude = 49.1, longitude = -116.5167),
            TimeZoneAnchor(zoneId = "America/Cuiaba", latitude = -15.5833, longitude = -56.0833),
            TimeZoneAnchor(zoneId = "America/Curacao", latitude = 12.1833, longitude = -69.0),
            TimeZoneAnchor(zoneId = "America/Danmarkshavn", latitude = 76.7667, longitude = -18.6667),
            TimeZoneAnchor(zoneId = "America/Dawson", latitude = 64.0667, longitude = -139.4167),
            TimeZoneAnchor(zoneId = "America/Dawson_Creek", latitude = 55.7667, longitude = -120.2333),
            TimeZoneAnchor(zoneId = "America/Denver", latitude = 39.7392, longitude = -104.9842),
            TimeZoneAnchor(zoneId = "America/Detroit", latitude = 42.3314, longitude = -83.0458),
            TimeZoneAnchor(zoneId = "America/Dominica", latitude = 15.3, longitude = -61.4),
            TimeZoneAnchor(zoneId = "America/Edmonton", latitude = 53.55, longitude = -113.4667),
            TimeZoneAnchor(zoneId = "America/Eirunepe", latitude = -6.6667, longitude = -69.8667),
            TimeZoneAnchor(zoneId = "America/El_Salvador", latitude = 13.7, longitude = -89.2),
            TimeZoneAnchor(zoneId = "America/Fort_Nelson", latitude = 58.8, longitude = -122.7),
            TimeZoneAnchor(zoneId = "America/Fortaleza", latitude = -3.7167, longitude = -38.5),
            TimeZoneAnchor(zoneId = "America/Glace_Bay", latitude = 46.2, longitude = -59.95),
            TimeZoneAnchor(zoneId = "America/Goose_Bay", latitude = 53.3333, longitude = -60.4167),
            TimeZoneAnchor(zoneId = "America/Grand_Turk", latitude = 21.4667, longitude = -71.1333),
            TimeZoneAnchor(zoneId = "America/Grenada", latitude = 12.05, longitude = -61.75),
            TimeZoneAnchor(zoneId = "America/Guadeloupe", latitude = 16.2333, longitude = -61.5333),
            TimeZoneAnchor(zoneId = "America/Guatemala", latitude = 14.6333, longitude = -90.5167),
            TimeZoneAnchor(zoneId = "America/Guayaquil", latitude = -2.1667, longitude = -79.8333),
            TimeZoneAnchor(zoneId = "America/Guyana", latitude = 6.8, longitude = -58.1667),
            TimeZoneAnchor(zoneId = "America/Halifax", latitude = 44.65, longitude = -63.6),
            TimeZoneAnchor(zoneId = "America/Havana", latitude = 23.1333, longitude = -82.3667),
            TimeZoneAnchor(zoneId = "America/Hermosillo", latitude = 29.0667, longitude = -110.9667),
            TimeZoneAnchor(zoneId = "America/Indiana/Indianapolis", latitude = 39.7683, longitude = -86.1581),
            TimeZoneAnchor(zoneId = "America/Indiana/Knox", latitude = 41.2958, longitude = -86.625),
            TimeZoneAnchor(zoneId = "America/Indiana/Marengo", latitude = 38.3756, longitude = -86.3447),
            TimeZoneAnchor(zoneId = "America/Indiana/Petersburg", latitude = 38.4919, longitude = -87.2786),
            TimeZoneAnchor(zoneId = "America/Indiana/Tell_City", latitude = 37.9531, longitude = -86.7614),
            TimeZoneAnchor(zoneId = "America/Indiana/Vevay", latitude = 38.7478, longitude = -85.0672),
            TimeZoneAnchor(zoneId = "America/Indiana/Vincennes", latitude = 38.6772, longitude = -87.5286),
            TimeZoneAnchor(zoneId = "America/Indiana/Winamac", latitude = 41.0514, longitude = -86.6031),
            TimeZoneAnchor(zoneId = "America/Inuvik", latitude = 68.3497, longitude = -133.7167),
            TimeZoneAnchor(zoneId = "America/Iqaluit", latitude = 63.7333, longitude = -68.4667),
            TimeZoneAnchor(zoneId = "America/Jamaica", latitude = 17.9681, longitude = -76.7933),
            TimeZoneAnchor(zoneId = "America/Juneau", latitude = 58.3019, longitude = -134.4197),
            TimeZoneAnchor(zoneId = "America/Kentucky/Louisville", latitude = 38.2542, longitude = -85.7594),
            TimeZoneAnchor(zoneId = "America/Kentucky/Monticello", latitude = 36.8297, longitude = -84.8492),
            TimeZoneAnchor(zoneId = "America/Kralendijk", latitude = 12.1508, longitude = -68.2767),
            TimeZoneAnchor(zoneId = "America/La_Paz", latitude = -16.5, longitude = -68.15),
            TimeZoneAnchor(zoneId = "America/Lima", latitude = -12.05, longitude = -77.05),
            TimeZoneAnchor(zoneId = "America/Los_Angeles", latitude = 34.0522, longitude = -118.2428),
            TimeZoneAnchor(zoneId = "America/Lower_Princes", latitude = 18.0514, longitude = -63.0472),
            TimeZoneAnchor(zoneId = "America/Maceio", latitude = -9.6667, longitude = -35.7167),
            TimeZoneAnchor(zoneId = "America/Managua", latitude = 12.15, longitude = -86.2833),
            TimeZoneAnchor(zoneId = "America/Manaus", latitude = -3.1333, longitude = -60.0167),
            TimeZoneAnchor(zoneId = "America/Marigot", latitude = 18.0667, longitude = -63.0833),
            TimeZoneAnchor(zoneId = "America/Martinique", latitude = 14.6, longitude = -61.0833),
            TimeZoneAnchor(zoneId = "America/Matamoros", latitude = 25.8333, longitude = -97.5),
            TimeZoneAnchor(zoneId = "America/Mazatlan", latitude = 23.2167, longitude = -106.4167),
            TimeZoneAnchor(zoneId = "America/Menominee", latitude = 45.1078, longitude = -87.6142),
            TimeZoneAnchor(zoneId = "America/Merida", latitude = 20.9667, longitude = -89.6167),
            TimeZoneAnchor(zoneId = "America/Metlakatla", latitude = 55.1269, longitude = -131.5764),
            TimeZoneAnchor(zoneId = "America/Mexico_City", latitude = 19.4, longitude = -99.15),
            TimeZoneAnchor(zoneId = "America/Miquelon", latitude = 47.05, longitude = -56.3333),
            TimeZoneAnchor(zoneId = "America/Moncton", latitude = 46.1, longitude = -64.7833),
            TimeZoneAnchor(zoneId = "America/Monterrey", latitude = 25.6667, longitude = -100.3167),
            TimeZoneAnchor(zoneId = "America/Montevideo", latitude = -34.9092, longitude = -56.2125),
            TimeZoneAnchor(zoneId = "America/Montserrat", latitude = 16.7167, longitude = -62.2167),
            TimeZoneAnchor(zoneId = "America/Nassau", latitude = 25.0833, longitude = -77.35),
            TimeZoneAnchor(zoneId = "America/New_York", latitude = 40.7142, longitude = -74.0064),
            TimeZoneAnchor(zoneId = "America/Nome", latitude = 64.5011, longitude = -165.4064),
            TimeZoneAnchor(zoneId = "America/Noronha", latitude = -3.85, longitude = -32.4167),
            TimeZoneAnchor(zoneId = "America/North_Dakota/Beulah", latitude = 47.2642, longitude = -101.7778),
            TimeZoneAnchor(zoneId = "America/North_Dakota/Center", latitude = 47.1164, longitude = -101.2992),
            TimeZoneAnchor(zoneId = "America/North_Dakota/New_Salem", latitude = 46.845, longitude = -101.4108),
            TimeZoneAnchor(zoneId = "America/Nuuk", latitude = 64.1833, longitude = -51.7333),
            TimeZoneAnchor(zoneId = "America/Ojinaga", latitude = 29.5667, longitude = -104.4167),
            TimeZoneAnchor(zoneId = "America/Panama", latitude = 8.9667, longitude = -79.5333),
            TimeZoneAnchor(zoneId = "America/Paramaribo", latitude = 5.8333, longitude = -55.1667),
            TimeZoneAnchor(zoneId = "America/Phoenix", latitude = 33.4483, longitude = -112.0733),
            TimeZoneAnchor(zoneId = "America/Port-au-Prince", latitude = 18.5333, longitude = -72.3333),
            TimeZoneAnchor(zoneId = "America/Port_of_Spain", latitude = 10.65, longitude = -61.5167),
            TimeZoneAnchor(zoneId = "America/Porto_Velho", latitude = -8.7667, longitude = -63.9),
            TimeZoneAnchor(zoneId = "America/Puerto_Rico", latitude = 18.4683, longitude = -66.1061),
            TimeZoneAnchor(zoneId = "America/Punta_Arenas", latitude = -53.15, longitude = -70.9167),
            TimeZoneAnchor(zoneId = "America/Rankin_Inlet", latitude = 62.8167, longitude = -92.0831),
            TimeZoneAnchor(zoneId = "America/Recife", latitude = -8.05, longitude = -34.9),
            TimeZoneAnchor(zoneId = "America/Regina", latitude = 50.4, longitude = -104.65),
            TimeZoneAnchor(zoneId = "America/Resolute", latitude = 74.6956, longitude = -94.8292),
            TimeZoneAnchor(zoneId = "America/Rio_Branco", latitude = -9.9667, longitude = -67.8),
            TimeZoneAnchor(zoneId = "America/Santarem", latitude = -2.4333, longitude = -54.8667),
            TimeZoneAnchor(zoneId = "America/Santiago", latitude = -33.45, longitude = -70.6667),
            TimeZoneAnchor(zoneId = "America/Santo_Domingo", latitude = 18.4667, longitude = -69.9),
            TimeZoneAnchor(zoneId = "America/Sao_Paulo", latitude = -23.5333, longitude = -46.6167),
            TimeZoneAnchor(zoneId = "America/Scoresbysund", latitude = 70.4833, longitude = -21.9667),
            TimeZoneAnchor(zoneId = "America/Sitka", latitude = 57.1764, longitude = -135.3019),
            TimeZoneAnchor(zoneId = "America/St_Barthelemy", latitude = 17.8833, longitude = -62.85),
            TimeZoneAnchor(zoneId = "America/St_Johns", latitude = 47.5667, longitude = -52.7167),
            TimeZoneAnchor(zoneId = "America/St_Kitts", latitude = 17.3, longitude = -62.7167),
            TimeZoneAnchor(zoneId = "America/St_Lucia", latitude = 14.0167, longitude = -61.0),
            TimeZoneAnchor(zoneId = "America/St_Thomas", latitude = 18.35, longitude = -64.9333),
            TimeZoneAnchor(zoneId = "America/St_Vincent", latitude = 13.15, longitude = -61.2333),
            TimeZoneAnchor(zoneId = "America/Swift_Current", latitude = 50.2833, longitude = -107.8333),
            TimeZoneAnchor(zoneId = "America/Tegucigalpa", latitude = 14.1, longitude = -87.2167),
            TimeZoneAnchor(zoneId = "America/Thule", latitude = 76.5667, longitude = -68.7833),
            TimeZoneAnchor(zoneId = "America/Tijuana", latitude = 32.5333, longitude = -117.0167),
            TimeZoneAnchor(zoneId = "America/Toronto", latitude = 43.65, longitude = -79.3833),
            TimeZoneAnchor(zoneId = "America/Tortola", latitude = 18.45, longitude = -64.6167),
            TimeZoneAnchor(zoneId = "America/Vancouver", latitude = 49.2667, longitude = -123.1167),
            TimeZoneAnchor(zoneId = "America/Whitehorse", latitude = 60.7167, longitude = -135.05),
            TimeZoneAnchor(zoneId = "America/Winnipeg", latitude = 49.8833, longitude = -97.15),
            TimeZoneAnchor(zoneId = "America/Yakutat", latitude = 59.5469, longitude = -139.7272),
            TimeZoneAnchor(zoneId = "Antarctica/Casey", latitude = -66.2833, longitude = 110.5167),
            TimeZoneAnchor(zoneId = "Antarctica/Davis", latitude = -68.5833, longitude = 77.9667),
            TimeZoneAnchor(zoneId = "Antarctica/DumontDUrville", latitude = -66.6667, longitude = 140.0167),
            TimeZoneAnchor(zoneId = "Antarctica/Macquarie", latitude = -54.5, longitude = 158.95),
            TimeZoneAnchor(zoneId = "Antarctica/Mawson", latitude = -67.6, longitude = 62.8833),
            TimeZoneAnchor(zoneId = "Antarctica/McMurdo", latitude = -77.8333, longitude = 166.6),
            TimeZoneAnchor(zoneId = "Antarctica/Palmer", latitude = -64.8, longitude = -64.1),
            TimeZoneAnchor(zoneId = "Antarctica/Rothera", latitude = -67.5667, longitude = -68.1333),
            TimeZoneAnchor(zoneId = "Antarctica/Syowa", latitude = -69.0061, longitude = 39.59),
            TimeZoneAnchor(zoneId = "Antarctica/Troll", latitude = -72.0114, longitude = 2.535),
            TimeZoneAnchor(zoneId = "Antarctica/Vostok", latitude = -78.4, longitude = 106.9),
            TimeZoneAnchor(zoneId = "Arctic/Longyearbyen", latitude = 78.0, longitude = 16.0),
            TimeZoneAnchor(zoneId = "Asia/Aden", latitude = 12.75, longitude = 45.2),
            TimeZoneAnchor(zoneId = "Asia/Almaty", latitude = 43.25, longitude = 76.95),
            TimeZoneAnchor(zoneId = "Asia/Amman", latitude = 31.95, longitude = 35.9333),
            TimeZoneAnchor(zoneId = "Asia/Anadyr", latitude = 64.75, longitude = 177.4833),
            TimeZoneAnchor(zoneId = "Asia/Aqtau", latitude = 44.5167, longitude = 50.2667),
            TimeZoneAnchor(zoneId = "Asia/Aqtobe", latitude = 50.2833, longitude = 57.1667),
            TimeZoneAnchor(zoneId = "Asia/Ashgabat", latitude = 37.95, longitude = 58.3833),
            TimeZoneAnchor(zoneId = "Asia/Atyrau", latitude = 47.1167, longitude = 51.9333),
            TimeZoneAnchor(zoneId = "Asia/Baghdad", latitude = 33.35, longitude = 44.4167),
            TimeZoneAnchor(zoneId = "Asia/Bahrain", latitude = 26.3833, longitude = 50.5833),
            TimeZoneAnchor(zoneId = "Asia/Baku", latitude = 40.3833, longitude = 49.85),
            TimeZoneAnchor(zoneId = "Asia/Bangkok", latitude = 13.75, longitude = 100.5167),
            TimeZoneAnchor(zoneId = "Asia/Barnaul", latitude = 53.3667, longitude = 83.75),
            TimeZoneAnchor(zoneId = "Asia/Beirut", latitude = 33.8833, longitude = 35.5),
            TimeZoneAnchor(zoneId = "Asia/Bishkek", latitude = 42.9, longitude = 74.6),
            TimeZoneAnchor(zoneId = "Asia/Brunei", latitude = 4.9333, longitude = 114.9167),
            TimeZoneAnchor(zoneId = "Asia/Chita", latitude = 52.05, longitude = 113.4667),
            TimeZoneAnchor(zoneId = "Asia/Colombo", latitude = 6.9333, longitude = 79.85),
            TimeZoneAnchor(zoneId = "Asia/Damascus", latitude = 33.5, longitude = 36.3),
            TimeZoneAnchor(zoneId = "Asia/Dhaka", latitude = 23.7167, longitude = 90.4167),
            TimeZoneAnchor(zoneId = "Asia/Dili", latitude = -8.55, longitude = 125.5833),
            TimeZoneAnchor(zoneId = "Asia/Dubai", latitude = 25.3, longitude = 55.3),
            TimeZoneAnchor(zoneId = "Asia/Dushanbe", latitude = 38.5833, longitude = 68.8),
            TimeZoneAnchor(zoneId = "Asia/Famagusta", latitude = 35.1167, longitude = 33.95),
            TimeZoneAnchor(zoneId = "Asia/Gaza", latitude = 31.5, longitude = 34.4667),
            TimeZoneAnchor(zoneId = "Asia/Hebron", latitude = 31.5333, longitude = 35.095),
            TimeZoneAnchor(zoneId = "Asia/Ho_Chi_Minh", latitude = 10.75, longitude = 106.6667),
            TimeZoneAnchor(zoneId = "Asia/Hong_Kong", latitude = 22.2833, longitude = 114.15),
            TimeZoneAnchor(zoneId = "Asia/Hovd", latitude = 48.0167, longitude = 91.65),
            TimeZoneAnchor(zoneId = "Asia/Irkutsk", latitude = 52.2667, longitude = 104.3333),
            TimeZoneAnchor(zoneId = "Asia/Jakarta", latitude = -6.1667, longitude = 106.8),
            TimeZoneAnchor(zoneId = "Asia/Jayapura", latitude = -2.5333, longitude = 140.7),
            TimeZoneAnchor(zoneId = "Asia/Jerusalem", latitude = 31.7806, longitude = 35.2239),
            TimeZoneAnchor(zoneId = "Asia/Kabul", latitude = 34.5167, longitude = 69.2),
            TimeZoneAnchor(zoneId = "Asia/Kamchatka", latitude = 53.0167, longitude = 158.65),
            TimeZoneAnchor(zoneId = "Asia/Karachi", latitude = 24.8667, longitude = 67.05),
            TimeZoneAnchor(zoneId = "Asia/Kathmandu", latitude = 27.7167, longitude = 85.3167),
            TimeZoneAnchor(zoneId = "Asia/Khandyga", latitude = 62.6564, longitude = 135.5539),
            TimeZoneAnchor(zoneId = "Asia/Kolkata", latitude = 22.5333, longitude = 88.3667),
            TimeZoneAnchor(zoneId = "Asia/Krasnoyarsk", latitude = 56.0167, longitude = 92.8333),
            TimeZoneAnchor(zoneId = "Asia/Kuala_Lumpur", latitude = 3.1667, longitude = 101.7),
            TimeZoneAnchor(zoneId = "Asia/Kuching", latitude = 1.55, longitude = 110.3333),
            TimeZoneAnchor(zoneId = "Asia/Kuwait", latitude = 29.3333, longitude = 47.9833),
            TimeZoneAnchor(zoneId = "Asia/Macau", latitude = 22.1972, longitude = 113.5417),
            TimeZoneAnchor(zoneId = "Asia/Magadan", latitude = 59.5667, longitude = 150.8),
            TimeZoneAnchor(zoneId = "Asia/Makassar", latitude = -5.1167, longitude = 119.4),
            TimeZoneAnchor(zoneId = "Asia/Manila", latitude = 14.5867, longitude = 120.9678),
            TimeZoneAnchor(zoneId = "Asia/Muscat", latitude = 23.6, longitude = 58.5833),
            TimeZoneAnchor(zoneId = "Asia/Nicosia", latitude = 35.1667, longitude = 33.3667),
            TimeZoneAnchor(zoneId = "Asia/Novokuznetsk", latitude = 53.75, longitude = 87.1167),
            TimeZoneAnchor(zoneId = "Asia/Novosibirsk", latitude = 55.0333, longitude = 82.9167),
            TimeZoneAnchor(zoneId = "Asia/Omsk", latitude = 55.0, longitude = 73.4),
            TimeZoneAnchor(zoneId = "Asia/Oral", latitude = 51.2167, longitude = 51.35),
            TimeZoneAnchor(zoneId = "Asia/Phnom_Penh", latitude = 11.55, longitude = 104.9167),
            TimeZoneAnchor(zoneId = "Asia/Pontianak", latitude = -0.0333, longitude = 109.3333),
            TimeZoneAnchor(zoneId = "Asia/Pyongyang", latitude = 39.0167, longitude = 125.75),
            TimeZoneAnchor(zoneId = "Asia/Qatar", latitude = 25.2833, longitude = 51.5333),
            TimeZoneAnchor(zoneId = "Asia/Qostanay", latitude = 53.2, longitude = 63.6167),
            TimeZoneAnchor(zoneId = "Asia/Qyzylorda", latitude = 44.8, longitude = 65.4667),
            TimeZoneAnchor(zoneId = "Asia/Riyadh", latitude = 24.6333, longitude = 46.7167),
            TimeZoneAnchor(zoneId = "Asia/Sakhalin", latitude = 46.9667, longitude = 142.7),
            TimeZoneAnchor(zoneId = "Asia/Samarkand", latitude = 39.6667, longitude = 66.8),
            TimeZoneAnchor(zoneId = "Asia/Seoul", latitude = 37.55, longitude = 126.9667),
            TimeZoneAnchor(zoneId = "Asia/Shanghai", latitude = 31.2333, longitude = 121.4667),
            TimeZoneAnchor(zoneId = "Asia/Singapore", latitude = 1.2833, longitude = 103.85),
            TimeZoneAnchor(zoneId = "Asia/Srednekolymsk", latitude = 67.4667, longitude = 153.7167),
            TimeZoneAnchor(zoneId = "Asia/Taipei", latitude = 25.05, longitude = 121.5),
            TimeZoneAnchor(zoneId = "Asia/Tashkent", latitude = 41.3333, longitude = 69.3),
            TimeZoneAnchor(zoneId = "Asia/Tbilisi", latitude = 41.7167, longitude = 44.8167),
            TimeZoneAnchor(zoneId = "Asia/Tehran", latitude = 35.6667, longitude = 51.4333),
            TimeZoneAnchor(zoneId = "Asia/Thimphu", latitude = 27.4667, longitude = 89.65),
            TimeZoneAnchor(zoneId = "Asia/Tokyo", latitude = 35.6544, longitude = 139.7447),
            TimeZoneAnchor(zoneId = "Asia/Tomsk", latitude = 56.5, longitude = 84.9667),
            TimeZoneAnchor(zoneId = "Asia/Ulaanbaatar", latitude = 47.9167, longitude = 106.8833),
            TimeZoneAnchor(zoneId = "Asia/Urumqi", latitude = 43.8, longitude = 87.5833),
            TimeZoneAnchor(zoneId = "Asia/Ust-Nera", latitude = 64.5603, longitude = 143.2267),
            TimeZoneAnchor(zoneId = "Asia/Vientiane", latitude = 17.9667, longitude = 102.6),
            TimeZoneAnchor(zoneId = "Asia/Vladivostok", latitude = 43.1667, longitude = 131.9333),
            TimeZoneAnchor(zoneId = "Asia/Yakutsk", latitude = 62.0, longitude = 129.6667),
            TimeZoneAnchor(zoneId = "Asia/Yangon", latitude = 16.7833, longitude = 96.1667),
            TimeZoneAnchor(zoneId = "Asia/Yekaterinburg", latitude = 56.85, longitude = 60.6),
            TimeZoneAnchor(zoneId = "Asia/Yerevan", latitude = 40.1833, longitude = 44.5),
            TimeZoneAnchor(zoneId = "Atlantic/Azores", latitude = 37.7333, longitude = -25.6667),
            TimeZoneAnchor(zoneId = "Atlantic/Bermuda", latitude = 32.2833, longitude = -64.7667),
            TimeZoneAnchor(zoneId = "Atlantic/Canary", latitude = 28.1, longitude = -15.4),
            TimeZoneAnchor(zoneId = "Atlantic/Cape_Verde", latitude = 14.9167, longitude = -23.5167),
            TimeZoneAnchor(zoneId = "Atlantic/Faroe", latitude = 62.0167, longitude = -6.7667),
            TimeZoneAnchor(zoneId = "Atlantic/Madeira", latitude = 32.6333, longitude = -16.9),
            TimeZoneAnchor(zoneId = "Atlantic/Reykjavik", latitude = 64.15, longitude = -21.85),
            TimeZoneAnchor(zoneId = "Atlantic/South_Georgia", latitude = -54.2667, longitude = -36.5333),
            TimeZoneAnchor(zoneId = "Atlantic/St_Helena", latitude = -15.9167, longitude = -5.7),
            TimeZoneAnchor(zoneId = "Atlantic/Stanley", latitude = -51.7, longitude = -57.85),
            TimeZoneAnchor(zoneId = "Australia/Adelaide", latitude = -34.9167, longitude = 138.5833),
            TimeZoneAnchor(zoneId = "Australia/Brisbane", latitude = -27.4667, longitude = 153.0333),
            TimeZoneAnchor(zoneId = "Australia/Broken_Hill", latitude = -31.95, longitude = 141.45),
            TimeZoneAnchor(zoneId = "Australia/Darwin", latitude = -12.4667, longitude = 130.8333),
            TimeZoneAnchor(zoneId = "Australia/Eucla", latitude = -31.7167, longitude = 128.8667),
            TimeZoneAnchor(zoneId = "Australia/Hobart", latitude = -42.8833, longitude = 147.3167),
            TimeZoneAnchor(zoneId = "Australia/Lindeman", latitude = -20.2667, longitude = 149.0),
            TimeZoneAnchor(zoneId = "Australia/Lord_Howe", latitude = -31.55, longitude = 159.0833),
            TimeZoneAnchor(zoneId = "Australia/Melbourne", latitude = -37.8167, longitude = 144.9667),
            TimeZoneAnchor(zoneId = "Australia/Perth", latitude = -31.95, longitude = 115.85),
            TimeZoneAnchor(zoneId = "Australia/Sydney", latitude = -33.8667, longitude = 151.2167),
            TimeZoneAnchor(zoneId = "Europe/Amsterdam", latitude = 52.3667, longitude = 4.9),
            TimeZoneAnchor(zoneId = "Europe/Andorra", latitude = 42.5, longitude = 1.5167),
            TimeZoneAnchor(zoneId = "Europe/Astrakhan", latitude = 46.35, longitude = 48.05),
            TimeZoneAnchor(zoneId = "Europe/Athens", latitude = 37.9667, longitude = 23.7167),
            TimeZoneAnchor(zoneId = "Europe/Belgrade", latitude = 44.8333, longitude = 20.5),
            TimeZoneAnchor(zoneId = "Europe/Berlin", latitude = 52.5, longitude = 13.3667),
            TimeZoneAnchor(zoneId = "Europe/Bratislava", latitude = 48.15, longitude = 17.1167),
            TimeZoneAnchor(zoneId = "Europe/Brussels", latitude = 50.8333, longitude = 4.3333),
            TimeZoneAnchor(zoneId = "Europe/Bucharest", latitude = 44.4333, longitude = 26.1),
            TimeZoneAnchor(zoneId = "Europe/Budapest", latitude = 47.5, longitude = 19.0833),
            TimeZoneAnchor(zoneId = "Europe/Busingen", latitude = 47.7, longitude = 8.6833),
            TimeZoneAnchor(zoneId = "Europe/Chisinau", latitude = 47.0, longitude = 28.8333),
            TimeZoneAnchor(zoneId = "Europe/Copenhagen", latitude = 55.6667, longitude = 12.5833),
            TimeZoneAnchor(zoneId = "Europe/Dublin", latitude = 53.3333, longitude = -6.25),
            TimeZoneAnchor(zoneId = "Europe/Gibraltar", latitude = 36.1333, longitude = -5.35),
            TimeZoneAnchor(zoneId = "Europe/Guernsey", latitude = 49.4547, longitude = -2.5361),
            TimeZoneAnchor(zoneId = "Europe/Helsinki", latitude = 60.1667, longitude = 24.9667),
            TimeZoneAnchor(zoneId = "Europe/Isle_of_Man", latitude = 54.15, longitude = -4.4667),
            TimeZoneAnchor(zoneId = "Europe/Istanbul", latitude = 41.0167, longitude = 28.9667),
            TimeZoneAnchor(zoneId = "Europe/Jersey", latitude = 49.1836, longitude = -2.1067),
            TimeZoneAnchor(zoneId = "Europe/Kaliningrad", latitude = 54.7167, longitude = 20.5),
            TimeZoneAnchor(zoneId = "Europe/Kirov", latitude = 58.6, longitude = 49.65),
            TimeZoneAnchor(zoneId = "Europe/Kyiv", latitude = 50.4333, longitude = 30.5167),
            TimeZoneAnchor(zoneId = "Europe/Lisbon", latitude = 38.7167, longitude = -9.1333),
            TimeZoneAnchor(zoneId = "Europe/Ljubljana", latitude = 46.05, longitude = 14.5167),
            TimeZoneAnchor(zoneId = "Europe/London", latitude = 51.5083, longitude = -0.1253),
            TimeZoneAnchor(zoneId = "Europe/Luxembourg", latitude = 49.6, longitude = 6.15),
            TimeZoneAnchor(zoneId = "Europe/Madrid", latitude = 40.4, longitude = -3.6833),
            TimeZoneAnchor(zoneId = "Europe/Malta", latitude = 35.9, longitude = 14.5167),
            TimeZoneAnchor(zoneId = "Europe/Mariehamn", latitude = 60.1, longitude = 19.95),
            TimeZoneAnchor(zoneId = "Europe/Minsk", latitude = 53.9, longitude = 27.5667),
            TimeZoneAnchor(zoneId = "Europe/Monaco", latitude = 43.7, longitude = 7.3833),
            TimeZoneAnchor(zoneId = "Europe/Moscow", latitude = 55.7558, longitude = 37.6178),
            TimeZoneAnchor(zoneId = "Europe/Oslo", latitude = 59.9167, longitude = 10.75),
            TimeZoneAnchor(zoneId = "Europe/Paris", latitude = 48.8667, longitude = 2.3333),
            TimeZoneAnchor(zoneId = "Europe/Podgorica", latitude = 42.4333, longitude = 19.2667),
            TimeZoneAnchor(zoneId = "Europe/Prague", latitude = 50.0833, longitude = 14.4333),
            TimeZoneAnchor(zoneId = "Europe/Riga", latitude = 56.95, longitude = 24.1),
            TimeZoneAnchor(zoneId = "Europe/Rome", latitude = 41.9, longitude = 12.4833),
            TimeZoneAnchor(zoneId = "Europe/Samara", latitude = 53.2, longitude = 50.15),
            TimeZoneAnchor(zoneId = "Europe/San_Marino", latitude = 43.9167, longitude = 12.4667),
            TimeZoneAnchor(zoneId = "Europe/Sarajevo", latitude = 43.8667, longitude = 18.4167),
            TimeZoneAnchor(zoneId = "Europe/Saratov", latitude = 51.5667, longitude = 46.0333),
            TimeZoneAnchor(zoneId = "Europe/Simferopol", latitude = 44.95, longitude = 34.1),
            TimeZoneAnchor(zoneId = "Europe/Skopje", latitude = 41.9833, longitude = 21.4333),
            TimeZoneAnchor(zoneId = "Europe/Sofia", latitude = 42.6833, longitude = 23.3167),
            TimeZoneAnchor(zoneId = "Europe/Stockholm", latitude = 59.3333, longitude = 18.05),
            TimeZoneAnchor(zoneId = "Europe/Tallinn", latitude = 59.4167, longitude = 24.75),
            TimeZoneAnchor(zoneId = "Europe/Tirane", latitude = 41.3333, longitude = 19.8333),
            TimeZoneAnchor(zoneId = "Europe/Ulyanovsk", latitude = 54.3333, longitude = 48.4),
            TimeZoneAnchor(zoneId = "Europe/Vaduz", latitude = 47.15, longitude = 9.5167),
            TimeZoneAnchor(zoneId = "Europe/Vatican", latitude = 41.9022, longitude = 12.4531),
            TimeZoneAnchor(zoneId = "Europe/Vienna", latitude = 48.2167, longitude = 16.3333),
            TimeZoneAnchor(zoneId = "Europe/Vilnius", latitude = 54.6833, longitude = 25.3167),
            TimeZoneAnchor(zoneId = "Europe/Volgograd", latitude = 48.7333, longitude = 44.4167),
            TimeZoneAnchor(zoneId = "Europe/Warsaw", latitude = 52.25, longitude = 21.0),
            TimeZoneAnchor(zoneId = "Europe/Zagreb", latitude = 45.8, longitude = 15.9667),
            TimeZoneAnchor(zoneId = "Europe/Zurich", latitude = 47.3833, longitude = 8.5333),
            TimeZoneAnchor(zoneId = "Indian/Antananarivo", latitude = -18.9167, longitude = 47.5167),
            TimeZoneAnchor(zoneId = "Indian/Chagos", latitude = -7.3333, longitude = 72.4167),
            TimeZoneAnchor(zoneId = "Indian/Christmas", latitude = -10.4167, longitude = 105.7167),
            TimeZoneAnchor(zoneId = "Indian/Cocos", latitude = -12.1667, longitude = 96.9167),
            TimeZoneAnchor(zoneId = "Indian/Comoro", latitude = -11.6833, longitude = 43.2667),
            TimeZoneAnchor(zoneId = "Indian/Kerguelen", latitude = -49.3528, longitude = 70.2175),
            TimeZoneAnchor(zoneId = "Indian/Mahe", latitude = -4.6667, longitude = 55.4667),
            TimeZoneAnchor(zoneId = "Indian/Maldives", latitude = 4.1667, longitude = 73.5),
            TimeZoneAnchor(zoneId = "Indian/Mauritius", latitude = -20.1667, longitude = 57.5),
            TimeZoneAnchor(zoneId = "Indian/Mayotte", latitude = -12.7833, longitude = 45.2333),
            TimeZoneAnchor(zoneId = "Indian/Reunion", latitude = -20.8667, longitude = 55.4667),
            TimeZoneAnchor(zoneId = "Pacific/Apia", latitude = -13.8333, longitude = -171.7333),
            TimeZoneAnchor(zoneId = "Pacific/Auckland", latitude = -36.8667, longitude = 174.7667),
            TimeZoneAnchor(zoneId = "Pacific/Bougainville", latitude = -6.2167, longitude = 155.5667),
            TimeZoneAnchor(zoneId = "Pacific/Chatham", latitude = -43.95, longitude = -176.55),
            TimeZoneAnchor(zoneId = "Pacific/Chuuk", latitude = 7.4167, longitude = 151.7833),
            TimeZoneAnchor(zoneId = "Pacific/Easter", latitude = -27.15, longitude = -109.4333),
            TimeZoneAnchor(zoneId = "Pacific/Efate", latitude = -17.6667, longitude = 168.4167),
            TimeZoneAnchor(zoneId = "Pacific/Fakaofo", latitude = -9.3667, longitude = -171.2333),
            TimeZoneAnchor(zoneId = "Pacific/Fiji", latitude = -18.1333, longitude = 178.4167),
            TimeZoneAnchor(zoneId = "Pacific/Funafuti", latitude = -8.5167, longitude = 179.2167),
            TimeZoneAnchor(zoneId = "Pacific/Galapagos", latitude = -0.9, longitude = -89.6),
            TimeZoneAnchor(zoneId = "Pacific/Gambier", latitude = -23.1333, longitude = -134.95),
            TimeZoneAnchor(zoneId = "Pacific/Guadalcanal", latitude = -9.5333, longitude = 160.2),
            TimeZoneAnchor(zoneId = "Pacific/Guam", latitude = 13.4667, longitude = 144.75),
            TimeZoneAnchor(zoneId = "Pacific/Honolulu", latitude = 21.3069, longitude = -157.8583),
            TimeZoneAnchor(zoneId = "Pacific/Kanton", latitude = -2.7833, longitude = -171.7167),
            TimeZoneAnchor(zoneId = "Pacific/Kiritimati", latitude = 1.8667, longitude = -157.3333),
            TimeZoneAnchor(zoneId = "Pacific/Kosrae", latitude = 5.3167, longitude = 162.9833),
            TimeZoneAnchor(zoneId = "Pacific/Kwajalein", latitude = 9.0833, longitude = 167.3333),
            TimeZoneAnchor(zoneId = "Pacific/Majuro", latitude = 7.15, longitude = 171.2),
            TimeZoneAnchor(zoneId = "Pacific/Marquesas", latitude = -9.0, longitude = -139.5),
            TimeZoneAnchor(zoneId = "Pacific/Midway", latitude = 28.2167, longitude = -177.3667),
            TimeZoneAnchor(zoneId = "Pacific/Nauru", latitude = -0.5167, longitude = 166.9167),
            TimeZoneAnchor(zoneId = "Pacific/Niue", latitude = -19.0167, longitude = -169.9167),
            TimeZoneAnchor(zoneId = "Pacific/Norfolk", latitude = -29.05, longitude = 167.9667),
            TimeZoneAnchor(zoneId = "Pacific/Noumea", latitude = -22.2667, longitude = 166.45),
            TimeZoneAnchor(zoneId = "Pacific/Pago_Pago", latitude = -14.2667, longitude = -170.7),
            TimeZoneAnchor(zoneId = "Pacific/Palau", latitude = 7.3333, longitude = 134.4833),
            TimeZoneAnchor(zoneId = "Pacific/Pitcairn", latitude = -25.0667, longitude = -130.0833),
            TimeZoneAnchor(zoneId = "Pacific/Pohnpei", latitude = 6.9667, longitude = 158.2167),
            TimeZoneAnchor(zoneId = "Pacific/Port_Moresby", latitude = -9.5, longitude = 147.1667),
            TimeZoneAnchor(zoneId = "Pacific/Rarotonga", latitude = -21.2333, longitude = -159.7667),
            TimeZoneAnchor(zoneId = "Pacific/Saipan", latitude = 15.2, longitude = 145.75),
            TimeZoneAnchor(zoneId = "Pacific/Tahiti", latitude = -17.5333, longitude = -149.5667),
            TimeZoneAnchor(zoneId = "Pacific/Tarawa", latitude = 1.4167, longitude = 173.0),
            TimeZoneAnchor(zoneId = "Pacific/Tongatapu", latitude = -21.1333, longitude = -175.2),
            TimeZoneAnchor(zoneId = "Pacific/Wake", latitude = 19.2833, longitude = 166.6167),
            TimeZoneAnchor(zoneId = "Pacific/Wallis", latitude = -13.3, longitude = -176.1667),
        )

    val AVAILABLE_ZONE_IDS: List<String> = CANONICAL_ANCHORS.map { it.zoneId }.sorted()

    // Bundled identifiers an older supported device tzdb does not define, mapped to the identifier
    // that held the same region before the split or rename. See resolveZone for why this exists.
    private val ZONE_ALIASES: Map<String, String> =
        mapOf(
            // Renamed from Europe/Kiev in tzdata 2022b.
            "Europe/Kyiv" to "Europe/Kiev",
            // Renamed from America/Godthab in tzdata 2020a.
            "America/Nuuk" to "America/Godthab",
            // Renamed from Pacific/Enderbury in tzdata 2021b.
            "Pacific/Kanton" to "Pacific/Enderbury",
            // Split out of America/Ojinaga in tzdata 2022g.
            "America/Ciudad_Juarez" to "America/Ojinaga",
            // Split out of America/Santiago in tzdata 2025b.
            "America/Coyhaique" to "America/Santiago",
            // Split out of Asia/Qyzylorda in tzdata 2018h. The target is Asia/Almaty rather than
            // the parent zone because the wallpaper renders the present instant: a tzdb old enough
            // to need this entry reports both Almaty and Qyzylorda at UTC+06.
            "Asia/Qostanay" to "Asia/Almaty",
        )

    /**
     * Resolves [id] against the device tzdb, substituting the identifier that held the same region
     * before the zone's split or rename. Returns null rather than throwing `ZoneRulesException`
     * when the device knows neither identifier.
     *
     * The outcome is reported in exactly one log line, because a substitution and a miss are
     * different diagnoses and must not share wording: a caller that acts on the result can tell
     * that a device degraded, and a genuine miss still reads as a failure.
     *
     * The anchor list follows current tzdb releases, while the API 26 floor can carry a tzdb as
     * old as 2017a, whose `ZoneRulesProvider` rejects six of the bundled identifiers; every alias
     * target is defined in tzdata 2017a. An alias therefore differs from the current zone only
     * where tzdb itself has moved the region since 2017a. Maintain `ZONE_ALIASES` whenever an
     * anchor is added from a release newer than the oldest tzdb the minimum SDK can carry.
     *
     * [zoneOf] is the injection seam for the device's `ZoneRulesProvider`, mirroring
     * `LocationStore`'s `deviceZone`.
     */
    fun resolveZone(id: String, zoneOf: (String) -> ZoneId = ZoneId::of): ZoneId? {
        val resolved = resolveWithoutLogging(id = id, zoneOf = zoneOf)
        if (resolved == null) {
            Log.w(TAG, "device tzdb has no timezone $id and no predecessor identifier for it")
        } else if (resolved.id != id) {
            Log.w(TAG, "device tzdb has no $id; substituting its predecessor ${resolved.id}")
        }
        return resolved
    }

    // Resolution with no logging, for lookup: it searches anchors nearest-first and takes the first
    // that resolves, so a rejected identifier is an expected step of that search rather than a
    // failure, and reporting it would repeat on every keystroke of manual coordinate entry.
    private fun resolveWithoutLogging(id: String, zoneOf: (String) -> ZoneId): ZoneId? {
        val alias = ZONE_ALIASES[id]
        return attemptZone(id = id, zoneOf = zoneOf)
            ?: alias?.let { attemptZone(id = it, zoneOf = zoneOf) }
    }

    private fun attemptZone(id: String, zoneOf: (String) -> ZoneId): ZoneId? {
        // A block body rather than `= try`: the formatter's function-expression-body rule would
        // rewrite the latter back to a single-line form that detekt's MultilineExpressionWrapping
        // rejects, and neither rule is disabled for this project.
        val resolved =
            try {
                zoneOf(id)
            } catch (_: DateTimeException) {
                null
            }
        return resolved
    }

    /** The bundled identifiers this device can resolve, so no dead entry reaches the picker. */
    fun resolvableZoneIds(zoneOf: (String) -> ZoneId = ZoneId::of): List<String> =
        AVAILABLE_ZONE_IDS.filter { resolveZone(id = it, zoneOf = zoneOf) != null }

    /**
     * The picker's entries: every bundled identifier this device can resolve, plus [currentZone].
     *
     * [currentZone] can be a valid zone that has no anchor, such as `UTC`, `Etc/GMT+2`, or a bare
     * offset inherited from a migrated record. Including it makes the dialog show and highlight
     * the zone actually in effect instead of the alphabetically first entry, so inspecting or
     * correcting the saved zone does not start from a misleading selection.
     */
    fun pickerZoneIds(currentZone: ZoneId, zoneOf: (String) -> ZoneId = ZoneId::of): List<String> =
        (resolvableZoneIds(zoneOf = zoneOf) + currentZone.id).distinct().sorted()

    /**
     * The entries in [ids] that match [query], ignoring case and the `/`, `_`, and space separators,
     * so typing "new york" reaches `America/New_York`. Signs stay significant, so "gmt+2" does not
     * match `Etc/GMT-2`. A blank [query] returns [ids] unchanged rather than an empty list.
     */
    fun filterZoneIds(ids: List<String>, query: String): List<String> {
        val key = query.zoneFilterKey()
        return if (key.isEmpty()) ids else ids.filter { it.zoneFilterKey().contains(key) }
    }

    // Case-folded and separator-free, so the filter is forgiving about how a zone is typed.
    private fun String.zoneFilterKey(): String =
        lowercase(Locale.ROOT).filterNot { it == '/' || it == '_' || it.isWhitespace() }

    /**
     * The nearest bundled anchor this device can resolve, or UTC when it can resolve none.
     *
     * Anchors are visited in increasing distance so that a device missing the nearest anchor
     * falls through to the next-nearest one it does know, rather than failing the whole lookup.
     */
    fun lookup(latitude: Double, longitude: Double, zoneOf: (String) -> ZoneId = ZoneId::of): ZoneId {
        val cosLat = cos(Math.toRadians(latitude))
        val squareDegreesTo = { anchor: TimeZoneAnchor ->
            val dLat = latitude - anchor.latitude
            val dLon = normalizeLongitudeDiff(longitude - anchor.longitude) * cosLat
            dLat * dLat + dLon * dLon
        }
        val resolvable =
            CANONICAL_ANCHORS
                .sortedBy(squareDegreesTo)
                .firstNotNullOfOrNull { anchor -> resolveWithoutLogging(id = anchor.zoneId, zoneOf = zoneOf) }
        return resolvable ?: ZoneId.of(UTC_ZONE_ID)
    }

    private fun normalizeLongitudeDiff(diff: Double): Double {
        var d = (diff + HALF_TURN_DEGREES) % FULL_TURN_DEGREES
        if (d < 0.0) d += FULL_TURN_DEGREES
        return d - HALF_TURN_DEGREES
    }
}
