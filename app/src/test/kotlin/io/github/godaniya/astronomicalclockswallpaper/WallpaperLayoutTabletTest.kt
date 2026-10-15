package io.github.godaniya.astronomicalclockswallpaper

import android.content.pm.ApplicationInfo
import android.os.Looper
import android.os.SystemClock
import android.service.wallpaper.WallpaperService
import android.view.SurfaceView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog
import org.robolectric.shadows.ShadowLooper
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset

/**
 * Verifies dial rendering, proportional scaling, zero clipping, and dynamic surface
 * recreation for tablet and foldable aspect ratios and resolutions (#86).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class WallpaperLayoutTabletTest {
    @Test
    @Config(qualifiers = "sw1600dp-w2560dp-h1600dp-land-mdpi")
    fun landscapeTabletDialFits() {
        assertDialFitsWithinDimensions(
            width = TabletSurfaces.LANDSCAPE_16_10.width,
            height = TabletSurfaces.LANDSCAPE_16_10.height,
        )
    }

    @Test
    @Config(qualifiers = "sw1536dp-w2048dp-h1536dp-land-mdpi")
    fun fourToThreeTabletDialFits() {
        assertDialFitsWithinDimensions(
            width = TabletSurfaces.LANDSCAPE_4_3.width,
            height = TabletSurfaces.LANDSCAPE_4_3.height,
        )
    }

    @Test
    @Config(qualifiers = "sw1600dp-w1600dp-h2560dp-port-mdpi")
    fun portraitTabletDialFits() {
        assertDialFitsWithinDimensions(
            width = TabletSurfaces.PORTRAIT_10_16.width,
            height = TabletSurfaces.PORTRAIT_10_16.height,
        )
    }

    @Test
    @Config(qualifiers = "sw1768dp-w2208dp-h1768dp-mdpi")
    fun foldableUnfoldedDialFits() {
        assertDialFitsWithinDimensions(
            width = TabletSurfaces.FOLDABLE_UNFOLDED.width,
            height = TabletSurfaces.FOLDABLE_UNFOLDED.height,
        )
    }

    @Test
    fun dynamicFoldTransition() {
        RuntimeEnvironment.setQualifiers("sw1080dp-w1080dp-h2400dp-port-mdpi")
        val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java).create()
        val service = controller.get()
        service.applicationInfo.flags = service.applicationInfo.flags or ApplicationInfo.FLAG_DEBUGGABLE

        LocationStore(service) { ZoneOffset.UTC }.save(PRAGUE_LOCATION)

        var lastViewport: DialViewport? = null
        var lastGeometry: DialGeometry? = null
        var frameCount = 0
        val expectedGeometry =
            AstronomyEngineCalculator().dialGeometry(
                time = TEST_INSTANT,
                location = PRAGUE_LOCATION,
            )

        val (holder, engine) =
            createFoldableTestHarness(service) { geometry, usable ->
                frameCount++
                lastViewport = usable
                lastGeometry = geometry
            }
        val looper = shadowOf(Looper.getMainLooper())

        try {
            assertFoldedPhase(
                engine = engine,
                holder = holder,
                looper = looper,
                frameCount = { frameCount },
            )
            assertEquals(
                "Observing site geometry must match calculated astronomical frame",
                expectedGeometry,
                lastGeometry,
            )

            assertUnfoldedPhase(
                engine = engine,
                holder = holder,
                looper = looper,
                frameCount = { frameCount },
                viewport = { lastViewport },
            )
            assertEquals(
                "Observing site geometry must persist across surface resize",
                expectedGeometry,
                lastGeometry,
            )

            assertFoldRecovery(
                engine = engine,
                holder = holder,
                looper = looper,
                frameCount = { frameCount },
                viewport = { lastViewport },
            )
        } finally {
            engine.onDestroy()
            holder.release()
            controller.destroy()
        }
    }

    private fun createFoldableTestHarness(
        service: AstronomicalClocksWallpaperService,
        onFrame: (DialGeometry?, DialViewport?) -> Unit,
    ): Pair<ReadyFrameHolder, WallpaperService.Engine> {
        val holder =
            ReadyFrameHolder(
                delegate = SurfaceView(service).holder,
                width = maxOf(a = TabletSurfaces.FOLDABLE_UNFOLDED.width, b = TabletSurfaces.FOLDABLE_FOLDED.width),
                height = maxOf(a = TabletSurfaces.FOLDABLE_UNFOLDED.height, b = TabletSurfaces.FOLDABLE_FOLDED.height),
            )
        val engine =
            service.createEngine(
                draw = { _, _, geometry, _, _, _, usable -> onFrame(geometry, usable) },
                holder = holder,
                clock = Clock.fixed(TEST_INSTANT, ZoneOffset.UTC),
            )
        return holder to engine
    }

    private fun assertFoldedPhase(
        engine: WallpaperService.Engine,
        holder: ReadyFrameHolder,
        looper: ShadowLooper,
        frameCount: () -> Int,
    ) {
        engine.onSurfaceChanged(
            holder,
            0,
            TabletSurfaces.FOLDABLE_FOLDED.width,
            TabletSurfaces.FOLDABLE_FOLDED.height,
        )
        engine.onVisibilityChanged(true)
        assertEquals(1, frameCount())
        assertTicksOncePerSecond(looper = looper, frameCount = frameCount)
    }

    private fun assertUnfoldedPhase(
        engine: WallpaperService.Engine,
        holder: ReadyFrameHolder,
        looper: ShadowLooper,
        frameCount: () -> Int,
        viewport: () -> DialViewport?,
    ) {
        RuntimeEnvironment.setQualifiers("sw1768dp-w2208dp-h1768dp-mdpi")
        engine.onSurfaceChanged(
            holder,
            0,
            TabletSurfaces.FOLDABLE_UNFOLDED.width,
            TabletSurfaces.FOLDABLE_UNFOLDED.height,
        )

        assertEquals(4, frameCount())
        assertTicksOncePerSecond(looper = looper, frameCount = frameCount)

        val unfoldedViewport = requireNotNull(viewport())
        assertEquals(TabletSurfaces.FOLDABLE_UNFOLDED.width.toFloat(), unfoldedViewport.right, TOLERANCE)
        assertEquals(TabletSurfaces.FOLDABLE_UNFOLDED.height.toFloat(), unfoldedViewport.bottom, TOLERANCE)
        val placement = requireNotNull(unfoldedViewport.resolve(DialDisplaySettings()))
        val expectedRadius = TabletSurfaces.FOLDABLE_UNFOLDED.height * DialViewport.RADIUS_FRACTION
        assertEquals(expectedRadius, placement.radius, TOLERANCE)
        assertEquals(TabletSurfaces.FOLDABLE_UNFOLDED.width / 2f, placement.centerX, TOLERANCE)
        assertEquals(TabletSurfaces.FOLDABLE_UNFOLDED.height / 2f, placement.centerY, TOLERANCE)
    }

    private fun assertFoldRecovery(
        engine: WallpaperService.Engine,
        holder: ReadyFrameHolder,
        looper: ShadowLooper,
        frameCount: () -> Int,
        viewport: () -> DialViewport?,
    ) {
        RuntimeEnvironment.setQualifiers("sw1080dp-w1080dp-h2400dp-port-mdpi")
        engine.onSurfaceChanged(
            holder,
            0,
            TabletSurfaces.FOLDABLE_FOLDED.width,
            TabletSurfaces.FOLDABLE_FOLDED.height,
        )
        assertEquals(7, frameCount())
        assertTicksOncePerSecond(looper = looper, frameCount = frameCount)
        val recovered = requireNotNull(requireNotNull(viewport()).resolve(DialDisplaySettings()))
        val expectedRadius = TabletSurfaces.FOLDABLE_FOLDED.width * DialViewport.RADIUS_FRACTION
        assertEquals(expectedRadius, recovered.radius, TOLERANCE)
    }

    private fun assertTicksOncePerSecond(looper: ShadowLooper, frameCount: () -> Int) {
        val delay = looper.nextScheduledTaskTime.toMillis() - SystemClock.uptimeMillis()
        assertEquals(1000L, delay)

        val beforeCount = frameCount()
        looper.idleFor(Duration.ofMillis(999))
        assertEquals("Frame count must not increment before tick interval", beforeCount, frameCount())

        looper.idleFor(Duration.ofMillis(1))
        assertEquals("Frame count must increment exactly at 1 second mark", beforeCount + 1, frameCount())

        looper.idleFor(Duration.ofSeconds(1))
        assertEquals("Frame count must increment on subsequent 1 second tick", beforeCount + 2, frameCount())
    }

    private fun assertDialFitsWithinDimensions(width: Int, height: Int) {
        val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java).create()
        val service = controller.get()
        service.applicationInfo.flags = service.applicationInfo.flags or ApplicationInfo.FLAG_DEBUGGABLE

        val holder =
            ReadyFrameHolder(
                delegate = SurfaceView(service).holder,
                width = width,
                height = height,
            )
        var drawnViewport: DialViewport? = null
        val engine =
            service.createEngine(
                draw = { _, _, _, _, _, _, usable -> drawnViewport = usable },
                holder = holder,
                clock = Clock.fixed(Instant.parse("2026-03-21T12:00:00Z"), ZoneOffset.UTC),
            )

        try {
            engine.onSurfaceChanged(holder, 0, width, height)
            ShadowLog.clear()
            engine.onVisibilityChanged(true)

            val logs = ShadowLog.getLogsForTag("DialDisplay").map { it.msg }
            val fields = reportFields(logs.single { it.contains("DialLayout") })
            val cx = fields.getValue("cx").toFloat()
            val cy = fields.getValue("cy").toFloat()
            val radius = fields.getValue("radius").toFloat()

            assertCenterAndRadius(width = width, height = height, cx = cx, cy = cy, radius = radius)
            assertNoEdgeClipping(width = width, height = height, cx = cx, cy = cy, radius = radius)
            assertBalancedMargins(width = width, height = height, cx = cx, radius = radius)
            assertSliderExtremes(viewport = requireNotNull(drawnViewport), width = width, height = height)
        } finally {
            engine.onDestroy()
            holder.release()
            controller.destroy()
        }
    }

    private fun assertCenterAndRadius(width: Int, height: Int, cx: Float, cy: Float, radius: Float) {
        assertEquals(width / 2f, cx, TOLERANCE)
        assertEquals(height / 2f, cy, TOLERANCE)
        val expectedRadius = minOf(a = width, b = height) * DialViewport.RADIUS_FRACTION
        assertEquals(expectedRadius, radius, TOLERANCE)
    }

    private fun assertNoEdgeClipping(width: Int, height: Int, cx: Float, cy: Float, radius: Float) {
        val extent = radius * DialViewport.STROKED_EXTENT
        assertTrue("Left edge must not clip: cx=$cx extent=$extent", cx - extent >= -TOLERANCE)
        assertTrue("Top edge must not clip: cy=$cy extent=$extent", cy - extent >= -TOLERANCE)
        assertTrue("Right edge must not clip", cx + extent <= width + TOLERANCE)
        assertTrue("Bottom edge must not clip", cy + extent <= height + TOLERANCE)
    }

    private fun assertBalancedMargins(width: Int, height: Int, cx: Float, radius: Float) {
        val extent = radius * DialViewport.STROKED_EXTENT
        if (width > height) {
            val leftMargin = cx - extent
            val rightMargin = width - (cx + extent)
            assertEquals("Horizontal margins must be balanced", leftMargin, rightMargin, TOLERANCE)
        }
    }

    private fun assertSliderExtremes(viewport: DialViewport, width: Int, height: Int) {
        for (size in listOf(50, 100, 115)) {
            for (hSlider in listOf(0, 50, 100)) {
                for (vSlider in listOf(0, 50, 100)) {
                    val settings = DialDisplaySettings(size = size, horizontal = hSlider, vertical = vSlider)
                    val p = requireNotNull(viewport.resolve(settings))
                    val ext = p.radius * DialViewport.STROKED_EXTENT
                    assertTrue(p.centerX - ext >= -TOLERANCE)
                    assertTrue(p.centerY - ext >= -TOLERANCE)
                    assertTrue(p.centerX + ext <= width + TOLERANCE)
                    assertTrue(p.centerY + ext <= height + TOLERANCE)
                }
            }
        }
    }

    private companion object {
        const val TOLERANCE = 0.5f

        val TEST_INSTANT: Instant = Instant.parse("2026-03-21T12:00:00Z")

        val PRAGUE_LOCATION =
            ObservingLocation(
                latitude = 50.0875,
                longitude = 14.4213,
                zoneId = ZoneOffset.ofHours(1),
                source = ObservingLocation.Source.MANUAL,
            )

        val FIELDS = Regex("""(\w+)=([^ ]+)""")

        fun reportFields(line: String): Map<String, String> {
            assertTrue("unexpected layout report: $line", line.contains("DialLayout token="))
            return FIELDS.findAll(line).associate { it.groupValues[1] to it.groupValues[2] }
        }
    }
}
