package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.app.Activity
import android.app.WallpaperManager
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import java.math.BigDecimal
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.text.NumberFormat
import java.text.ParsePosition
import java.time.ZoneId
import java.util.Locale
import kotlin.math.roundToLong

/** Opens Android's preview and manages the observing location. */
class SettingsActivity : Activity() {
    private val locationStore by lazy { LocationStore(applicationContext) }
    private val locationProvider by lazy { LocationProvider(applicationContext) }
    private val locationCurrent by lazy { findViewById<TextView>(R.id.location_current) }
    private val latitudeInput by lazy { findViewById<EditText>(R.id.latitude_input) }
    private val longitudeInput by lazy { findViewById<EditText>(R.id.longitude_input) }
    private var isForceFreshPending = false

    override fun onCreate(savedInstanceState: Bundle?) {
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
        val location = locationStore.load()
        displayLocation(location, seedInputs = savedInstanceState == null)
        bindDialLayers(hasLocation = location != null)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean(STATE_FORCE_FRESH_PENDING, isForceFreshPending)
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
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
            isForceFreshPending = forceFresh
            requestPermissions(arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION), REQUEST_LOCATION_PERMISSION)
            return
        }
        fetchCurrentLocation(forceFresh)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_LOCATION_PERMISSION) {
            return
        }
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            fetchCurrentLocation(isForceFreshPending)
        } else {
            Toast.makeText(this, R.string.location_permission_denied, Toast.LENGTH_LONG).show()
        }
        isForceFreshPending = false
    }

    private fun fetchCurrentLocation(forceFresh: Boolean) {
        locationProvider.fetch(forceFresh = forceFresh) { fix ->
            if (fix != null) {
                // A refresh updates the coordinates; an already-saved site keeps its geographic
                // zone, which no current-location input can resolve. The phone zone is the fallback
                // for a first acquisition, when there is no site to preserve.
                val location =
                    ObservingLocation(
                        latitude = fix.latitude,
                        longitude = fix.longitude,
                        source = ObservingLocation.Source.CURRENT_COARSE,
                        zoneId = locationStore.load(repair = false)?.zoneId ?: ZoneId.systemDefault(),
                    )
                locationStore.save(location)
                displayLocation(location, seedInputs = true)
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
        val stored = locationStore.load(repair = false)
        val isUnchanged =
            stored != null &&
                latitude == stored.latitude &&
                longitude == stored.longitude
        if (isUnchanged) {
            Toast.makeText(this, R.string.location_unchanged, Toast.LENGTH_SHORT).show()
            return
        }
        val location =
            ObservingLocation(
                latitude = latitude,
                longitude = longitude,
                source = ObservingLocation.Source.MANUAL,
                zoneId = ZoneId.systemDefault(),
            )
        locationStore.save(location)
        displayLocation(location)
        Toast.makeText(this, R.string.location_saved, Toast.LENGTH_SHORT).show()
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
        const val REQUEST_LOCATION_PERMISSION = 1
        const val STATE_FORCE_FRESH_PENDING = "force_fresh_pending"
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
