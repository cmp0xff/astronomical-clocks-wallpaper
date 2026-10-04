package io.github.godaniya.astronomicalclockswallpaper

import android.util.Log
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog

/** Verifies the tick-loop failure log stays bounded when a fault repeats every second. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class RepeatedFailureLogTest {
    private val log = RepeatedFailureLog(tag = TAG, message = MESSAGE)

    @Before
    fun clearLogs() {
        ShadowLog.clear()
    }

    @Test
    fun firstFailureLogsThrowable() {
        val failure = IllegalStateException("boom")
        log.recordFailure(failure)
        val entry = ShadowLog.getLogsForTag(TAG).single()
        assertEquals(Log.ERROR, entry.type)
        assertEquals(MESSAGE, entry.msg)
        assertSame(failure, entry.throwable)
    }

    @Test
    fun repeatsAreCountedNotLogged() {
        repeat(59) { log.recordFailure(IllegalStateException("boom")) }
        assertEquals(1, ShadowLog.getLogsForTag(TAG).size)
    }

    @Test
    fun summaryEverySixtyFailures() {
        repeat(60) { log.recordFailure(IllegalStateException("boom")) }
        val entries = ShadowLog.getLogsForTag(TAG)
        assertEquals(2, entries.size)
        assertEquals(Log.ERROR, entries.last().type)
        assertTrue(entries.last().msg.contains("repeated 60 times"))
        assertTrue(entries.last().msg.contains("IllegalStateException: boom"))
    }

    @Test
    fun recoveryAfterBurstIsLogged() {
        repeat(2) { log.recordFailure(IllegalStateException("boom")) }
        log.recordSuccess()
        val entries = ShadowLog.getLogsForTag(TAG)
        assertEquals(2, entries.size)
        assertEquals(Log.INFO, entries.last().type)
        assertTrue(entries.last().msg.contains("recovered after 2 consecutive failures"))
    }

    @Test
    fun levelIsConfigurable() {
        val warnLog = RepeatedFailureLog(tag = TAG, message = MESSAGE, level = Log.WARN)
        warnLog.recordFailure(IllegalStateException("boom"))
        assertEquals(Log.WARN, ShadowLog.getLogsForTag(TAG).single().type)
    }

    @Test
    fun singleFailureRecoversSilently() {
        log.recordFailure(IllegalStateException("boom"))
        log.recordSuccess()
        assertEquals(1, ShadowLog.getLogsForTag(TAG).size)
    }

    @Test
    fun counterResetsAcrossEpisodes() {
        log.recordFailure(IllegalStateException("boom"))
        log.recordSuccess()
        log.recordFailure(IllegalStateException("boom"))
        val entries = ShadowLog.getLogsForTag(TAG)
        assertEquals(2, entries.size)
        assertEquals(MESSAGE, entries.last().msg)
    }

    @Test
    fun failureWithoutThrowableLogs() {
        log.recordFailure()
        val entry = ShadowLog.getLogsForTag(TAG).single()
        assertEquals(Log.ERROR, entry.type)
        assertEquals(MESSAGE, entry.msg)
        assertNull(entry.throwable)
    }

    @Test
    fun failureWithDetailLogsDetail() {
        val failure = IllegalArgumentException("bad argument")
        log.recordFailure(failure, detail = "bad argument")
        val entry = ShadowLog.getLogsForTag(TAG).single()
        assertEquals(Log.ERROR, entry.type)
        assertEquals("$MESSAGE: bad argument", entry.msg)
        assertSame(failure, entry.throwable)
    }

    @Test
    fun summaryWithoutThrowable() {
        repeat(60) { log.recordFailure() }
        val entries = ShadowLog.getLogsForTag(TAG)
        assertEquals(2, entries.size)
        assertEquals(Log.ERROR, entries.last().type)
        assertTrue(entries.last().msg.contains("repeated 60 times"))
        assertFalse(entries.last().msg.contains("latest:"))
    }

    private companion object {
        const val TAG = "RepeatedFailureLogTest"
        const val MESSAGE = "unexpected error; keeping tick loop alive"
    }
}
