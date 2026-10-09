package io.github.godaniya.astronomicalclockswallpaper

import android.graphics.Canvas
import android.service.wallpaper.WallpaperService
import android.view.SurfaceHolder
import java.time.Clock
import java.time.ZoneId

/** Creates an engine with palette awareness and optional controlled surface holder. */
internal fun AstronomicalClocksWallpaperService.createEngine(
    draw: (Canvas, ClockState, DialGeometry?, DialLayers, DialPalette) -> Unit,
    holder: SurfaceHolder? = null,
    clock: Clock = Clock.systemUTC(),
    deviceZone: () -> ZoneId = ZoneId::systemDefault,
    calculator: AstronomyCalculator = AstronomyEngineCalculator(),
): WallpaperService.Engine {
    val engine =
        createEngine(
            draw = { canvas, state, geometry, layers, palette, _, _ -> draw(canvas, state, geometry, layers, palette) },
            holder = holder,
            clock = clock,
            deviceZone = deviceZone,
            calculator = calculator,
        )
    return engine
}
