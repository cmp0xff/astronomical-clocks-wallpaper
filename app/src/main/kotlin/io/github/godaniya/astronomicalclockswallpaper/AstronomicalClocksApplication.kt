package io.github.godaniya.astronomicalclockswallpaper

import android.app.Application

/** Application entry point that owns one-time startup maintenance. */
class AstronomicalClocksApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        LocationStore(this).migrateAndRepair()
    }
}
