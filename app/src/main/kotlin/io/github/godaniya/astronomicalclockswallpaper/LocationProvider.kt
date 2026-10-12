package io.github.godaniya.astronomicalclockswallpaper

import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log

/**
 * Fetches a single approximate coordinate fix with a bounded timeout.
 *
 * Call [fetch] and [cancel] on the main thread. Immediate results invoke the callback synchronously
 * from [fetch]; fresh updates and timeouts are dispatched through the main looper.
 * Cached fixes are accepted up to five minutes old; forceFresh always requests a new fix.
 */
internal class LocationProvider(
    private val context: Context,
    private val timeoutMillis: Long = DEFAULT_TIMEOUT_MILLIS,
) {
    private val handler = Handler(Looper.getMainLooper())
    private val locationManager = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private var activeListener: LocationListener? = null
    private var activeTimeout: Runnable? = null
    private var requestGeneration = 0

    fun fetch(forceFresh: Boolean = false, callback: (LocationResult) -> Unit) {
        cancel()
        if (!hasCoarsePermission()) {
            Log.w(TAG, "location fetch skipped: ACCESS_COARSE_LOCATION not granted")
            callback(LocationResult.Failure(LocationFailureReason.PERMISSION_DENIED))
            return
        }
        if (NETWORK_PROVIDER !in locationManager.allProviders) {
            Log.w(TAG, "network location provider unavailable")
            callback(LocationResult.Failure(LocationFailureReason.PROVIDER_UNAVAILABLE))
        } else {
            fetchAvailableLocation(forceFresh, callback)
        }
    }

    /** Cancels any in-flight request so a finished setup does not deliver a late callback. */
    fun cancel() {
        requestGeneration++
        activeTimeout?.let { handler.removeCallbacks(it) }
        val listener = activeListener
        activeListener = null
        activeTimeout = null
        if (listener != null) {
            try {
                locationManager.removeUpdates(listener)
            } catch (e: SecurityException) {
                Log.w(TAG, "removing location updates denied", e)
            }
        }
    }

    private fun hasCoarsePermission(): Boolean =
        context.checkSelfPermission(ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    private fun fetchAvailableLocation(forceFresh: Boolean, callback: (LocationResult) -> Unit) {
        val cached = if (forceFresh) null else lastKnownLocation()
        when {
            cached != null -> {
                callback(LocationResult.Success(cached))
            }

            !locationManager.isProviderEnabled(NETWORK_PROVIDER) -> {
                Log.w(TAG, "network location provider disabled")
                callback(LocationResult.Failure(LocationFailureReason.PROVIDER_DISABLED))
            }

            else -> {
                requestFreshLocation(callback)
            }
        }
    }

    // Only reached after hasCoarsePermission() passed in fetch().
    @SuppressLint("MissingPermission")
    private fun lastKnownLocation(): CoordinateFix? {
        val location =
            try {
                locationManager.getLastKnownLocation(NETWORK_PROVIDER)
            } catch (e: SecurityException) {
                Log.w(TAG, "getLastKnownLocation denied", e)
                null
            } catch (e: IllegalArgumentException) {
                Log.w(TAG, "cached network location unavailable", e)
                null
            }
        return location?.takeIf { isRecent(it) }?.toCoordinateFix()
    }

    private fun isRecent(location: Location): Boolean {
        val timestamp = location.elapsedRealtimeNanos
        val age = SystemClock.elapsedRealtimeNanos() - timestamp
        val isRecent = timestamp > 0 && age in 0..MAX_CACHE_AGE_NANOS
        if (!isRecent) {
            Log.d(TAG, "cached network location discarded: stale or invalid elapsed timestamp")
        }
        return isRecent
    }

    private fun requestFreshLocation(callback: (LocationResult) -> Unit) {
        val generation = requestGeneration
        val complete = { result: LocationResult -> completeRequest(generation, result, callback) }
        val listener =
            object : LocationListener {
                override fun onLocationChanged(location: Location) {
                    if (generation == requestGeneration) {
                        val fix = location.toCoordinateFix()
                        if (fix != null) {
                            complete(LocationResult.Success(fix))
                        } else {
                            complete(LocationResult.Failure(LocationFailureReason.FETCH_FAILED))
                        }
                    }
                }

                // Implement every callback for API 26; newer releases provide default methods.
                override fun onProviderDisabled(provider: String) {
                    if (generation == requestGeneration) {
                        Log.w(TAG, "network location provider disabled during acquisition")
                        complete(LocationResult.Failure(LocationFailureReason.PROVIDER_DISABLED))
                    }
                }

                override fun onProviderEnabled(provider: String) = Unit

                @Suppress("OVERRIDE_DEPRECATION")
                override fun onStatusChanged(provider: String, status: Int, extras: Bundle?) = Unit
            }
        activeListener = listener
        val timeout =
            Runnable {
                if (generation == requestGeneration) {
                    Log.w(TAG, "network location update timed out after ${timeoutMillis}ms")
                    complete(LocationResult.Failure(LocationFailureReason.TIMEOUT))
                }
            }
        activeTimeout = timeout
        handler.postDelayed(timeout, timeoutMillis)
        try {
            requestSingleUpdate(NETWORK_PROVIDER, listener)
        } catch (e: SecurityException) {
            Log.w(TAG, "requestSingleUpdate denied", e)
            complete(LocationResult.Failure(LocationFailureReason.PERMISSION_DENIED))
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "network location request unavailable", e)
            complete(LocationResult.Failure(LocationFailureReason.PROVIDER_UNAVAILABLE))
        }
    }

    private fun completeRequest(generation: Int, result: LocationResult, callback: (LocationResult) -> Unit) {
        if (generation == requestGeneration) {
            cancel()
            callback(result)
        }
    }

    // requestSingleUpdate is the single-update API available since API 9; the API 30 replacement
    // (getCurrentLocation) is unavailable on devices at the API 26 minimum. See docs/development.md.
    // Only reached after hasCoarsePermission() passed in fetch().
    @SuppressLint("MissingPermission")
    @Suppress("DEPRECATION")
    private fun requestSingleUpdate(provider: String, listener: LocationListener) {
        locationManager.requestSingleUpdate(provider, listener, Looper.getMainLooper())
    }

    private companion object {
        const val ACCESS_COARSE_LOCATION = "android.permission.ACCESS_COARSE_LOCATION"
        const val DEFAULT_TIMEOUT_MILLIS = 10_000L
        const val MAX_CACHE_AGE_NANOS = 300_000_000_000L

        // The network provider supports coarse permission across the API 26+ compatibility range.
        val NETWORK_PROVIDER = LocationManager.NETWORK_PROVIDER
        private const val TAG = "LocationProvider"
    }
}

internal fun Location.toCoordinateFix(): CoordinateFix? {
    if (!ObservingLocation.isValidLatitude(latitude) || !ObservingLocation.isValidLongitude(longitude)) {
        Log.w("LocationProvider", "network location discarded: invalid coordinates")
        return null
    }
    return CoordinateFix(latitude = latitude, longitude = longitude)
}

internal enum class LocationFailureReason {
    PERMISSION_DENIED,
    PROVIDER_UNAVAILABLE,
    PROVIDER_DISABLED,
    TIMEOUT,
    FETCH_FAILED,
}

internal sealed interface LocationResult {
    data class Success(val fix: CoordinateFix) : LocationResult

    data class Failure(val reason: LocationFailureReason) : LocationResult
}
