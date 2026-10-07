# Permission recovery verification (#3)

Test build: local debug `app-debug.apk` from `fix/3-location-permission-recovery`.
The first full pass (2026-10-05) ran the rebased recovery change and the follow-up
that reports interrupted permission results distinctly (7aafc6d, APK SHA-256
fd0c466e3dbb8c191ca0d0e390bf964221d9a1b3afb46fc6577c20231d9ca66b). The focused
re-check (2026-10-06) ran this round's permission-callback hardening (eb46c57,
APK SHA-256 2adc059ea9c4216284096c036a773052385c091f85521763d04143f777995dfd),
installed in place with `adb install -r` over the existing binding. Rows dated
2026-10-06 re-run only the paths this round changed; the rest are the earlier pass.

Android version: 16 (API 36). Firmware build: withheld (embeds the model
identifier). The saved site started and ended as the neutral manual
`50.08, 14.42`, `Europe/Prague`; the temporary Tokyo site below is a published
coordinate pair, not the device's own position, and no device location was read
or recorded.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-05 | first prompt | With coarse location revoked and its user-set flags cleared, **Use current location** opened Android's approximate-location prompt directly: no rationale-history shortcut, and no fine-location row because only coarse is requested |
| 2026-10-06 | deny once, retry | Denying showed the promptable denial toast (**Location permission denied. Enter coordinates manually.**) and stored no observation — `location_permission` held an empty map, where the earlier build left `non_promptable_denial=false`; the next tap showed the cancellable rationale (**Current location permission** / "Approximate location is used…") |
| 2026-10-05 | rationale Cancel | **Cancel** dismissed the rationale, started no activity, left **Save coordinates** and the coordinate fields enabled, and cleared the observation, as the rationale branch does |
| 2026-10-05 | rationale Continue | **Continue** issued a fresh system prompt |
| 2026-10-06 | deny to non-promptable | A second denial set the `USER_FIXED` flag, stored `non_promptable_denial=true`, and showed the blocked toast (**Android is not offering a location permission prompt…**) |
| 2026-10-05 | explicit retry | The next **Use current location** showed the recovery dialog (**Open app settings** / **Try permission again** / **Cancel**) with no automatic system request |
| 2026-10-05 | cancel recovery, manual save | **Cancel** left the saved site unchanged; entering the published Tokyo `35.6762, 139.6503` suggested **Timezone: Asia/Tokyo**, and **Save coordinates** → **Estimated timezone** → **Save** stored it as `MANUAL`/`Asia/Tokyo` without any permission |
| 2026-10-05 | open app settings, return | **Open app settings** reached the app's details page; returning with the permission still denied started no acquisition, kept manual entry usable, and left the observation intact |
| 2026-10-05 | grant, return, use | Granting coarse location (applied with `pm grant`, the state the settings toggle sets) and returning started no acquisition; the next **Use current location** acquired a fix and offered its estimated timezone, so the grant was used on demand |
| 2026-10-05 | reset flags, Try permission again | Clearing the user-set/user-fixed flags and choosing **Try permission again** cleared the observation and issued a fresh system prompt, so a reset is not a permanent blacklist |
| 2026-10-05 | recreate during a request | Rotating while the system prompt was up recreated Settings; granting then delivered the result to the recreated activity, which acquired a fix with no crash and no duplicate request |
| 2026-10-05 | reopen Settings | Reopening Settings after a blocked denial showed no launch-time prompt; the recovery dialog appeared only on an explicit location action |
| 2026-10-06 | rotate with the recovery dialog open | Rotating to landscape and back with the recovery dialog open recreated Settings — the focus window token changed at each rotation — and logged no leaked-window error, so destroy dismissed the dialog |
| 2026-10-06 | grant, next action | Granting coarse location with `pm grant` and tapping **Refresh location** acquired a fix (the estimated-timezone dialog appeared) and removed the observation key; cancelling kept the saved Prague site |

Backing out of the system prompt with the back gesture produced a **denied**
result on this device, not an empty/interrupted callback: the app recorded a
non-promptable denial and stored no interrupted observation. The interrupted
branch therefore stays host-verified by `SettingsActivityPermissionTest`
(`emptyResultIsInterrupted`); no device action produced an empty result. An
unrelated-permission callback cannot be produced by a device action either, so
that branch stays host-verified by `unrelatedGrantIsIgnored` and
`unrelatedKeepsFreshPending`, which fail if the guard is removed. The observation
bookkeeping is pinned by `malformedObservationRepaired` and
`promptableDenialLeavesNoKey`, and the destroy dismissal by
`destroyDismissesOpenDialog`, each in Robolectric on API 26 and 36.

Android 11+ treats repeated Deny as non-promptable; older versions expose an
explicit Don't ask again choice. The passes inspected the permission flags with
`adb shell dumpsys package <package>` and used Android's documented
[permission-flag reset procedure](https://developer.android.com/about/versions/11/privacy/permissions#dialog-visibility)
as optional test setup, not as an app recovery action. Every setting changed for
the passes was restored afterwards: the coarse-location permission to its granted
state with its user-set flag (the `USER_FIXED` flag from the second denial
cleared), the `location_permission` preference file removed, the saved site back
to manual Prague `50.08, 14.42`, `screen_off_pocket` and `proximity_sensor` back
to 1, `stay_on_while_plugged_in` back to 0, and `accelerometer_rotation` back to 1
with rotation at portrait. `dumpsys wallpaper` still reported the app's
`AstronomicalClocksWallpaperService` as the home binding. A device-specific
accidental-touch overlay appeared once at the start of the re-check and was
dismissed with the recorded swipe; disabling the proximity sensor stopped it
recurring. These checks do not replace lifecycle/battery
qualification under #6.
