package io.github.godaniya.astronomicalclockswallpaper

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset

class MutableDebugClockTest {
    private val baseInstant = Instant.parse("2026-10-04T12:00:00Z")
    private val baseClock = Clock.fixed(baseInstant, ZoneOffset.UTC)
    private val debugClock = MutableDebugClock(baseClock)

    @Test
    fun defaultMatchesBaseClock() {
        assertEquals(baseInstant, debugClock.instant())
        assertEquals(baseInstant.toEpochMilli(), debugClock.millis())
        assertEquals(ZoneOffset.UTC, debugClock.zone)
        assertEquals(Duration.ZERO, debugClock.currentOffset)
        assertNull(debugClock.currentFixedInstant)
    }

    @Test
    fun setOffsetShiftsTime() {
        val thirtyMinutes = Duration.ofMinutes(30)
        debugClock.setOffset(thirtyMinutes)

        val expectedInstant = baseInstant.plus(thirtyMinutes)
        assertEquals(expectedInstant, debugClock.instant())
        assertEquals(expectedInstant.toEpochMilli(), debugClock.millis())
        assertEquals(thirtyMinutes, debugClock.currentOffset)
        assertNull(debugClock.currentFixedInstant)
    }

    @Test
    fun setInstantOverridesBaseTime() {
        val fixed = Instant.parse("2026-06-21T00:00:00Z")
        debugClock.setInstant(fixed)

        assertEquals(fixed, debugClock.instant())
        assertEquals(fixed.toEpochMilli(), debugClock.millis())
        assertEquals(fixed, debugClock.currentFixedInstant)
        assertEquals(Duration.ZERO, debugClock.currentOffset)
    }

    @Test
    fun resetRestoresBaseClock() {
        debugClock.setOffset(Duration.ofHours(2))
        debugClock.reset()

        assertEquals(baseInstant, debugClock.instant())
        assertEquals(baseInstant.toEpochMilli(), debugClock.millis())
        assertEquals(Duration.ZERO, debugClock.currentOffset)
        assertNull(debugClock.currentFixedInstant)
    }

    @Test
    fun withZonePreservesState() {
        val offset = Duration.ofHours(1)
        debugClock.setOffset(offset)

        val pragueZone = ZoneId.of("Europe/Prague")
        val pragueClock = debugClock.withZone(pragueZone)

        assertEquals(pragueZone, pragueClock.zone)
        assertEquals(baseInstant.plus(offset), pragueClock.instant())
        assertEquals(baseInstant.plus(offset).toEpochMilli(), pragueClock.millis())
    }
}
