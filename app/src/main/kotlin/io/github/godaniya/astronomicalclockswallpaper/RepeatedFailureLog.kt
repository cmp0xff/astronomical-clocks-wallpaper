package io.github.godaniya.astronomicalclockswallpaper

import android.util.Log

/**
 * Bounds the log noise from a failure that recurs on every tick. A wallpaper ticks once a second for
 * weeks, so a persistent fault would otherwise emit tens of thousands of stack traces.
 *
 * The first failure logs its stack trace, later ones only advance a counter, one summary is emitted
 * every [SUMMARY_EVERY] consecutive failures (about a minute at the 1 Hz tick), and the end of a
 * sustained episode is reported so the fault's end is visible. A single transient failure is
 * deliberately silent on recovery, which keeps a clean frame free of log output.
 *
 * Confined to the thread that drives the tick loop.
 */
internal class RepeatedFailureLog(
    private val tag: String,
    private val message: String,
    private val level: Int = Log.ERROR,
) {
    private var consecutiveFailures = 0

    fun recordFailure(error: Throwable? = null, detail: String? = null) {
        consecutiveFailures++
        if (consecutiveFailures == 1) {
            val text = if (detail != null) "$message: $detail" else message
            log(text, error)
        } else if (consecutiveFailures % SUMMARY_EVERY == 0) {
            val latest = if (error != null) ", latest: ${describe(error)}" else ""
            log("$message (repeated $consecutiveFailures times$latest)", null)
        }
    }

    fun recordSuccess() {
        if (consecutiveFailures > 1) {
            Log.i(tag, "$message (recovered after $consecutiveFailures consecutive failures)")
        }
        consecutiveFailures = 0
    }

    private fun log(text: String, error: Throwable?) {
        when (level) {
            Log.WARN -> Log.w(tag, text, error)
            Log.DEBUG -> Log.d(tag, text, error)
            else -> Log.e(tag, text, error)
        }
    }

    private fun describe(error: Throwable): String = "${error.javaClass.simpleName}: ${error.message.orEmpty()}"

    private companion object {
        const val SUMMARY_EVERY = 60
    }
}
