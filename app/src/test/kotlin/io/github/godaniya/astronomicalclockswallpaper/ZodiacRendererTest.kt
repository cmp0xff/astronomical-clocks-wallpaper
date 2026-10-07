package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.util.Log
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowLog

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ZodiacRendererTest {
    private val prague = DialGeometry(localSiderealAngleDeg = 0.0, trueObliquityDeg = 23.44, latitudeDeg = 50.08)
    private val renderer = ZodiacRenderer()

    @Test
    fun normalDrawProducesNoWarnings() {
        val bitmap = Bitmap.createBitmap(800, 800, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        val projection = OrlojProjection(prague)

        ShadowLog.clear()
        renderer.drawBalanced(canvas, projection)
        val logs = ShadowLog.getLogsForTag("ZodiacRenderer")
        assertTrue("Normal render must not emit logs, but was: $logs", logs.isEmpty())
    }

    @Test
    fun degenerateCoordinatesBounded() {
        val bitmap = Bitmap.createBitmap(800, 800, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        val degenerateProjection =
            object : OrlojProjection(prague) {
                override fun eclipticPoint(longitudeDeg: Double): DialPoint =
                    if (longitudeDeg == 0.0) DialPoint(x = 0.0, y = 0.0) else super.eclipticPoint(longitudeDeg)
            }
        val normalProjection = OrlojProjection(prague)

        ShadowLog.clear()
        repeat(3) { renderer.drawBalanced(canvas, degenerateProjection) }
        val warnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
        assertEquals(1, warnings.size)
        assertTrue(warnings[0].msg.contains("skipping divider: degenerate coordinate"))
        assertTrue(warnings[0].msg.contains("sign 0: distance=0.0"))

        // One completed healthy frame ends the episode and logs recovery.
        renderer.drawBalanced(canvas, normalProjection)
        val recoveries = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.INFO }
        assertEquals(1, recoveries.size)
        assertTrue(recoveries[0].msg.contains("recovered after 3 consecutive failures"))

        // A subsequent degenerate frame logs again as a new episode.
        renderer.drawBalanced(canvas, degenerateProjection)
        val subsequentWarnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
        assertEquals(2, subsequentWarnings.size)
    }

    @Test
    fun nonFiniteCoordinatesBounded() {
        for (coordinate in listOf(Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY)) {
            assertNonFinitePointBounded(DialPoint(x = coordinate, y = 0.0))
            assertNonFinitePointBounded(DialPoint(x = 0.0, y = coordinate))
        }
    }

    private fun assertNonFinitePointBounded(point: DialPoint) {
        val caseRenderer = ZodiacRenderer()
        val canvas = Canvas(Bitmap.createBitmap(800, 800, Bitmap.Config.ARGB_8888))
        val faultyProjection =
            object : OrlojProjection(prague) {
                override fun eclipticPoint(longitudeDeg: Double): DialPoint =
                    if (longitudeDeg == 30.0) point else super.eclipticPoint(longitudeDeg)
            }
        val expectedDistance = if (point.x.isNaN() || point.y.isNaN()) "NaN" else "Infinity"

        ShadowLog.clear()
        repeat(3) { caseRenderer.drawBalanced(canvas, faultyProjection) }
        val warnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
        assertEquals("Warnings for $point", 1, warnings.size)
        assertTrue(warnings[0].msg.contains("skipping divider: degenerate coordinate"))
        assertTrue(warnings[0].msg.contains("sign 1: distance=$expectedDistance"))

        caseRenderer.drawBalanced(canvas, OrlojProjection(prague))
        val recoveries = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.INFO }
        assertEquals("Recoveries for $point", 1, recoveries.size)
        assertTrue(recoveries[0].msg.contains("recovered after 3 consecutive failures"))

        caseRenderer.drawBalanced(canvas, faultyProjection)
        val subsequentWarnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
        assertEquals("New episode for $point", 2, subsequentWarnings.size)
        assertEquals(warnings[0].msg, subsequentWarnings[1].msg)
    }

    @Test
    fun nonFiniteEndpointsBounded() {
        val bitmap = Bitmap.createBitmap(800, 800, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        val nonFiniteCircleProjection =
            object : OrlojProjection(prague) {
                override val zodiacCircle: DialCircle
                    get() = DialCircle(center = DialPoint(x = Double.NaN, y = 0.0), radius = 1.0)
            }
        val normalProjection = OrlojProjection(prague)

        ShadowLog.clear()
        repeat(3) { renderer.drawBalanced(canvas, nonFiniteCircleProjection) }
        val endpointWarnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
        assertEquals(1, endpointWarnings.size)
        assertTrue(endpointWarnings[0].msg.contains("skipping divider: non-finite endpoint"))

        // Recovery check after healthy frame
        renderer.drawBalanced(canvas, normalProjection)
        val recoveries = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.INFO }
        assertEquals(1, recoveries.size)
        assertTrue(recoveries[0].msg.contains("recovered after 3 consecutive failures"))
    }

    @Test
    fun floatEndpointOverflowBounded() {
        for (direction in listOf(
            DialPoint(x = 1.0, y = 0.0),
            DialPoint(x = -1.0, y = 0.0),
            DialPoint(x = 0.0, y = 1.0),
            DialPoint(x = 0.0, y = -1.0),
        )) {
            val caseRenderer = ZodiacRenderer()
            val canvas = DividerRecordingCanvas()
            val faultyProjection =
                object : OrlojProjection(prague) {
                    override val zodiacCircle = DialCircle(center = DialPoint(x = 0.0, y = 0.0), radius = 1e40)

                    override fun eclipticPoint(longitudeDeg: Double): DialPoint = direction
                }

            ShadowLog.clear()
            repeat(3) { caseRenderer.drawBalanced(canvas, faultyProjection) }
            assertEquals("Overflow divider calls for $direction", 0, canvas.dividerCalls)
            val warnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
            assertEquals("Warnings for $direction", 1, warnings.size)
            assertTrue(warnings[0].msg.contains("non-finite endpoint: sign 0: start=1.0E40, end=1.0E40"))

            caseRenderer.drawBalanced(canvas, OrlojProjection(prague))
            assertEquals("Healthy divider calls for $direction", 12, canvas.dividerCalls)
            val recoveries = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.INFO }
            assertEquals(1, recoveries.size)
            assertTrue(recoveries[0].msg.contains("non-finite endpoint (recovered after 3 consecutive failures)"))

            caseRenderer.drawBalanced(canvas, faultyProjection)
            assertEquals("Recurrence divider calls for $direction", 12, canvas.dividerCalls)
            val subsequentWarnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
            assertEquals(2, subsequentWarnings.size)
            assertEquals(warnings[0].msg, subsequentWarnings[1].msg)
        }
    }

    @Test
    fun mixedFaultsRecoverSeparately() {
        val canvas = DividerRecordingCanvas()
        val coordinateProjection =
            object : OrlojProjection(prague) {
                override fun eclipticPoint(longitudeDeg: Double): DialPoint =
                    if (longitudeDeg == 30.0) DialPoint(x = 0.0, y = 0.0) else super.eclipticPoint(longitudeDeg)
            }
        val mixedProjection =
            object : OrlojProjection(prague) {
                override val zodiacCircle = DialCircle(center = DialPoint(x = Double.NaN, y = 0.0), radius = 1.0)

                override fun eclipticPoint(longitudeDeg: Double): DialPoint =
                    coordinateProjection.eclipticPoint(longitudeDeg)
            }

        ShadowLog.clear()
        repeat(3) { renderer.drawBalanced(canvas, mixedProjection) }
        assertEquals(0, canvas.dividerCalls)
        val warnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
        assertEquals(2, warnings.size)
        assertTrue(warnings[0].msg.contains("degenerate coordinate: sign 1: distance=0.0"))
        assertTrue(warnings[1].msg.contains("non-finite endpoint: sign 0: start=NaN, end=NaN"))

        renderer.drawBalanced(canvas, coordinateProjection)
        val endpointRecovery = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.INFO }
        assertEquals(1, endpointRecovery.size)
        assertTrue(endpointRecovery[0].msg.contains("non-finite endpoint (recovered after 3 consecutive failures)"))
        assertEquals(2, ShadowLog.getLogsForTag("ZodiacRenderer").count { it.type == Log.WARN })

        renderer.drawBalanced(canvas, OrlojProjection(prague))
        val recoveries = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.INFO }
        assertEquals(2, recoveries.size)
        assertTrue(recoveries[1].msg.contains("degenerate coordinate (recovered after 4 consecutive failures)"))

        renderer.drawBalanced(canvas, mixedProjection)
        val subsequentWarnings = ShadowLog.getLogsForTag("ZodiacRenderer").filter { it.type == Log.WARN }
        assertEquals(4, subsequentWarnings.size)
        assertEquals(warnings[0].msg, subsequentWarnings[2].msg)
        assertEquals(warnings[1].msg, subsequentWarnings[3].msg)
    }

    private fun ZodiacRenderer.drawBalanced(canvas: Canvas, projection: OrlojProjection) {
        val saveCount = canvas.saveCount
        draw(canvas, projection)
        assertEquals("Canvas save count after draw", saveCount, canvas.saveCount)
    }

    private class DividerRecordingCanvas : Canvas(Bitmap.createBitmap(800, 800, Bitmap.Config.ARGB_8888)) {
        var dividerCalls = 0
            private set

        // Corrupt circle geometry is intentional; this canvas observes only divider submissions.
        override fun drawCircle(cx: Float, cy: Float, radius: Float, paint: Paint) = Unit

        override fun drawLine(startX: Float, startY: Float, stopX: Float, stopY: Float, paint: Paint) {
            dividerCalls++
            assertTrue(
                "Submitted divider endpoints must be finite",
                startX.isFinite() && startY.isFinite() && stopX.isFinite() && stopY.isFinite(),
            )
        }
    }
}
