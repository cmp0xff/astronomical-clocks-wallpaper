# Unchanged Save provenance and timezone retention verification (#42)

The changes in `fix/42-unchanged-save-provenance` prevent an untouched **Save coordinates** action
from silently converting a `CURRENT_COARSE` site to `MANUAL` and overwriting its preserved geographic
timezone with the phone's system default timezone. At the tested revision recorded below,
`LocationStore.load(repair = false)` provided a pure read so that checking coordinates never
triggered a repair write. That historical API was replaced in #35: current callers use `load()`,
which never writes preferences. `AstronomicalClocksApplication.onCreate()` owns
`migrateAndRepair()` once per process startup, migrating valid legacy records and repairing
missing or invalid timezones in otherwise usable supported records. Malformed and unsupported
records remain untouched.

Host verification covers these paths across API 26 and 36 via Robolectric:
- `SettingsActivityTimezoneTest`: verifies that after acquiring a location on a site with a distinct
  geographic timezone, tapping Save coordinates untouched preserves `CURRENT_COARSE` and its timezone;
  verifies that cold-opening on a saved `CURRENT_COARSE` site and tapping Save coordinates without
  edits preserves `CURRENT_COARSE` and its timezone; and verifies that edited coordinates still switch
  to `MANUAL` and capture the device timezone.
- `SettingsActivityAcquisitionTest`: verifies refresh-then-save preserves `CURRENT_COARSE` and cancels
  in-flight location acquisition.
- `SettingsActivityTest`: verifies that an unchanged Save produces zero SharedPreferences writes after
  startup repairs an invalid timezone, shows the distinct "Coordinates unchanged; nothing to save."
  feedback, and verifies that a stored `-0.0` coordinate does not take the edited branch.
- `LocationStoreTest`: at the tested revision, verified that `load(repair = false)` did not persist
  repairs. Current pure-read and explicit maintenance coverage lives in `LocationStoreMigrationTest`
  and `LocationStoreCorruptionTest`; `AstronomicalClocksApplicationTest` covers startup ownership.

**Physical-device status.** On 2026-10-03, debug APK
`aba41ceec2fd69cddd899cb10e0fc8f48477e3c5b90b97b3c25f4ff7c2095c8c`
(source revision `02f5acb03788de650ebae7b30f1b7795319b46be`) was installed and exercised on a
connected Android 16 physical device. An existing `CURRENT_COARSE` site retained `(current)` and
`Europe/Prague` after an untouched Save and force-stop/relaunch; the Save visibly showed
"Coordinates unchanged; nothing to save." No new device location was requested. Earlier public
Prague-coordinate checks also covered manual Save, edited manual Save, and relaunch persistence.

**Editorial note (2026-10-07).** The filename date now reflects the first recorded verification
date, 2026-10-03. Branch/revision references describe the original feature work. Run dates,
measurements, and APK/source attribution are unchanged; no new measurements were made.
