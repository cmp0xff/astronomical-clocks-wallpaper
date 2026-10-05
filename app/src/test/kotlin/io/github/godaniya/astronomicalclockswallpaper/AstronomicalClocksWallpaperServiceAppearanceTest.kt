package io.github.godaniya.astronomicalclockswallpaper

import android.content.res.Configuration
import android.os.Looper
import android.service.wallpaper.WallpaperService
import android.view.SurfaceView
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class AstronomicalClocksWallpaperServiceAppearanceTest {
    private val controller = Robolectric.buildService(AstronomicalClocksWallpaperService::class.java)
    private val engines = mutableListOf<WallpaperService.Engine>()
    private val renderedPalettes = mutableListOf<DialPalette>()
    private lateinit var holder: ReadyFrameHolder
    private lateinit var appearanceStore: AppearanceStore

    @Before
    fun setUp() {
        controller.create()
        holder = ReadyFrameHolder(SurfaceView(controller.get()).holder)
        appearanceStore = AppearanceStore(controller.get())
        appearanceStore.save(DialAppearance.SYSTEM)
    }

    @After
    fun tearDown() {
        engines.forEach { it.onDestroy() }
        holder.release()
        controller.destroy()
    }

    private fun createTestEngine(): WallpaperService.Engine {
        val engine =
            controller.get().createEngine(
                draw = { _, _, _, _, palette ->
                    renderedPalettes.add(palette)
                },
                holder = holder,
                clock = Clock.fixed(Instant.parse("2026-03-21T12:00:00Z"), ZoneOffset.UTC),
                deviceZone = { ZoneOffset.UTC },
            )
        engines.add(engine)
        return engine
    }

    private fun tick() {
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(1))
    }

    @Test
    fun visibleRefreshesOnNextTick() {
        val engine = createTestEngine()
        engine.onVisibilityChanged(true)
        val initialCount = renderedPalettes.size

        appearanceStore.save(DialAppearance.LIGHT)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(initialCount, renderedPalettes.size)

        tick()
        assertEquals(initialCount + 1, renderedPalettes.size)
        assertEquals(DialStyle.LIGHT_PALETTE, renderedPalettes.last())

        appearanceStore.save(DialAppearance.DARK)
        tick()
        assertEquals(initialCount + 2, renderedPalettes.size)
        assertEquals(DialStyle.DARK_PALETTE, renderedPalettes.last())
    }

    @Test
    fun hiddenIgnoresPreferenceChange() {
        val engine = createTestEngine()
        engine.onVisibilityChanged(false)
        renderedPalettes.clear()

        appearanceStore.save(DialAppearance.LIGHT)
        tick()
        assertEquals(0, renderedPalettes.size)

        engine.onVisibilityChanged(true)
        assertEquals(1, renderedPalettes.size)
        assertEquals(DialStyle.LIGHT_PALETTE, renderedPalettes.last())
    }

    @Test
    fun systemFollowsNightOnVisible() {
        appearanceStore.save(DialAppearance.SYSTEM)
        val engine = createTestEngine()
        engine.onSurfaceChanged(holder, SURFACE_FORMAT, SURFACE_WIDTH, SURFACE_HEIGHT)
        engine.onVisibilityChanged(true)
        val initialCount = renderedPalettes.size

        controller.get().onConfigurationChanged(nightConfiguration())
        assertEquals(initialCount + 1, renderedPalettes.size)
        assertEquals(DialStyle.DARK_PALETTE, renderedPalettes.last())
    }

    @Test
    fun systemDoesNotWakeHidden() {
        appearanceStore.save(DialAppearance.SYSTEM)
        val engine = createTestEngine()
        engine.onSurfaceChanged(holder, SURFACE_FORMAT, SURFACE_WIDTH, SURFACE_HEIGHT)
        engine.onVisibilityChanged(false)
        renderedPalettes.clear()

        controller.get().onConfigurationChanged(nightConfiguration())
        assertEquals(0, renderedPalettes.size)
    }

    // Copilot review discussion_r4177344106: onSurfaceDestroyed cancels the loop but leaves the
    // engine visible, so a night-mode change arriving in that gap must not redraw onto the released
    // surface. Before the fix this ran a tick whose finally re-armed the per-second loop, so even the
    // later tick kept rendering; both checks must stay at zero.
    @Test
    fun nightChangeWithoutSurface() {
        appearanceStore.save(DialAppearance.SYSTEM)
        val engine = createTestEngine()
        engine.onSurfaceChanged(holder, SURFACE_FORMAT, SURFACE_WIDTH, SURFACE_HEIGHT)
        engine.onVisibilityChanged(true)
        renderedPalettes.clear()

        engine.onSurfaceDestroyed(holder)
        controller.get().onConfigurationChanged(nightConfiguration())
        assertEquals(0, renderedPalettes.size)

        tick()
        assertEquals(0, renderedPalettes.size)

        engine.onSurfaceChanged(holder, SURFACE_FORMAT, SURFACE_WIDTH, SURFACE_HEIGHT)
        assertEquals(1, renderedPalettes.size)
        assertEquals(DialStyle.DARK_PALETTE, renderedPalettes.last())
        tick()
        assertEquals(2, renderedPalettes.size)
        assertEquals(DialStyle.DARK_PALETTE, renderedPalettes.last())
    }

    private fun nightConfiguration(): Configuration {
        val nightConfig = Configuration(controller.get().resources.configuration)
        nightConfig.uiMode = nightConfig.uiMode and Configuration.UI_MODE_NIGHT_MASK.inv() or
            Configuration.UI_MODE_NIGHT_YES
        return nightConfig
    }

    private companion object {
        const val SURFACE_FORMAT = 1
        const val SURFACE_WIDTH = 200
        const val SURFACE_HEIGHT = 200
    }
}
