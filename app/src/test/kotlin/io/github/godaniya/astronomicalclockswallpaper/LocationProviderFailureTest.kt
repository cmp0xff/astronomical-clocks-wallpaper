package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
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
import org.robolectric.annotation.Implementation
import org.robolectric.annotation.Implements
import org.robolectric.shadow.api.Shadow
import org.robolectric.shadows.ShadowLocationManager
import java.time.Duration

/** Covers provider disappearance and permission races at each platform interaction. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 36], shadows = [FailingLocationManagerShadow::class])
class LocationProviderFailureTest {
    private val application = RuntimeEnvironment.getApplication()
    private val locationManager = application.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private val locationShadow = Shadow.extract<FailingLocationManagerShadow>(locationManager)
    private val provider = LocationProvider(application)
    private val results = mutableListOf<LocationResult>()

    @Before
    fun prepareProvider() {
        shadowOf(application).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION)
        locationShadow.enableNetworkProvider()
    }

    @Test
    fun deniedCacheFallsBack() {
        locationShadow.cacheFailure = SecurityException("cache denied in test")
        assertCacheFailureFallsBack()
    }

    @Test
    fun unavailableCacheFallsBack() {
        locationShadow.cacheFailure = IllegalArgumentException("provider unavailable in test")
        assertCacheFailureFallsBack()
    }

    @Test
    fun deniedRequestCleansUp() {
        locationShadow.requestFailure = SecurityException("request denied in test")
        assertRequestFailureCleansUp(LocationFailureReason.PERMISSION_DENIED)
    }

    @Test
    fun missingProviderCleansUp() {
        locationShadow.requestFailure = IllegalArgumentException("provider disappeared in test")
        assertRequestFailureCleansUp(LocationFailureReason.PROVIDER_UNAVAILABLE)
    }

    @Test
    fun deniedCancelDropsCallbacks() {
        provider.fetch(forceFresh = true) { results.add(it) }
        val listener = locationShadow.networkListeners().single()
        locationShadow.removalFailure = SecurityException("removal denied in test")
        provider.cancel()
        provider.cancel()
        listener.onLocationChanged(Location(LocationManager.NETWORK_PROVIDER))
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertTrue(results.isEmpty())
        assertEquals(1, locationShadow.removalAttempts)
    }

    @Test
    fun deniedTimeoutFailsOnce() {
        provider.fetch(forceFresh = true) { results.add(it) }
        val listener = locationShadow.networkListeners().single()
        locationShadow.removalFailure = SecurityException("removal denied in test")
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        listener.onLocationChanged(Location(LocationManager.NETWORK_PROVIDER))
        provider.cancel()
        assertEquals(listOf(LocationResult.Failure(LocationFailureReason.TIMEOUT)), results)
        assertEquals(1, locationShadow.removalAttempts)
    }

    private fun assertCacheFailureFallsBack() {
        provider.fetch { results.add(it) }
        assertTrue(results.isEmpty())
        val location = Location(LocationManager.NETWORK_PROVIDER)
        location.latitude = 10.0
        location.longitude = 20.0
        locationShadow.simulateLocation(LocationManager.NETWORK_PROVIDER, location)
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertEquals(
            listOf(
                LocationResult.Success(CoordinateFix(latitude = 10.0, longitude = 20.0)),
            ),
            results,
        )
        assertTrue(locationShadow.networkListeners().isEmpty())
    }

    private fun assertRequestFailureCleansUp(expectedReason: LocationFailureReason) {
        provider.fetch(forceFresh = true) { results.add(it) }
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10))
        assertEquals(listOf(LocationResult.Failure(expectedReason)), results)
        assertTrue(locationShadow.networkListeners().isEmpty())
        assertEquals(1, locationShadow.removalAttempts)
    }
}

/** Throws after registration to ensure partial requests are also unregistered on failure. */
@Implements(LocationManager::class)
class FailingLocationManagerShadow : ShadowLocationManager() {
    var cacheFailure: RuntimeException? = null
    var requestFailure: RuntimeException? = null
    var removalFailure: SecurityException? = null
    var removalAttempts = 0
        private set

    @Implementation
    override fun getLastKnownLocation(provider: String): Location? {
        cacheFailure?.let { throw it }
        return super.getLastKnownLocation(provider)
    }

    @Implementation
    override fun requestSingleUpdate(provider: String, listener: LocationListener, looper: Looper?) {
        super.requestSingleUpdate(provider, listener, looper)
        requestFailure?.let { throw it }
    }

    @Implementation
    override fun removeUpdates(listener: LocationListener) {
        removalAttempts++
        removalFailure?.let { throw it }
        super.removeUpdates(listener)
    }
}
