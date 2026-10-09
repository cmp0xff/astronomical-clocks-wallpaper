package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Build
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset

/**
 * Generates representative Orloj dial Canvas PNGs for visual inspection.
 *
 * This harness is decoupled from the automated unit suite and invoked explicitly via
 * `./gradlew exportRepresentativeImages`. It does not make synthetic assertions on image encoding.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class OrlojRepresentativeExport {
    @Test
    fun exportRepresentativeImages() {
        REPORT_DIRECTORY.mkdirs()
        val sites =
            listOf(
                "prague" to site(latitude = 50.08, longitude = 14.42, zoneId = PRAGUE),
                "sydney" to site(latitude = -33.87, longitude = 151.21, zoneId = SYDNEY),
                "equator" to site(latitude = 0.0, longitude = 0.0, zoneId = ZoneOffset.UTC),
                "north-pole" to site(latitude = 90.0, longitude = 0.0, zoneId = ZoneOffset.UTC),
                "south-pole" to site(latitude = -90.0, longitude = 0.0, zoneId = ZoneOffset.UTC),
            )
        val calculator = AstronomyEngineCalculator()
        for ((appearanceName, palette) in APPEARANCES) {
            for ((name, site) in sites) {
                exportSite(
                    calculator = calculator,
                    site = site,
                    name = name,
                    appearanceName = appearanceName,
                    palette = palette,
                )
            }
            exportNoLocation(appearanceName, palette)
        }
        exportPraguePlus30m(calculator)
        val sitesByName = sites.toMap()
        exportEquinoxOrientations(calculator, EQUINOX_SITE_NAMES.map { name -> name to sitesByName.getValue(name) })
        exportDisplayControls(calculator)
    }

    private fun exportDisplayControls(calculator: AstronomyCalculator) {
        val prague = site(latitude = 50.08, longitude = 14.42, zoneId = PRAGUE)
        val geometry = calculator.dialGeometry(EXPORT_INSTANT, prague)
        val controls =
            listOf(
                "small-top-left" to DialDisplaySettings(size = 50, horizontal = 0, vertical = 0),
                "small-bottom-right" to DialDisplaySettings(size = 50, horizontal = 100, vertical = 100),
                "large-top-right" to DialDisplaySettings(size = 115, horizontal = 100, vertical = 0),
                "large-bottom-left-dim" to
                    DialDisplaySettings(size = 115, horizontal = 0, vertical = 100, brightness = 80),
            )
        for ((appearance, palette) in APPEARANCES) {
            for ((name, display) in controls) {
                val bitmap = Bitmap.createBitmap(IMAGE_WIDTH, IMAGE_HEIGHT, Bitmap.Config.ARGB_8888)
                DialRenderer().renderDisplay(
                    canvas = Canvas(bitmap),
                    state = clockState(EXPORT_INSTANT.atZone(PRAGUE).toLocalTime()),
                    geometry = geometry,
                    layers = DialLayers(),
                    style =
                        DialRenderStyle(
                            palette = palette,
                            display = display,
                            viewport =
                                DialViewport
                                    .full(width = IMAGE_WIDTH, height = IMAGE_HEIGHT),
                        ),
                )
                savePng(bitmap, "display-$name-$appearance-api${Build.VERSION.SDK_INT}.png")
                bitmap.recycle()
            }
        }
    }

    private fun exportSite(
        calculator: AstronomyEngineCalculator,
        site: ObservingLocation,
        name: String,
        appearanceName: String,
        palette: DialPalette,
    ) {
        val geometry = calculator.dialGeometry(EXPORT_INSTANT, site)
        for (isRingEnabled in listOf(true, false)) {
            val bitmap = Bitmap.createBitmap(IMAGE_WIDTH, IMAGE_HEIGHT, Bitmap.Config.ARGB_8888)
            DialRenderer().renderDial(
                canvas = Canvas(bitmap),
                state = clockState(EXPORT_INSTANT.atZone(site.zoneId).toLocalTime()),
                geometry = geometry,
                layers = DialLayers(isZodiacRingEnabled = isRingEnabled),
                palette = palette,
            )
            val ringSuffix = if (isRingEnabled) "" else "-no-ring"
            savePng(bitmap, "$name-$appearanceName$ringSuffix-api${Build.VERSION.SDK_INT}.png")
            if (appearanceName == "dark" && isRingEnabled) {
                // Preserve the names of the pre-appearance dark exports.
                savePng(bitmap, "$name-api${Build.VERSION.SDK_INT}.png")
            }
        }
    }

    // Before a site is selected the dial is drawn without site geometry, so export that state for
    // each theme as well.
    private fun exportNoLocation(appearanceName: String, palette: DialPalette) {
        val bitmap = Bitmap.createBitmap(IMAGE_WIDTH, IMAGE_HEIGHT, Bitmap.Config.ARGB_8888)
        DialRenderer().renderDial(
            canvas = Canvas(bitmap),
            state = clockState(EXPORT_INSTANT.atZone(ZoneOffset.UTC).toLocalTime()),
            geometry = null,
            palette = palette,
        )
        savePng(bitmap, "no-location-$appearanceName-api${Build.VERSION.SDK_INT}.png")
    }

    private fun exportPraguePlus30m(calculator: AstronomyEngineCalculator) {
        val pragueSite = site(latitude = 50.08, longitude = 14.42, zoneId = PRAGUE)
        val exportInstantPlus30m = EXPORT_INSTANT.plus(Duration.ofMinutes(THIRTY_MINUTES))
        val bitmap = Bitmap.createBitmap(IMAGE_WIDTH, IMAGE_HEIGHT, Bitmap.Config.ARGB_8888)
        DialRenderer().renderDial(
            canvas = Canvas(bitmap),
            state = clockState(exportInstantPlus30m.atZone(pragueSite.zoneId).toLocalTime()),
            geometry = calculator.dialGeometry(exportInstantPlus30m, pragueSite),
        )
        savePng(bitmap, "prague-plus-30m-api${Build.VERSION.SDK_INT}.png")
    }

    private fun exportEquinoxOrientations(
        calculator: AstronomyEngineCalculator,
        sites: List<Pair<String, ObservingLocation>>,
    ) {
        for ((name, site) in sites) {
            for (angle in listOf(0, 90, 180, 270)) {
                val geometry =
                    calculator
                        .dialGeometry(EXPORT_INSTANT, site)
                        .copy(localSiderealAngleDeg = angle.toDouble())
                val bitmap = Bitmap.createBitmap(IMAGE_WIDTH, IMAGE_HEIGHT, Bitmap.Config.ARGB_8888)
                DialRenderer().renderDial(
                    canvas = Canvas(bitmap),
                    state = clockState(EXPORT_INSTANT.atZone(site.zoneId).toLocalTime()),
                    geometry = geometry,
                )
                val stem = "$name-equinox-$angle-api${Build.VERSION.SDK_INT}"
                savePng(bitmap, "$stem.png")
                savePng(EquinoxStarDetail.render(geometry, STAR_DETAIL_SIZE), "$stem-detail.png")
            }
        }
    }

    private fun savePng(bitmap: Bitmap, fileName: String) {
        File(REPORT_DIRECTORY, fileName).outputStream().use { output ->
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)
        }
    }

    private fun site(latitude: Double, longitude: Double, zoneId: ZoneId): ObservingLocation =
        MANUAL_LOCATION.copy(latitude = latitude, longitude = longitude, zoneId = zoneId)

    private companion object {
        const val IMAGE_WIDTH = 1080
        const val IMAGE_HEIGHT = 1600
        const val STAR_DETAIL_SIZE = 320
        const val THIRTY_MINUTES = 30L
        val REPORT_DIRECTORY = File("build/reports/orloj")
        val EXPORT_INSTANT: Instant = Instant.parse("2026-10-04T15:15:36Z")

        // Named rather than positional: the exported file stems assert which sites these are, so a
        // renamed entry fails here, and a reordered one can no longer silently export another site.
        val EQUINOX_SITE_NAMES = listOf("prague", "sydney")

        val APPEARANCES =
            listOf(
                "dark" to DialStyle.DARK_PALETTE,
                "light" to DialStyle.LIGHT_PALETTE,
            )

        // Each site states its own zone rather than inheriting another site's. The equator and
        // polar rows are neutral reference points with no civil zone of their own, so they state
        // UTC outright; every meridian meets at the poles, so longitude 0 is a stated choice for
        // them and not a borrowed Prague meridian.
        val MANUAL_LOCATION =
            ObservingLocation(
                latitude = 0.0,
                longitude = 0.0,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneOffset.UTC,
            )
        val PRAGUE: ZoneId = ZoneId.of("Europe/Prague")
        val SYDNEY: ZoneId = ZoneId.of("Australia/Sydney")
    }
}
