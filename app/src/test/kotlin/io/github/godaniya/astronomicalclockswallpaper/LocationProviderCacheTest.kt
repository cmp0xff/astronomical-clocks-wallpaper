package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.content.Context
import android.location.Location
import android.location.LocationManager
import android.os.Looper
import android.os.SystemClock
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration

/** Checks the inclusive five-minute cache limit using elapsed time independently of wall time. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class LocationProviderCacheTest {
    private val application = RuntimeEnvironment.getApplication()
    private val locationManager = application.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private val results = mutableListOf<LocationResult>()

    @Before
    fun prepareClockAndProvider() {
        shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        shadowOf(locationManager).enableNetworkProvider()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMinutes(10))
    }

    @Test
    fun disabledProviderAllowsCache() {
        shadowOf(locationManager).setProviderEnabled(LocationManager.NETWORK_PROVIDER, false)
        seedCache(SystemClock.elapsedRealtimeNanos())
        LocationProvider(application).fetch { results.add(it) }
        assertEquals(listOf(LocationResult.Success(CACHED_LOCATION)), results)
    }

    @Test
    fun acceptsBoundaryWithOldWallTime() {
        seedCache(SystemClock.elapsedRealtimeNanos() - MAX_AGE_NANOS)
        LocationProvider(application).fetch { results.add(it) }
        assertEquals(listOf(LocationResult.Success(CACHED_LOCATION)), results)
    }

    @Test
    fun rejectsCacheBeyondFiveMinutes() {
        assertRequestsFresh(SystemClock.elapsedRealtimeNanos() - MAX_AGE_NANOS - 1)
    }

    @Test
    fun rejectsMissingTimestamp() {
        assertRequestsFresh(0)
    }

    @Test
    fun rejectsNegativeTimestamp() {
        assertRequestsFresh(-1)
    }

    @Test
    fun rejectsFutureTimestamp() {
        assertRequestsFresh(SystemClock.elapsedRealtimeNanos() + 1)
    }

    @Test
    fun refreshRejectsEvenZeroAgeCache() {
        seedCache(SystemClock.elapsedRealtimeNanos())
        val provider = LocationProvider(application)
        provider.fetch(forceFresh = true) { results.add(it) }
        assertTrue(results.isEmpty())
        assertEquals(1, shadowOf(locationManager).networkListeners().size)
        provider.cancel()
    }

    @Test
    fun malformedCacheRequestsFresh() {
        seedCache(SystemClock.elapsedRealtimeNanos(), latitude = Double.NaN)
        val provider = LocationProvider(application)
        provider.fetch { results.add(it) }
        assertTrue(results.isEmpty())
        assertEquals(1, shadowOf(locationManager).networkListeners().size)
        provider.cancel()
    }

    private fun assertRequestsFresh(timestamp: Long) {
        seedCache(timestamp)
        LocationProvider(application).fetch { results.add(it) }
        assertTrue(results.isEmpty())
        assertEquals(1, shadowOf(locationManager).networkListeners().size)
        val fresh = Location(LocationManager.NETWORK_PROVIDER)
        fresh.latitude = 10.0
        fresh.longitude = 20.0
        shadowOf(locationManager).simulateLocation(LocationManager.NETWORK_PROVIDER, fresh)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(
            listOf(
                LocationResult.Success(CoordinateFix(latitude = 10.0, longitude = 20.0)),
            ),
            results,
        )
    }

    @Suppress("DEPRECATION")
    private fun seedCache(timestamp: Long, latitude: Double = CACHED_LOCATION.latitude) {
        val cached = Location(LocationManager.NETWORK_PROVIDER)
        cached.latitude = latitude
        cached.longitude = CACHED_LOCATION.longitude
        cached.elapsedRealtimeNanos = timestamp
        cached.time = 1L
        shadowOf(locationManager).setLastKnownLocation(LocationManager.NETWORK_PROVIDER, cached)
    }

    private companion object {
        const val MAX_AGE_NANOS = 300_000_000_000L
        val CACHED_LOCATION =
            CoordinateFix(latitude = 37.42, longitude = -122.08)
    }
}
