package io.github.godaniya.astronomicalclockswallpaper

import android.app.Activity
import android.app.AlertDialog
import android.text.Editable
import android.text.TextWatcher
import android.util.Log
import android.view.inputmethod.EditorInfo
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.ListView
import java.time.ZoneId

private const val TAG = "TimeZoneControls"

/** Encapsulates timezone selection controls and coordinate watchers for manual location entry. */
internal fun Activity.updateTimeZoneButtonText(zoneId: ZoneId) {
    findViewById<Button>(R.id.select_timezone)?.text =
        getString(R.string.select_timezone, zoneId.id)
}

internal fun Activity.showTimeZonePickerDialog(currentZone: ZoneId, onZoneSelected: (ZoneId) -> Unit) {
    // The list is the zones this device can resolve, plus the zone in effect even when it has no
    // anchor, so the current zone is highlighted rather than replaced by the first entry.
    val zones = TimeZoneLookup.pickerZoneIds(currentZone)
    val selectedZoneId = currentZone.id
    val builder = AlertDialog.Builder(this)
    builder.setTitle(R.string.choose_timezone_title)
    builder.setView(R.layout.dialog_timezone_picker)
    builder.setNegativeButton(android.R.string.cancel, null)
    val dialog = builder.show()
    var isCommitted = false

    val filter = dialog.findViewById<EditText>(R.id.timezone_filter)
    val list = dialog.findViewById<ListView>(R.id.timezone_list)
    val adapter = ArrayAdapter(this, android.R.layout.simple_list_item_single_choice, zones.toMutableList())
    list.adapter = adapter
    list.emptyView = dialog.findViewById(R.id.timezone_empty)
    // The dialog is shown before the list is populated, so the empty view is already wired when the
    // adapter reports its first count.
    applyCheckedZone(list = list, adapter = adapter, zoneId = selectedZoneId)
    // setItemChecked stores the checked state but does not bring the row on screen. The list is
    // hundreds of rows long, so without scrolling to the selection the picker would open at
    // Africa/Abidjan and hide the zone actually in effect. selectedZoneId is always in zones
    // (pickerZoneIds appends it), so indexOf never returns -1 here.
    list.setSelection(zones.indexOf(selectedZoneId))

    // Resolving through the adapter (rather than the original list) keeps the position the row
    // reports consistent with what the filter is currently showing; resolveZone logs a rejection,
    // so an entry that stops resolving is diagnosable rather than a tap that silently does nothing.
    val commit = { position: Int ->
        isCommitted = true
        val selectedId = adapter.getItem(position)
        if (selectedId != null) {
            TimeZoneLookup.resolveZone(id = selectedId)?.let(onZoneSelected)
        }
        dialog.dismiss()
    }
    list.setOnItemClickListener { _, _, position, _ -> commit(position) }
    filter.setOnEditorActionListener { _, actionId, _ ->
        if (actionId == EditorInfo.IME_ACTION_DONE && adapter.count == 1) {
            commit(0)
            true
        } else {
            // Anything but a lone remaining match (or a different action) keeps the IME open.
            false
        }
    }
    filter.addTextChangedListener(
        object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {
                // No-op before text changed.
            }

            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                // No-op during text change.
            }

            override fun afterTextChanged(s: Editable?) {
                // Filtering the source list in place would empty it on the first keystroke, so the
                // adapter holds a copy and every query is applied to the untouched original.
                val filtered = TimeZoneLookup.filterZoneIds(ids = zones, query = s?.toString().orEmpty())
                adapter.setNotifyOnChange(false)
                adapter.clear()
                adapter.addAll(filtered)
                adapter.notifyDataSetChanged()
                adapter.setNotifyOnChange(true)
                // notifyDataSetChanged keeps the stored check state, so the highlight is re-applied
                // against the row's new position and cleared when that zone is filtered out.
                applyCheckedZone(list = list, adapter = adapter, zoneId = selectedZoneId)
            }
        },
    )
    // A picker dismissed without a tap on a row (Cancel, Back, or the activity going away) commits
    // nothing; logging it makes the estimated-zone flow's "Save that did nothing" case — Choose…,
    // then abandon the picker — diagnosable. A row tap sets isCommitted, so exactly one line reports
    // an abandonment and a completed choice logs nothing.
    dialog.setOnDismissListener {
        if (!isCommitted) {
            Log.i(TAG, "timezone picker dismissed without a selection; nothing saved")
        }
    }
}

// Internal rather than private: the anonymous TextWatcher below is a separate class, and a private
// top-level function reached from it would make the compiler insert a synthetic accessor (Lint
// SyntheticAccessor).
internal fun applyCheckedZone(list: ListView, adapter: ArrayAdapter<String>, zoneId: String) {
    val position = (0 until adapter.count).firstOrNull { adapter.getItem(it) == zoneId }
    if (position != null) {
        list.setItemChecked(position, true)
    } else {
        // clearChoices, not setItemChecked(-1, true): the latter is not bounds-checked and stores a
        // bogus key that getCheckedItemPosition would then report.
        list.clearChoices()
    }
}

/**
 * Confirms a timezone that the nearest-anchor lookup estimated rather than the user choosing it.
 *
 * The bundled anchors are reference points, not boundaries, so an estimated zone can be wrong near
 * a border; [onZoneConfirmed] receives the zone only once the user accepts the estimate or picks
 * one, and never when the dialog is cancelled.
 */
internal fun Activity.confirmEstimatedZone(estimated: ZoneId, onZoneConfirmed: (ZoneId) -> Unit) {
    var isChosen = false
    val builder = AlertDialog.Builder(this)
    builder.setTitle(R.string.estimated_timezone_title)
    builder.setMessage(getString(R.string.estimated_timezone_message, estimated.id))
    builder.setPositiveButton(R.string.estimated_timezone_save) { _, _ ->
        isChosen = true
        onZoneConfirmed(estimated)
    }
    builder.setNeutralButton(R.string.estimated_timezone_choose) { _, _ ->
        isChosen = true
        showTimeZonePickerDialog(currentZone = estimated, onZoneSelected = onZoneConfirmed)
    }
    builder.setNegativeButton(android.R.string.cancel, null)
    // A dismissal that is not one of those two buttons stores nothing, and only Cancel and the
    // activity going away reach this listener. Choose… sets isChosen before the picker opens, so
    // abandoning the picker stays quiet here; the picker's own listener reports that case. Logging
    // the Cancel case keeps a Save that visibly did nothing diagnosable instead of silent; the
    // activity going away includes a configuration change, since this Activity does not handle
    // those itself.
    builder.setOnDismissListener {
        if (!isChosen) {
            Log.i(TAG, "estimated-zone confirmation dismissed without a choice; nothing saved")
        }
    }
    builder.show()
}

internal fun setupCoordinateTimezoneWatcher(
    latitudeInput: EditText,
    longitudeInput: EditText,
    parseCoordinate: (EditText) -> Double?,
    onSuggestedZone: (ZoneId) -> Unit,
) {
    val watcher =
        object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {
                // No-op before text changed.
            }

            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                // No-op during text change.
            }

            override fun afterTextChanged(s: Editable?) {
                val lat = parseCoordinate(latitudeInput)
                val lon = parseCoordinate(longitudeInput)
                if (lat != null && lon != null && isValidCoordinatePair(latitude = lat, longitude = lon)) {
                    onSuggestedZone(TimeZoneLookup.lookup(latitude = lat, longitude = lon))
                }
            }
        }
    latitudeInput.addTextChangedListener(watcher)
    longitudeInput.addTextChangedListener(watcher)
}

internal fun isValidCoordinatePair(latitude: Double, longitude: Double): Boolean =
    ObservingLocation.isValidLatitude(latitude) && ObservingLocation.isValidLongitude(longitude)
