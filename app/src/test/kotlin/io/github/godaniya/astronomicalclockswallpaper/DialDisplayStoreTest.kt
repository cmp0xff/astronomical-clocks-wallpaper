package io.github.godaniya.astronomicalclockswallpaper

import android.content.Context
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class DialDisplayStoreTest {
    @Test
    fun corruptionBoundsAndPureReads() {
        val context = RuntimeEnvironment.getApplication()
        val preferences = context.getSharedPreferences("dial_display", Context.MODE_PRIVATE)
        val store = DialDisplayStore(context)
        assertEquals(DialDisplaySettings(), store.load())
        assertEquals(emptyMap<String, Any>(), preferences.all)
        preferences
            .edit()
            .putString("size", "broken")
            .putLong("horizontal", Long.MAX_VALUE)
            .putInt("vertical", -10)
            .putFloat("brightness", Float.NaN)
            .apply()
        val original = preferences.all
        assertEquals(DialDisplaySettings(horizontal = 100, vertical = 0), store.load())
        assertEquals(original, preferences.all)
        preferences
            .edit()
            .putInt("size", 200)
            .putFloat("horizontal", 1.5f)
            .putInt("vertical", 120)
            .putInt("brightness", 1)
            .apply()
        assertEquals(DialDisplaySettings(size = 115, vertical = 100, brightness = 80), store.load())
    }

    @Test
    fun storeRecreation() {
        val context = RuntimeEnvironment.getApplication()
        val saved = DialDisplaySettings(size = 50, horizontal = 0, vertical = 100, brightness = 80)
        DialDisplayStore(context).save(saved)
        assertEquals(saved, DialDisplayStore(context).load())
    }
}
