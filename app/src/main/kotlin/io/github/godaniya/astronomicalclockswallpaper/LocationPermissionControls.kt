package io.github.godaniya.astronomicalclockswallpaper

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log
import android.widget.Toast

/** Offers contextual permission recovery without changing the saved site or acquiring location. */
internal class LocationPermissionControls(private val activity: Activity) {
    private val preferences = activity.getSharedPreferences("location_permission", Context.MODE_PRIVATE)
    private var visibleDialog: AlertDialog? = null

    fun request(onRequest: () -> Unit) {
        var observation = preferences.all[KEY_NON_PROMPTABLE_DENIAL]
        if (observation != null && observation !is Boolean) {
            // Repair the malformed key once instead of warning about it on every tap.
            Log.w(TAG, "ignoring malformed permission denial observation")
            clearDenial()
            observation = null
        }
        when {
            activity.shouldShowRequestPermissionRationale(Manifest.permission.ACCESS_COARSE_LOCATION) -> {
                clearDenial()
                showTracked(
                    AlertDialog
                        .Builder(activity)
                        .setTitle(R.string.location_permission_title)
                        .setMessage(R.string.location_permission_rationale)
                        .setPositiveButton(R.string.location_permission_continue) { _, _ -> onRequest() }
                        .setNegativeButton(android.R.string.cancel, null)
                        .show(),
                )
            }

            observation == true -> {
                showRecovery(onRequest)
            }

            else -> {
                onRequest()
            }
        }
    }

    fun clearDenial() {
        preferences.edit().remove(KEY_NON_PROMPTABLE_DENIAL).apply()
    }

    /** Only call for a matched, non-empty denied permission result, not cancellation. */
    fun recordDenial() {
        val isNonPromptable =
            !activity.shouldShowRequestPermissionRationale(Manifest.permission.ACCESS_COARSE_LOCATION)
        if (isNonPromptable) {
            preferences.edit().putBoolean(KEY_NON_PROMPTABLE_DENIAL, true).apply()
        } else {
            // A still-promptable denial must not leave an inert `false` behind, so a later read
            // cannot mistake the key for an observation.
            clearDenial()
        }
        val message =
            if (isNonPromptable) R.string.location_permission_blocked else R.string.location_permission_denied
        Toast.makeText(activity, message, Toast.LENGTH_LONG).show()
    }

    /** Dismisses an open rationale or recovery dialog; call from the owning activity's destroy. */
    fun dismissDialog() {
        visibleDialog?.dismiss()
        visibleDialog = null
    }

    private fun showTracked(dialog: AlertDialog) {
        // A dialog dismisses itself on a button tap or Back; clearing the reference as it goes keeps
        // dismissDialog() off a stale instance and never leaves a shown dialog untracked.
        visibleDialog?.dismiss()
        visibleDialog = dialog
        dialog.setOnDismissListener { visibleDialog = null }
    }

    private fun showRecovery(onRequest: () -> Unit) {
        val builder = AlertDialog.Builder(activity)
        builder.setTitle(R.string.location_permission_title)
        builder.setMessage(R.string.location_permission_recovery)
        builder.setPositiveButton(R.string.location_permission_settings) { _, _ -> openAppSettings() }
        // The observation is not an OS permission flag: resets can make a new request possible.
        builder.setNeutralButton(R.string.location_permission_retry) { _, _ ->
            clearDenial()
            onRequest()
        }
        builder.setNegativeButton(android.R.string.cancel, null)
        showTracked(builder.show())
    }

    private fun openAppSettings() {
        val intent =
            Intent(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.fromParts("package", activity.packageName, null),
            )
        try {
            activity.startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "app permission settings unavailable", e)
            Toast.makeText(activity, R.string.location_permission_settings_unavailable, Toast.LENGTH_LONG).show()
        }
    }

    internal companion object {
        private const val TAG = "LocationPermissionControls"

        // Shared with tests so the observation assertions cannot drift from the production key.
        const val KEY_NON_PROMPTABLE_DENIAL = "non_promptable_denial"
    }
}
