package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Looper
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

/** Exercises delayed callbacks and request cleanup without relying on provider-side cancellation. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36])
class LocationProviderLifecycleTest {
    private val application = RuntimeEnvironment.getApplication()
    private val locationManager = application.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private val provider = LocationProvider(application)
    private val results = mutableListOf<LocationResult>()

    @Before
    fun prepareProvider() {
        shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        shadowOf(locationManager).enableNetworkProvider()
    }

    @Test
    fun cancelDropsLateCallbacks() {
        val listener = startRequest()
        provider.cancel()
        provider.cancel()
        listener.onLocationChanged(location())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertTrue(results.isEmpty())
        assertTrue(shadowOf(locationManager).networkListeners().isEmpty())
    }

    @Test
    fun newRequestSupersedesOld() {
        val firstListener = startRequest()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(5))
        val secondListener = startRequest()
        firstListener.onLocationChanged(location())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(5))
        assertTrue(results.isEmpty())
        assertEquals(1, shadowOf(locationManager).networkListeners().size)
        secondListener.onLocationChanged(location())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertEquals(listOf(LocationResult.Success(EXPECTED_LOCATION)), results)
    }

    @Test
    fun successDeliveredOnce() {
        val listener = startRequest()
        listener.onLocationChanged(location())
        listener.onLocationChanged(location())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertEquals(listOf(LocationResult.Success(EXPECTED_LOCATION)), results)
        assertTrue(shadowOf(locationManager).networkListeners().isEmpty())
    }

    @Test
    fun timeoutAtTenSeconds() {
        val listener = startRequest()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(9_999))
        assertTrue(results.isEmpty())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(1))
        listener.onLocationChanged(location())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertEquals(listOf(LocationResult.Failure(LocationFailureReason.TIMEOUT)), results)
        assertTrue(shadowOf(locationManager).networkListeners().isEmpty())
    }

    @Test
    fun disabledProviderFailsOnce() {
        val listener = startRequest()
        listener.onProviderDisabled(LocationManager.NETWORK_PROVIDER)
        listener.onLocationChanged(location())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertEquals(listOf(LocationResult.Failure(LocationFailureReason.PROVIDER_DISABLED)), results)
        assertTrue(shadowOf(locationManager).networkListeners().isEmpty())
    }

    @Test
    @SuppressLint("Range")
    fun malformedFixFailsOnce() {
        val listener = startRequest()
        val invalid = location()
        invalid.latitude = Double.NaN
        listener.onLocationChanged(invalid)
        listener.onLocationChanged(location())
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertEquals(listOf(LocationResult.Failure(LocationFailureReason.FETCH_FAILED)), results)
        assertTrue(shadowOf(locationManager).networkListeners().isEmpty())
    }

    @Test
    fun callbackCanStartNextRequest() {
        provider.fetch(forceFresh = true) { location ->
            results.add(location)
            provider.fetch(forceFresh = true) { next -> results.add(next) }
        }
        val firstListener = shadowOf(locationManager).networkListeners().single()
        firstListener.onLocationChanged(location())
        val secondListener = shadowOf(locationManager).networkListeners().single()
        firstListener.onLocationChanged(location())
        assertEquals(listOf(LocationResult.Success(EXPECTED_LOCATION)), results)
        secondListener.onLocationChanged(location())
        assertEquals(
            listOf(
                LocationResult.Success(EXPECTED_LOCATION),
                LocationResult.Success(EXPECTED_LOCATION),
            ),
            results,
        )
        assertTrue(shadowOf(locationManager).networkListeners().isEmpty())
    }

    private fun startRequest(): LocationListener {
        provider.fetch(forceFresh = true) { results.add(it) }
        return shadowOf(locationManager).networkListeners().single()
    }

    private fun location(): Location {
        val location = Location(LocationManager.NETWORK_PROVIDER)
        location.latitude = EXPECTED_LOCATION.latitude
        location.longitude = EXPECTED_LOCATION.longitude
        return location
    }

    private companion object {
        val EXPECTED_LOCATION =
            CoordinateFix(latitude = 37.42, longitude = -122.08)
    }
}
