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
        val directory = File("build/reports/orloj")
        if (!directory.exists()) {
            directory.mkdirs()
        }
        val sites =
            listOf(
                "prague" to site(latitude = 50.08, longitude = 14.42, zoneId = PRAGUE),
                "sydney" to site(latitude = -33.87, longitude = 151.21, zoneId = SYDNEY),
                "equator" to site(latitude = 0.0, longitude = 0.0, zoneId = ZoneOffset.UTC),
                "north-pole" to site(latitude = 90.0, longitude = 0.0, zoneId = ZoneOffset.UTC),
                "south-pole" to site(latitude = -90.0, longitude = 0.0, zoneId = ZoneOffset.UTC),
            )
        val calculator = AstronomyEngineCalculator()
        val exportInstant = Instant.parse("2026-10-04T15:15:36Z")
        for ((name, site) in sites) {
            val bitmap = Bitmap.createBitmap(IMAGE_WIDTH, IMAGE_HEIGHT, Bitmap.Config.ARGB_8888)
            DialRenderer().renderDial(
                canvas = Canvas(bitmap),
                state = clockState(exportInstant.atZone(site.zoneId).toLocalTime()),
                geometry = calculator.dialGeometry(exportInstant, site),
            )
            File(directory, "$name-api${Build.VERSION.SDK_INT}.png").outputStream().use { output ->
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)
            }
        }

        val pragueSite = site(latitude = 50.08, longitude = 14.42, zoneId = PRAGUE)
        val exportInstantPlus30m = exportInstant.plus(Duration.ofMinutes(THIRTY_MINUTES))
        val bitmap30m = Bitmap.createBitmap(IMAGE_WIDTH, IMAGE_HEIGHT, Bitmap.Config.ARGB_8888)
        DialRenderer().renderDial(
            canvas = Canvas(bitmap30m),
            state = clockState(exportInstantPlus30m.atZone(pragueSite.zoneId).toLocalTime()),
            geometry = calculator.dialGeometry(exportInstantPlus30m, pragueSite),
        )
        File(directory, "prague-plus-30m-api${Build.VERSION.SDK_INT}.png").outputStream().use { output ->
            bitmap30m.compress(Bitmap.CompressFormat.PNG, 100, output)
        }
    }

    private fun site(latitude: Double, longitude: Double, zoneId: ZoneId): ObservingLocation =
        MANUAL_LOCATION.copy(latitude = latitude, longitude = longitude, zoneId = zoneId)

    private companion object {
        const val IMAGE_WIDTH = 1080
        const val IMAGE_HEIGHT = 1600
        const val THIRTY_MINUTES = 30L

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
