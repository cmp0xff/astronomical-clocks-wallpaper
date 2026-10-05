package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.app.Activity
import android.app.WallpaperManager
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import java.math.BigDecimal
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.text.NumberFormat
import java.text.ParsePosition
import java.time.DateTimeException
import java.time.ZoneId
import java.util.Locale
import kotlin.math.roundToLong

/** Opens Android's preview and manages the observing location. */
class SettingsActivity : Activity() {
    private val locationStore by lazy { LocationStore(applicationContext) }
    private val locationProvider by lazy { LocationProvider(applicationContext) }
    private val locationPermissionControls by lazy { LocationPermissionControls(this) }
    private val locationCurrent by lazy { findViewById<TextView>(R.id.location_current) }
    private val latitudeInput by lazy { findViewById<EditText>(R.id.latitude_input) }
    private val longitudeInput by lazy { findViewById<EditText>(R.id.longitude_input) }
    private val selectTimezoneButton by lazy { findViewById<Button>(R.id.select_timezone) }
    private var isForceFreshPending = false
    private var manualZone: ZoneId? = null
    private var isManualZoneExplicit = false

    override fun onCreate(savedInstanceState: Bundle?) {
        applyAppearanceTheme()
        super.onCreate(savedInstanceState)
        isForceFreshPending = savedInstanceState?.getBoolean(STATE_FORCE_FRESH_PENDING) == true
        setContentView(R.layout.activity_settings)
        // The layout's inputType filter drops the locale decimal separator; see CoordinateKeyListener.
        latitudeInput.keyListener = CoordinateKeyListener(latitudeInput.textLocale)
        longitudeInput.keyListener = CoordinateKeyListener(longitudeInput.textLocale)
        findViewById<Button>(R.id.open_preview).setOnClickListener { openWallpaperPreview() }
        findViewById<Button>(
            R.id.use_current_location,
        ).setOnClickListener { requestCurrentLocation(forceFresh = false) }
        findViewById<Button>(R.id.refresh_location).setOnClickListener { requestCurrentLocation(forceFresh = true) }
        findViewById<Button>(R.id.save_location).setOnClickListener { saveManualLocation() }
        selectTimezoneButton.setOnClickListener {
            val current = manualZone ?: locationStore.load()?.zoneId ?: ZoneId.systemDefault()
            showTimeZonePickerDialog(currentZone = current) { chosen ->
                manualZone = chosen
                isManualZoneExplicit = true
                updateTimeZoneButtonText(chosen)
            }
        }
        val location = locationStore.load()
        displayLocation(location, seedInputs = savedInstanceState == null)
        if (savedInstanceState != null) {
            val restoredZone =
                savedInstanceState.getString(STATE_MANUAL_ZONE)?.let { stored ->
                    try {
                        ZoneId.of(stored)
                    } catch (_: DateTimeException) {
                        Log.w(TAG, "ignoring unreadable restored timezone $stored")
                        null
                    }
                }
            if (restoredZone != null) {
                manualZone = restoredZone
                isManualZoneExplicit = savedInstanceState.getBoolean(STATE_MANUAL_ZONE_EXPLICIT, false)
                updateTimeZoneButtonText(restoredZone)
            }
        }
        setupCoordinateTimezoneWatcher(
            latitudeInput = latitudeInput,
            longitudeInput = longitudeInput,
            parseCoordinate = ::parseCoordinate,
            onSuggestedZone = { suggested ->
                if (!isManualZoneExplicit) {
                    manualZone = suggested
                    updateTimeZoneButtonText(suggested)
                }
            },
        )
        bindDialLayers(hasLocation = location != null)
        bindAppearanceControls()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean(STATE_FORCE_FRESH_PENDING, isForceFreshPending)
        manualZone?.let { outState.putString(STATE_MANUAL_ZONE, it.id) }
        outState.putBoolean(STATE_MANUAL_ZONE_EXPLICIT, isManualZoneExplicit)
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
        locationPermissionControls.dismissDialog()
        locationProvider.cancel()
        super.onDestroy()
    }

    private fun openWallpaperPreview() {
        val intent = Intent(WallpaperManager.ACTION_CHANGE_LIVE_WALLPAPER)
        intent.putExtra(
            WallpaperManager.EXTRA_LIVE_WALLPAPER_COMPONENT,
            ComponentName(this, AstronomicalClocksWallpaperService::class.java),
        )
        try {
            startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(this, R.string.preview_unavailable, Toast.LENGTH_LONG).show()
        }
    }

    private fun requestCurrentLocation(forceFresh: Boolean) {
        val hasPermission =
            checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
        if (!hasPermission) {
            isForceFreshPending = false
            locationPermissionControls.request {
                isForceFreshPending = forceFresh
                requestPermissions(arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION), REQUEST_LOCATION_PERMISSION)
            }
            return
        }
        locationPermissionControls.clearDenial()
        fetchCurrentLocation(forceFresh)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_LOCATION_PERMISSION) {
            return
        }
        val permissionIndex = permissions.indexOf(Manifest.permission.ACCESS_COARSE_LOCATION)
        // A non-empty callback for another permission can arrive under this request code. Reading
        // the -1 index as a null result would misreport it as an interruption and consume the
        // freshness of a still-live request. Ignore it and leave the pending state alone; an empty
        // result is different and falls through to the interrupted report below.
        if (permissionIndex == -1 && permissions.isNotEmpty()) {
            Log.i(TAG, "ignoring location permission result for unrelated permissions")
            return
        }
        val result = grantResults.getOrNull(permissionIndex)
        if (result == PackageManager.PERMISSION_GRANTED) {
            locationPermissionControls.clearDenial()
            fetchCurrentLocation(isForceFreshPending)
        } else if (result == PackageManager.PERMISSION_DENIED) {
            locationPermissionControls.recordDenial()
        } else {
            Log.i(TAG, "location permission request interrupted; no acquisition started")
            Toast.makeText(this, R.string.location_permission_interrupted, Toast.LENGTH_LONG).show()
        }
        isForceFreshPending = false
    }

    private fun fetchCurrentLocation(forceFresh: Boolean) {
        locationProvider.fetch(forceFresh = forceFresh) { fix ->
            if (fix != null) {
                // An acquired fix has no explicit choice behind it, so its zone is always an
                // estimate from the lookup and goes through the confirmation dialog.
                val estimate = TimeZoneLookup.lookup(latitude = fix.latitude, longitude = fix.longitude)
                confirmEstimatedZone(estimated = estimate) { zoneId ->
                    val location =
                        ObservingLocation(
                            latitude = fix.latitude,
                            longitude = fix.longitude,
                            source = ObservingLocation.Source.CURRENT_COARSE,
                            zoneId = zoneId,
                        )
                    locationStore.save(location)
                    displayLocation(location, seedInputs = true)
                }
            } else {
                // Preserve the previous selection; prompt for manual entry.
                Toast.makeText(this, R.string.location_fetch_failed, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun saveManualLocation() {
        val latitude = parseCoordinate(latitudeInput)
        val longitude = parseCoordinate(longitudeInput)
        val isLatitudeValid = latitude != null && ObservingLocation.isValidLatitude(latitude)
        val isLongitudeValid = longitude != null && ObservingLocation.isValidLongitude(longitude)
        if (!isLatitudeValid || !isLongitudeValid) {
            Toast.makeText(this, R.string.location_invalid, Toast.LENGTH_LONG).show()
            return
        }
        locationProvider.cancel()
        val zoneId = manualZone ?: TimeZoneLookup.lookup(latitude, longitude)
        val stored = locationStore.load()
        val isUnchanged =
            stored != null &&
                latitude == stored.latitude &&
                longitude == stored.longitude &&
                zoneId == stored.zoneId
        if (isUnchanged) {
            Toast.makeText(this, R.string.location_unchanged, Toast.LENGTH_SHORT).show()
            return
        }
        // A local rather than a member function: SettingsActivity is already at detekt's
        // 11-function limit, and this write is only reached from here.
        val writeZone = { confirmed: ZoneId ->
            val location =
                ObservingLocation(
                    latitude = latitude,
                    longitude = longitude,
                    source = ObservingLocation.Source.MANUAL,
                    zoneId = confirmed,
                )
            locationStore.save(location)
            displayLocation(location)
            Toast.makeText(this, R.string.location_saved, Toast.LENGTH_SHORT).show()
        }
        if (isManualZoneExplicit) {
            writeZone(zoneId)
        } else {
            // The zone came from the lookup, not a choice, so confirm it before it is stored as
            // the site's authoritative civil time.
            confirmEstimatedZone(estimated = zoneId, onZoneConfirmed = writeZone)
        }
    }

    private fun parseCoordinate(input: EditText): Double? {
        val text = input.text.toString().trim()
        val locale = input.textLocale
        val decimal = DecimalFormatSymbols.getInstance(locale).decimalSeparator
        // Coordinates are written with '.' in every locale; accept it as an alias for the locale
        // separator so a German comma-decimal keyboard and a coordinate-style dot both parse.
        val normalized = text.replace(oldChar = '.', newChar = decimal)
        val format = NumberFormat.getNumberInstance(locale)
        format.isGroupingUsed = false
        // DecimalFormat omits the positive sign by default, but the signed input field accepts it.
        if (format is DecimalFormat && normalized.startsWith("+")) {
            format.positivePrefix = "+"
        }
        val position = ParsePosition(0)
        val number = format.parse(normalized, position)
        return if (position.index == normalized.length) number?.toDouble() else null
    }

    private fun displayLocation(location: ObservingLocation?, seedInputs: Boolean = false) {
        locationCurrent.text =
            if (location == null) {
                getString(R.string.location_unset)
            } else {
                formatLocation(location)
            }
        if (seedInputs && location != null) {
            latitudeInput.setText(formatSeedCoordinate(location.latitude))
            longitudeInput.setText(formatSeedCoordinate(location.longitude))
        }
        if (location != null) {
            manualZone = location.zoneId
            isManualZoneExplicit = false
            updateTimeZoneButtonText(location.zoneId)
        } else {
            val initial = manualZone ?: ZoneId.systemDefault()
            updateTimeZoneButtonText(initial)
        }
        updateDialLayersAvailability(hasLocation = location != null)
    }

    private fun formatLocation(location: ObservingLocation): String {
        val source =
            when (location.source) {
                ObservingLocation.Source.CURRENT_COARSE -> getString(R.string.location_current_source)
                ObservingLocation.Source.MANUAL -> getString(R.string.location_manual_source)
            }
        return getString(
            R.string.location_details,
            formatCoordinate(location.latitude),
            formatCoordinate(location.longitude),
            source,
            location.zoneId.id,
        )
    }

    private companion object {
        const val TAG = "SettingsActivity"
        const val REQUEST_LOCATION_PERMISSION = 1
        const val STATE_FORCE_FRESH_PENDING = "force_fresh_pending"
        const val STATE_MANUAL_ZONE = "manual_zone"
        const val STATE_MANUAL_ZONE_EXPLICIT = "manual_zone_explicit"
        const val COORDINATE_SCALE = 10_000.0

        // Four decimals is about 11 m, and '.' is used in every locale because a coordinate is
        // not a locale-formatted quantity. Rounding before formatting keeps a value that rounds
        // to zero from rendering as "-0.0000".
        fun formatCoordinate(value: Double): String {
            val rounded = (value * COORDINATE_SCALE).roundToLong() / COORDINATE_SCALE
            return String.format(Locale.ROOT, "%.4f", if (rounded == 0.0) 0.0 else rounded)
        }

        // Lossless plain decimal, never scientific notation. Negative zero seeds as "0.0":
        // -0.0 and 0.0 name the same place, and BigDecimal drops the sign.
        fun formatSeedCoordinate(value: Double): String = BigDecimal.valueOf(value).toPlainString()
    }
}
