package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLog

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class AppearanceStoreTest {
    private val application = RuntimeEnvironment.getApplication()
    private lateinit var store: AppearanceStore
    private lateinit var preferences: SharedPreferences

    @Before
    fun setUp() {
        preferences = application.getSharedPreferences("appearance_settings", Context.MODE_PRIVATE)
        preferences
            .edit()
            .clear()
            .apply()
        store = AppearanceStore(application)
    }

    @Test
    fun defaultAppearanceIsSystem() {
        assertEquals(DialAppearance.SYSTEM, store.load())
    }

    @Test
    fun persistsThemeChoices() {
        store.save(DialAppearance.LIGHT)
        assertEquals(DialAppearance.LIGHT, store.load())

        store.save(DialAppearance.DARK)
        assertEquals(DialAppearance.DARK, store.load())

        store.save(DialAppearance.SYSTEM)
        assertEquals(DialAppearance.SYSTEM, store.load())
    }

    @Test
    fun malformedFallsBackToSystem() {
        preferences.edit().putInt(AppearanceStore.KEY_APPEARANCE, 42).apply()
        assertEquals(DialAppearance.SYSTEM, store.load())

        preferences.edit().putString(AppearanceStore.KEY_APPEARANCE, "UNKNOWN_MODE").apply()
        assertEquals(DialAppearance.SYSTEM, store.load())
    }

    // An unrecognized stored value is a fault (corruption or a value a newer build wrote), unlike an
    // absent key, which is the ordinary first-run default. Only the fault is logged, so a user whose
    // theme silently reverted leaves a trace.
    @Test
    fun unrecognizedValueIsLogged() {
        ShadowLog.clear()
        assertEquals(DialAppearance.SYSTEM, store.load())
        assertTrue(ShadowLog.getLogsForTag("AppearanceStore").isEmpty())

        preferences.edit().putString(AppearanceStore.KEY_APPEARANCE, "UNKNOWN_MODE").apply()
        assertEquals(DialAppearance.SYSTEM, store.load())
        val logs = ShadowLog.getLogsForTag("AppearanceStore")
        assertEquals(1, logs.size)
        assertTrue(logs.single().msg.contains("unrecognized appearance value"))
    }

    @Test
    fun notifiesRegisteredListener() {
        var notifiedKey: String? = null
        val listener =
            SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
                notifiedKey = key
            }
        store.registerListener(listener)
        try {
            store.save(DialAppearance.LIGHT)
            assertEquals(AppearanceStore.KEY_APPEARANCE, notifiedKey)
        } finally {
            store.unregisterListener(listener)
        }
    }

    @Test
    fun fromStringParsesModes() {
        assertEquals(DialAppearance.LIGHT, DialAppearance.fromString("LIGHT"))
        assertEquals(DialAppearance.DARK, DialAppearance.fromString("DARK"))
        assertEquals(DialAppearance.SYSTEM, DialAppearance.fromString("SYSTEM"))
        assertEquals(DialAppearance.SYSTEM, DialAppearance.fromString(null))
        assertEquals(DialAppearance.SYSTEM, DialAppearance.fromString("other"))
    }
}
