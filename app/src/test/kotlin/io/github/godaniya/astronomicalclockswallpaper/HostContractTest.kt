package io.github.godaniya.astronomicalclockswallpaper

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Verifies that `scripts/host_contract.json` matches the authoritative Kotlin constants,
 * build configuration, and AndroidManifest declarations.
 *
 * When invoked via `./gradlew :app:exportHostContract`, writes the canonical JSON contract
 * before verifying it.
 */
class HostContractTest {
    @Test
    fun contractMatchesKotlinSources() {
        val contractFile = resolveRepoFile("scripts/host_contract.json")
        val buildGradleText = resolveRepoFile("app/build.gradle.kts").readText()
        val manifestText = resolveRepoFile("app/src/main/AndroidManifest.xml").readText()

        assertTrue(
            "app/build.gradle.kts must declare base applicationId $BASE_APPLICATION_ID",
            buildGradleText.contains("""applicationId = "$BASE_APPLICATION_ID""""),
        )
        assertTrue(
            "app/build.gradle.kts must declare debug applicationIdSuffix $DEBUG_SUFFIX",
            buildGradleText.contains("""applicationIdSuffix = "$DEBUG_SUFFIX""""),
        )

        val serviceClassName = AstronomicalClocksWallpaperService::class.java.name
        val serviceShortName = "." + AstronomicalClocksWallpaperService::class.java.simpleName
        assertTrue(
            "AndroidManifest.xml must declare wallpaper service $serviceShortName",
            manifestText.contains("""android:name="$serviceShortName""""),
        )

        val expectedJson = formatCanonicalJson(buildContractMap(serviceClassName)) + "\n"
        if (System.getProperty(EXPORT_PROPERTY) == "true") {
            contractFile.writeText(expectedJson)
        }
        assertTrue(
            "scripts/host_contract.json is missing; run ./gradlew :app:exportHostContract",
            contractFile.isFile,
        )
        assertEquals(
            "scripts/host_contract.json drifted from Kotlin constants; run ./gradlew :app:exportHostContract",
            expectedJson,
            contractFile.readText(),
        )
    }

    private fun buildContractMap(serviceClassName: String): Map<String, Any> {
        val debugPackageName = BASE_APPLICATION_ID + DEBUG_SUFFIX
        return sortedMapOf(
            "debug_action" to AstronomicalClocksWallpaperService.ACTION_DEBUG_SET_TIME,
            "debug_extras" to
                sortedMapOf(
                    "diagnostics" to AstronomicalClocksWallpaperService.EXTRA_DIAGNOSTICS,
                    "instant" to AstronomicalClocksWallpaperService.EXTRA_INSTANT,
                    "offset_hours" to AstronomicalClocksWallpaperService.EXTRA_OFFSET_HOURS,
                    "offset_millis" to AstronomicalClocksWallpaperService.EXTRA_OFFSET_MILLIS,
                    "offset_minutes" to AstronomicalClocksWallpaperService.EXTRA_OFFSET_MINUTES,
                    "offset_seconds" to AstronomicalClocksWallpaperService.EXTRA_OFFSET_SECONDS,
                    "reset" to AstronomicalClocksWallpaperService.EXTRA_RESET,
                ),
            "debug_package_name" to debugPackageName,
            "display_settings" to
                sortedMapOf(
                    "max_brightness" to DialDisplaySettings.BRIGHTNESS_RANGE.last,
                    "min_brightness" to DialDisplaySettings.BRIGHTNESS_RANGE.first,
                ),
            "geometry" to
                sortedMapOf(
                    "civil_midnight_angle_deg" to CivilDialConstants.MIDNIGHT_ANGLE_DEG,
                    "civil_seconds_per_degree" to floatToDecimalDouble(CivilDialConstants.SECONDS_PER_DEGREE),
                    "outer_radius" to floatToDecimalDouble(DialRenderer.OUTER_RADIUS),
                    "radius_fraction" to floatToDecimalDouble(DialViewport.RADIUS_FRACTION),
                    "rim_inset" to floatToDecimalDouble(DialRenderer.RIM_INSET),
                    "rim_width" to floatToDecimalDouble(DialRenderer.RIM_WIDTH),
                ),
            "location_store" to buildLocationStoreMap(),
            "logs" to
                sortedMapOf(
                    "debug_clock_fixed_instant_prefix" to
                        AstronomicalClocksWallpaperService.DEBUG_CLOCK_FIXED_INSTANT_PREFIX,
                    "debug_clock_offset_prefix" to AstronomicalClocksWallpaperService.DEBUG_CLOCK_OFFSET_PREFIX,
                    "debug_clock_offset_suffix" to AstronomicalClocksWallpaperService.DEBUG_CLOCK_OFFSET_SUFFIX,
                    "debug_clock_reset_message" to AstronomicalClocksWallpaperService.DEBUG_CLOCK_RESET_MESSAGE,
                    "dial_layout_prefix" to AstronomicalClocksWallpaperService.DIAL_LAYOUT_PREFIX,
                    "display_tag" to AstronomicalClocksWallpaperService.DISPLAY_LOG_TAG,
                    "renderer_tag" to DialRenderer.TAG,
                    "service_tag" to AstronomicalClocksWallpaperService.TAG,
                ),
            "palette" to
                sortedMapOf(
                    "dark_gold_rgb" to argbToRgbList(DialStyle.GOLD),
                    "dark_hand_rgb" to argbToRgbList(DialStyle.HAND),
                    "dark_rim_rgb" to argbToRgbList(DialStyle.RIM),
                    "light_hand_rgb" to argbToRgbList(DialStyle.LIGHT_HAND),
                    "light_rim_rgb" to argbToRgbList(DialStyle.LIGHT_RIM),
                ),
            "service_class_name" to serviceClassName,
            "service_name" to "$debugPackageName/$serviceClassName",
        )
    }

    private fun buildLocationStoreMap(): Map<String, Any> {
        val sortedSources =
            ObservingLocation.Source.entries
                .map { it.name }
                .sorted()
        return sortedMapOf(
            "fields" to
                sortedMapOf(
                    "latitude" to LocationStore.KEY_LATITUDE,
                    "longitude" to LocationStore.KEY_LONGITUDE,
                    "source" to LocationStore.KEY_SOURCE,
                    "version" to LocationStore.KEY_VERSION,
                    "zone_id" to LocationStore.KEY_ZONE_ID,
                ),
            "max_latitude" to ObservingLocation.MAX_LATITUDE,
            "max_longitude" to ObservingLocation.MAX_LONGITUDE,
            "preferences_name" to LocationStore.PREFERENCES_NAME,
            "record_key" to LocationStore.KEY_RECORD,
            "record_version" to LocationStore.RECORD_VERSION,
            "sources" to sortedSources,
        )
    }

    private fun resolveRepoFile(relativePath: String): File {
        val fromModuleDir = File("..", relativePath)
        return if (fromModuleDir.exists() || File("../settings.gradle.kts").isFile) {
            fromModuleDir
        } else {
            File(relativePath)
        }
    }

    private fun floatToDecimalDouble(value: Float): Double = value.toString().toDouble()

    private fun argbToRgbList(argb: Int): List<Int> {
        val red = argb ushr RED_SHIFT and CHANNEL_MASK
        val green = argb ushr GREEN_SHIFT and CHANNEL_MASK
        val blue = argb and CHANNEL_MASK
        return listOf(red, green, blue)
    }

    private fun formatCanonicalJson(value: Any, indent: String = ""): String {
        val childIndent = indent + INDENT_STEP
        return when (value) {
            is Map<*, *> -> {
                val entries =
                    value.entries
                        .sortedBy { requireNotNull(it.key).toString() }
                        .joinToString(",\n") { (key, entryValue) ->
                            val formattedKey = quoteJsonString(requireNotNull(key).toString())
                            val formattedVal =
                                formatCanonicalJson(
                                    value = requireNotNull(entryValue),
                                    indent = childIndent,
                                )
                            "$childIndent$formattedKey: $formattedVal"
                        }
                "{\n$entries\n$indent}"
            }

            is List<*> -> {
                if (value.all { it is String || it is Number || it is Boolean }) {
                    val items =
                        value.joinToString(separator = ", ") { item ->
                            formatCanonicalJson(value = requireNotNull(item))
                        }
                    "[$items]"
                } else {
                    val items =
                        value.joinToString(",\n") { item ->
                            val formattedItem =
                                formatCanonicalJson(
                                    value = requireNotNull(item),
                                    indent = childIndent,
                                )
                            "$childIndent$formattedItem"
                        }
                    "[\n$items\n$indent]"
                }
            }

            is String -> {
                quoteJsonString(value)
            }

            is Number, is Boolean -> {
                value.toString()
            }

            else -> {
                error("Unsupported contract JSON value: $value")
            }
        }
    }

    private fun quoteJsonString(raw: String): String {
        val escaped =
            raw
                .replace(oldValue = "\\", newValue = "\\\\")
                .replace(oldValue = "\"", newValue = "\\\"")
                .replace(oldValue = "\n", newValue = "\\n")
                .replace(oldValue = "\r", newValue = "\\r")
                .replace(oldValue = "\t", newValue = "\\t")
        return "\"$escaped\""
    }

    private companion object {
        const val BASE_APPLICATION_ID = "io.github.godaniya.astronomicalclockswallpaper"
        const val DEBUG_SUFFIX = ".debug"
        const val EXPORT_PROPERTY = "exportHostContract"
        const val INDENT_STEP = "  "
        const val RED_SHIFT = 16
        const val GREEN_SHIFT = 8
        const val CHANNEL_MASK = 0xFF
    }
}
