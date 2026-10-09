package io.github.godaniya.astronomicalclockswallpaper

import android.content.pm.ApplicationInfo
import android.view.SurfaceView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36], qualifiers = "w1080dp-h1000dp-mdpi")
class WallpaperLayoutDiagnosticTest {
    // A surface wider than the display is scrolled by the framework, so the layout report must name
    // the centre in screen coordinates: that is what the device harnesses probe in a capture.
    @Test
    fun reportsScreenSpaceCentre() {
        val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java).create()
        val service = controller.get()
        // diagnoseLayout is gated on FLAG_DEBUGGABLE; the release merged manifest is not debuggable,
        // so set the flag the debug manifest carries rather than assume the variant under test.
        service.applicationInfo.flags = service.applicationInfo.flags or ApplicationInfo.FLAG_DEBUGGABLE
        val holder =
            ReadyFrameHolder(
                delegate = SurfaceView(service).holder,
                width = SURFACE_WIDTH,
                height = SURFACE_HEIGHT,
            )
        var drawn: DialViewport? = null
        val engine =
            service.createEngine(
                draw = { _, _, _, _, _, _, usable -> drawn = usable },
                holder = holder,
                clock = Clock.fixed(Instant.parse("2026-03-21T12:00:00Z"), ZoneOffset.UTC),
            )
        try {
            // The surface has 920px of slack, so a -500 scroll is interior and the window origin is
            // 500. Centred default settings put the drawn centre at surface x=1040, which therefore
            // appears at screen 540 = display/2. The offsets are set before the first draw so the
            // deduplicated report below is the scrolled one and never the unscrolled frame.
            engine.onOffsetsChanged(0.5f, 0.5f, 0f, 0f, -WINDOW_ORIGIN, 0)
            ShadowLog.clear()
            engine.onVisibilityChanged(true)
            val fields = reportFields(ShadowLog.getLogsForTag(DIAL_DISPLAY_TAG).map { it.msg }.single())
            assertEquals(DISPLAY_WIDTH, fields.getValue("width").toInt())
            assertEquals(DISPLAY_HEIGHT, fields.getValue("height").toInt())
            assertEquals(WINDOW_ORIGIN.toFloat(), requireNotNull(drawn).left, TOLERANCE)
            val cx = fields.getValue("cx").toFloat()
            val cy = fields.getValue("cy").toFloat()
            val radius = fields.getValue("radius").toFloat()
            assertEquals(DISPLAY_WIDTH / 2f, cx, TOLERANCE)
            assertEquals(DISPLAY_HEIGHT / 2f, cy, TOLERANCE)
            // Mirror the parser's own fit check: scripts/device_layer.py rejects a report whose drawn
            // disc, radius * STROKED_EXTENT, leaves the frame. This fixture sits about 68 px clear of
            // the frame, so it shows acceptance, not the boundary. The boundary is pinned by
            // scripts/test_device_smoke.py::test_reports_reject_a_clipped_outer_stroke and by
            // DialDisplayRenderTest.nativeCornerPlacement, which already uses STROKED_EXTENT.
            val extent = radius * DialViewport.STROKED_EXTENT
            assertTrue(cx - extent >= -EXTENT_TOLERANCE_PX)
            assertTrue(cy - extent >= -EXTENT_TOLERANCE_PX)
            assertTrue(cx + extent <= DISPLAY_WIDTH + EXTENT_TOLERANCE_PX)
            assertTrue(cy + extent <= DISPLAY_HEIGHT + EXTENT_TOLERANCE_PX)
        } finally {
            engine.onDestroy()
            holder.release()
            controller.destroy()
        }
    }

    private companion object {
        const val DIAL_DISPLAY_TAG = "DialDisplay"
        const val SURFACE_WIDTH = 2000
        const val SURFACE_HEIGHT = 1000
        const val DISPLAY_WIDTH = 1080
        const val DISPLAY_HEIGHT = 1000
        const val WINDOW_ORIGIN = 500
        const val TOLERANCE = 0.01f

        // Matches device_layer.EXTENT_TOLERANCE_PX: the acceptance slack the parser allows around the
        // stroked extent. Equal to TOLERANCE but kept separate, because it absorbs the float rounding
        // of the printed radius rather than a placement difference.
        const val EXTENT_TOLERANCE_PX = 0.01f
        val FIELDS = Regex("""(\w+)=([^ ]+)""")

        fun reportFields(line: String): Map<String, String> {
            assertTrue("unexpected layout report: $line", line.contains("DialLayout token=layout "))
            return FIELDS.findAll(line).associate { it.groupValues[1] to it.groupValues[2] }
        }
    }
}
