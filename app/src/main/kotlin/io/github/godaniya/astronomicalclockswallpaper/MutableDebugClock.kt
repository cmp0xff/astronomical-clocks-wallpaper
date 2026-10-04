package io.github.godaniya.astronomicalclockswallpaper

import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneId

/**
 * A [Clock] that supports applying a time offset or fixing to a specific [Instant] in debug mode.
 */
internal class MutableDebugClock(private val baseClock: Clock = Clock.systemUTC()) : Clock() {
    @Volatile
    private var offset: Duration = Duration.ZERO

    @Volatile
    private var fixedInstant: Instant? = null

    val currentOffset: Duration
        get() = offset

    val currentFixedInstant: Instant?
        get() = fixedInstant

    override fun getZone(): ZoneId = baseClock.zone

    override fun withZone(zone: ZoneId): Clock {
        val copy = MutableDebugClock(baseClock.withZone(zone))
        copy.offset = this.offset
        copy.fixedInstant = this.fixedInstant
        return copy
    }

    override fun instant(): Instant {
        fixedInstant?.let { return it }
        val now = baseClock.instant()
        return if (!offset.isZero) now.plus(offset) else now
    }

    override fun millis(): Long {
        fixedInstant?.let { return it.toEpochMilli() }
        val now = baseClock.millis()
        return if (!offset.isZero) now + offset.toMillis() else now
    }

    fun setOffset(duration: Duration) {
        fixedInstant = null
        offset = duration
    }

    fun setInstant(instant: Instant) {
        offset = Duration.ZERO
        fixedInstant = instant
    }

    fun reset() {
        offset = Duration.ZERO
        fixedInstant = null
    }
}
