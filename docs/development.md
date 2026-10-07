# Android development

## Pinned toolchain

| Component | Version |
| --- | --- |
| Eclipse Temurin JDK | 21.0.12.1+1 (HotSpot); installed via the `temurin@21` Homebrew cask on macOS and the checksum-verified archive on Linux/CI |
| Gradle wrapper | 9.6.1 |
| Android Gradle Plugin (AGP) | 9.3.2 |
| Kotlin compiler and standard library | 2.4.10 |
| detekt plugin, engine, ktlint wrapper | 2.0.0-alpha.6 |
| Wrapped ktlint | 1.8.0 |
| Android compile/target SDK | API 37 (`platforms;android-37.0`, revision 2) |
| Android Build Tools | 36.0.0 |
| Android platform-tools | 37.0.1 |
| Android command-line tools | 23.0, archive build 16111833 |
| JUnit | 4.13.2 |
| Robolectric | 4.17 |

The versions align with [detekt's tested toolchain](https://detekt.dev/docs/introduction/compatibility/),
with the subsequent [AGP 9.3.2 patch](https://developer.android.com/build/releases/agp-9-3-0-release-notes).
The detekt prerelease is an accepted development dependency; it is not packaged into the APK.
AGP supplies built-in Kotlin integration. The build explicitly pins the Kotlin Gradle plugin dependency
and compiler classpath instead of applying the separate Kotlin Android plugin. Gradle's embedded Kotlin
for build scripts is independent of the application's compiler; `./gradlew --version` records it.
Java and Kotlin produce Java 17 bytecode while Gradle runs on JDK 21.

API 26 is a conservative initial minimum, not a device compatibility claim. API 37 is the compile/target
level supported by AGP 9.3. Physical-device behavior and firmware qualification are recorded in
[device-testing.md](device-testing.md).

## Local setup

Android Studio is optional. Install Homebrew on macOS, plus the pinned JDK, `curl`, `unzip`, `shasum`, and Python 3.
On Apple Silicon macOS, install the Temurin JDK via Homebrew:

```sh
brew install --cask temurin@21
export JAVA_HOME="$(/usr/libexec/java_home -v 21)"
unset ANDROID_SDK_ROOT
export ANDROID_HOME="$HOME/Library/Android/sdk"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
```

The cask installs system-wide under `/Library/Java/JavaVirtualMachines/temurin-21.jdk` and prompts for
admin rights. `java_home -v 21` filters by version only, so any other 21.x JVM on the machine also
matches; confirm the active JDK with `"$JAVA_HOME/bin/java" -version` and remove or repoint any older
install.

Use the matching Linux x64 archive from the
[Temurin release](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.12.1%2B1)
on Linux, verify its published SHA-256, and set `JAVA_HOME` to the extracted JDK and `ANDROID_HOME` to
`$HOME/Android/Sdk`. CI downloads and checksum-verifies the exact Linux JDK archive; setup-java does not
accept this release's four-part version string. The `temurin@21` cask checksums the build its formula
pins, so macOS integrity is still verified, but that pin advances with Adoptium's 21.x releases and may
diverge from this archive. The checksum-verified archive that CI installs remains the reproducible-build
reference.
Keep these exports in your own shell configuration; do not commit local SDK paths.

From the checkout:

```sh
scripts/setup-android-sdk.sh
java -version
./gradlew --version
./gradlew qualityGate :app:assembleDebug
scripts/verify-apk.sh
```

The setup script supports Apple Silicon macOS and Linux x64. It verifies the command-line archive's
SHA-256 and installs API 37.0, Build Tools 36.0.0, and platform-tools in `ANDROID_HOME`. Other platforms
can install command-line tools 23.0 manually and run the same package installation. The Android CLI
may present SDK license terms; review and accept them to install the SDK. Downloads and Gradle caches
are outside maintained source. Initial builds require network access; the app itself works offline.

## Checking policy

| Python role | Kotlin / Android equivalent | Enforcement |
| --- | --- | --- |
| mypy / Pyright | Kotlin compiler | Mandatory type checking for production and tests |
| Strict type/warning options | Kotlin compiler | `allWarningsAsErrors`, `-Wextra`, strict Java nullability, full unused-return-value checking |
| Ruff / pylint correctness rules | detekt | All rules, upstream defaults, strict config validation, fail on warnings |
| Framework/platform analysis | Android Lint | All warnings including normally disabled checks, test sources, warnings as errors |
| Black / Ruff formatting | ktlint through detekt | Official style, all optional wrapped rules, four spaces, 120 columns |
| pytest | JUnit + Robolectric | Activity behavior, manifest discovery, and service lifecycle |

`./gradlew qualityGate` compiles and tests debug and release variants, runs type-resolved
`detektDebug`, `detektRelease`, `detektDebugUnitTest`, and `detektReleaseUnitTest`, checks all maintained
Kotlin files and Gradle scripts through root detekt, and runs `lintDebug` and `lintRelease`.
`./gradlew check` includes the same gate. Gradle Kotlin DSL warnings and Gradle deprecation warnings
also fail the build. Generated build output, caches, and downloaded SDK sources are excluded.

`./gradlew formatKotlin` is the explicit formatting command. The quality gate and CI never reformat.
There are no baselines or blanket suppressions. Fix findings first; for a demonstrated false positive
or incompatible rules, record the rule ID, concrete example, reason, and narrow scope below and in the
PR. Removing a finding by lowering global severity or excluding production/test directories is not a fix.

The host-only ADB harnesses in `scripts/` are standard-library Python. [`pyproject.toml`](../pyproject.toml)
configures their checks, and CI runs all four commands before the Android build:

```sh
uvx --from ruff==0.16.10 ruff check --output-format=github scripts
uvx --from ruff==0.16.10 ruff format --check --output-format=github scripts
uvx --from ty==0.0.84 ty check --output-format=github scripts
python3 -I -m unittest discover -s scripts -p 'test_*.py' -v
```

Ruff runs `select = ["ALL"]` on stable rules at a 120-column line length, and ty promotes every rule to
an error and fails on a warning. Both are pinned to an exact version, and both target Python 3.12,
which is what the runner's `python3` resolves to. The checks cover `scripts/` only: the unit tests
exercise parsing and decision helpers, and neither they nor the static checks connect to a device or
establish Android lifecycle, rendering, battery, or CPU behavior.

These are analysis tools, not dependencies of the scripts: nothing is imported from them, there is
nothing to resolve or lock, and the Gradle build does not read `pyproject.toml`. Every rule the
configuration relaxes is recorded below.

### Host-tooling decisions

A few choices here are deliberate and have been questioned, so they are recorded rather than left to
be re-derived.

**The 120-column width.** `line-length` in [`pyproject.toml`](../pyproject.toml) is the only place
Ruff's width is declared, and it agrees with `.editorconfig`, the detekt config, and the 120 columns
stated above. Dropping it falls back to Ruff's default of 88 and reformats all maintained Python files away from
the width the rest of the repository is written to.

**No `__init__.py` in `scripts/`.** `scripts/` is a scripts directory, not a distribution package:
it also holds `setup-android-sdk.sh` and `verify-apk.sh`, the harnesses are run as programs, and the
test module imports them from the same directory that `unittest discover` puts on `sys.path`. A
package would add no capability and would make one file importable as both `device_qualification`
and `scripts.device_qualification` — two module objects with two sets of constants, so the suite's
patch targets could silently address the wrong one. The `INP001` exception below records the
mechanism.

The smoke and qualification suites are separate; synthetic RGBA fixtures live in
`scripts/device_test_fixtures.py`. The qualification suite checks the duplicated constants
only once both harnesses are present. None of this adds production shared-device helpers.

**Standard-library `unittest`, not pytest.** pytest runs this `unittest.TestCase` suite
without changes, so a switch would cost no rewrite, and it could be pinned like Ruff and ty
without a lockfile. What it would not buy is rule cleanup: `PT009`, `PT019`, and `PT027` fire on the
`assertEqual`/`assertRaises` calls and the positionally-injected `unittest.mock.patch` parameters
under any runner, so all three ignores would stay while their rationale above became false. It would
also invert the standard-library-only decision recorded in [`pyproject.toml`](../pyproject.toml) and
here, which the dependency rules in [AGENTS.md](../AGENTS.md) subject to owner approval and provenance
recording, and it would need plugin autoload disabled to preserve CI's `python3 -I` hermeticity. That
is a separate change with that cost list, not part of this tooling.

**`main()` in what looks like a test file.** `scripts/device_smoke_test.py` is a harness, not a test
module: `main()` plus `if __name__ == "__main__"` is its command-line entry point, and it defines no
name pytest would collect. Its `*_test.py` suffix does match pytest's default collection pattern
while CI collects only `test_*.py`, but collection over `scripts/` collects only the host suite,
because the harness defines no `test_*` name and has no import-time side effects. The suffix is a
latent smell, not a defect; the rename is a precondition of any pytest move and is folded into #107
rather than repeated here after #103 renamed these files once already.

**`serial` passed explicitly, not a class per harness.** `serial` threads through the two harnesses
because it is the identity every ADB call needs; it is a symptom of the duplication *between* the two
harnesses rather than of missing classes. A class per harness would fork the device abstraction twice
and make the deferred merge harder, and the pure helpers take no `serial` at all. #107 records the
planned shape: one shared device object that owns `serial` as constructor state, absorbs the helpers
the two harnesses duplicate today, and keeps their deliberately different restore policies explicit.
Nothing in this pull request changes as a result of that issue.

## Rule exceptions

| Rule ID | Example and reason | Scope |
| --- | --- | --- |
| detekt `UnnecessaryInnerClass` | `ClockEngine : WallpaperService.Engine()` needs its enclosing service because the superclass is a Java non-static inner class. detekt does not recognize that implicit outer-instance use. | Only `ClockEngine`, annotated in source |
| Kotlin `DEPRECATION` | The preview test reads a `ComponentName` with the legacy `getParcelableExtra` overload because the typed overload is unavailable on its API 26 test environment. | Only the local test value reading this extra |
| Kotlin `DEPRECATION` | `LocationManager.requestSingleUpdate` is the single-update API available since API 9; its API 30 replacement `getCurrentLocation` is unavailable on devices at the API 26 minimum. | Only the `LocationProvider.requestSingleUpdate` helper, annotated in source |
| Kotlin `OVERRIDE_DEPRECATION` | `LocationListener.onStatusChanged` is required on API 26 but deprecated on newer releases; status notifications need no action for this single fix. The required enabled/disabled callbacks are not deprecated; disabling completes the request with a logged failure. | Only the `LocationProvider` listener's `onStatusChanged` override, annotated in source |
| Lint `MissingPermission` | `LocationManager.getLastKnownLocation` / `requestSingleUpdate` run only after `fetch` has confirmed `ACCESS_COARSE_LOCATION` at runtime; lint cannot see the helper-method guard. | Only the two `LocationProvider` methods, annotated in source |
| Lint `SetTextI18n` | The settings tests type literal coordinates or a literal timezone-filter query into `EditText` fields; they are test inputs, not user-facing text. | Only the test helpers that type such literals, in `SettingsActivityTest`, `SettingsActivityLocaleTest`, `SettingsActivityAcquisitionTest`, `SettingsActivityManualTimezoneTest`, and `SettingsActivityTimezoneTest`, annotated in source |
| Kotlin `DEPRECATION` | The provider tests use Robolectric's deprecated `ShadowLocationManager.setLastKnownLocation` to seed exact cached timestamps, including missing or future timestamps. | Only `LocationProviderCacheTest.seedCache`, annotated in source |
| Kotlin `DEPRECATION` | Robolectric's deprecated `getLocationUpdateListeners` has no replacement exposing registered listeners. Race tests must retain a listener to simulate callbacks already queued before cancellation and verify registration cleanup. | Only the test helper `ShadowLocationManager.networkListeners`, annotated in source |
| Lint `Range` | The malformed-fix test intentionally injects a `NaN` latitude into a platform `Location` to verify rejection and request cleanup. | Only `LocationProviderLifecycleTest.malformedFixFailsOnce`, annotated in source |
| Lint `QueryPermissionsNeeded` | `queryIntentServices` in the manifest test restricts the query to its own package, which is always visible. Adding external package queries would misstate app needs. | Only `wallpaperDeclaration` test method |
| Lint `UnsupportedChromeOsHardware` | `android.software.live_wallpaper` is required because wallpaper rendering is the app's core feature; devices lacking it cannot provide that feature. | Only that manifest `uses-feature` element |
| Lint `AndroidGradlePluginVersion` | Lint suggests Gradle 9.7.1 over 9.6.1. The explicit 9.6.1 pin follows the selected detekt compatibility family; network-discovered upgrade suggestions must not change this bootstrap's agreed toolchain. | Only `gradle/wrapper/gradle-wrapper.properties`, via `app/lint.xml` |
| Lint `NewerVersionAvailable` | The check live-queries Maven Central on every run, so it errors the hermetic gate the moment a dependency ships a newer release (Robolectric 4.16.1 → 4.17 did exactly this). Upgrades are reviewed deliberately instead of on CI's clock. | All modules, via `app/lint.xml` |
| Lint `GradleDependency` | Same network-discovered-upgrade category as `NewerVersionAvailable`; keeping it active would reintroduce the same non-hermetic failure. | All modules, via `app/lint.xml` |
| detekt `TooGenericExceptionCaught` | `runTick` catches `Exception` to keep the wallpaper tick loop alive across unexpected drawing exceptions while letting VM `Error` propagate. | Only `ClockEngine.runTick`, annotated in source |
| detekt `TooGenericExceptionCaught` | `dialGeometryOrNull` catches `RuntimeException` around `dialGeometry` to fall back to the 24-hour civil dial rather than blanking the frame on geometry calculation failures. | Only `ClockEngine.dialGeometryOrNull`, annotated in source |
| Lint `UnspecifiedRegisterReceiverFlag` | `registerDebugReceiver` calls the 2-argument `registerReceiver` on API < 33 when `RECEIVER_EXPORTED` is unavailable; lint requires annotating the API 33+ branch guard. | Only `AstronomicalClocksWallpaperService.registerDebugReceiver`, annotated in source |
| detekt `TooManyFunctions` | `ClockEngine` is a `WallpaperService.Engine` that carries the four platform lifecycle overrides, whose surface is fixed by the platform, plus the tick-loop and drawing helpers, including #85's `stopTicking`; it already sat at the per-class function budget. The appearance feature adds one more callback, `onConfigurationChanged`, which the enclosing service invokes rather than the platform, and that addition is what takes the class past the budget. Splitting the engine to satisfy the count would separate drawing from the lifecycle that drives it. | Only `AstronomicalClocksWallpaperService.ClockEngine`, annotated in source |
| Ruff formatter-conflict set (`W191`, `E111`, `E114`, `E117`, `D203`, `D206`, `D300`, `Q000`–`Q004`, `COM812`, `COM819`) | Ruff documents these as conflicting with its formatter wherever the formatter is the authority on layout; the formatter owns indentation, quote style, docstring indentation, and trailing commas, so the lint rule and the format step cannot both hold. | Ruff config, `scripts/` |
| Ruff `D212` | Multi-line docstring summary on the first line. Conflicts with `D213`, which requires the second line; the docstrings in `scripts/` use the `D213` layout, so exactly one of the pair can be enabled. | Ruff config, `scripts/` |
| Ruff `T201` | Both harnesses print their report to stdout, and that output *is* the deliverable — the device report is assembled from it. A standard-library logger would add machinery without improving the tabular report. | `scripts/device_qualification.py`, `scripts/device_smoke_test.py` |
| Ruff `INP001` | `scripts/` deliberately has no `__init__.py`: the harnesses are run as scripts, and the test modules import them from the same directory, which `unittest discover` puts on `sys.path`. | Every file in `scripts/` |
| Ruff `D102`, `D103` | Test methods and helpers in the suite are described by their names and their docstrings, not by a summary line restating the name. | `scripts/test_device_smoke.py`, `scripts/test_device_qualification.py` |
| Ruff `PT009`, `PT019`, `PT027` | These are flake8-pytest-style rules, and the suite is standard-library `unittest` because host tooling may not add a dependency. `PT009` and `PT027` want `assertEqual`/`assertRaises` replaced with bare `assert` and `pytest.raises`, which would cost the assertion diffs; `PT019` reads the `unittest.mock.patch` parameters, which are injected positionally, as pytest fixtures. | `scripts/test_device_smoke.py`, `scripts/test_device_qualification.py` |
| Ruff `S603` | `run_adb` is the one `subprocess.run` call. It executes the developer's own `adb` from `PATH` with an argv built from literals and parsed device output, `check=True`, and no shell, so there is no untrusted input to validate. `S607` is not raised because the executable is not written at the call site. | `run_adb` in both harnesses, annotated in source |
| Ruff `CPY001` (via `notice-rgx`) | The rule looks for a copyright line; this repository marks licensing with an SPDX identifier instead, and the Kotlin sources, the shell scripts, and the maintained Python files all use that form. | Ruff config, `scripts/` |

Upstream defaults remain the starting point, including per-rule defaults for test documentation and
magic numbers. Tests are still compiled with the same strict compiler and analyzed with type resolution;
no entire test or production source directory is excluded. Dependency and artwork provenance is in
[dependencies.md](dependencies.md).

See [2026-09-07-bootstrap.md](testing/reports/2026-09-07-bootstrap.md) for the local positive and negative checks.

## Tests and artifacts

Current-location permission is requested only from **Use current location** or
**Refresh location**. A requestable denial gets a cancellable explanation on retry;
a non-empty coarse-permission denial with no Android rationale records a private
recovery observation in `location_permission` preferences, separate from the saved
site, while a still-promptable denial stores nothing. An empty/interrupted callback
reports the interruption rather than a denial and records nothing, and a callback
carrying only unrelated permissions is ignored without consuming the pending
cache-or-fresh policy. A later explicit retry offers **Open app settings**, **Try
permission again**, or Cancel. The observation is not an authoritative OS flag:
grants and requestable rationale clear it, a malformed value is repaired on the
first tap, and the explicit permission retry handles otherwise ambiguous permission
resets. A first request is never blocked just because Android reports no rationale.
An open rationale or recovery dialog is dismissed when the activity is destroyed.

Permission denial and settings navigation never replace the saved site, block
manual coordinates, or acquire location automatically on return. After changing
permission in system settings, tap the desired location action again; Use current
location retains its cache policy, while Refresh location requests a fresh fix.
An unavailable app-settings activity is logged and reported with a manual-entry
fallback. This follows Android's
[runtime-permission guidance](https://developer.android.com/training/permissions/requesting)
without adding a permission library or background-location permission.

Robolectric tests use API 26 and API 36 environments. They are JVM simulations and do not establish
physical-device, lit lock-screen, or actual wallpaper surface behavior. API 37 compilation and Android
Lint additionally check against the selected target. The `ClockEngine` schedules one redraw per whole
second while visible and cancels the pending tick when hidden or destroyed. Each engine caches
its saved location and dial-layer choices, listens for preference changes until destruction, and uses the new snapshot
on its next visible frame. Each frame resolves civil time from one clock instant in the saved
zone, falling back to the current phone zone only when no usable location is saved.

The location preference now holds one version-1 JSON record containing latitude, longitude,
source, and zone ID. `LocationStore.load()` is a pure read that performs no disk writes; one-time
startup migration of valid legacy flat records and repair of missing or invalid zones in supported
records is owned explicitly by `AstronomicalClocksApplication` at process startup. Malformed
records and unsupported versions are logged and left untouched until the user explicitly saves
a replacement. Both current-location acquisition and manual coordinate entry resolve the site's
geographic timezone offline via nearest-anchor lookup against public-domain IANA tzdb reference
points (`TimeZoneLookup`), independent of the phone's system timezone. Manual coordinate entry
additionally provides an explicit timezone picker dialog with a filter box, so a user can type to
narrow the hundreds of offered entries and inspect or override the geographic timezone (#21, #24);
only a listed zone can be chosen, and a query that matches none shows a no-match message and commits
nothing. The anchors are zone representatives rather than boundaries, so
an inferred zone can be wrong near a border; a zone that came from the lookup rather than from an
explicit pick is presented as an estimate and must be confirmed before it is saved as the site's
civil time. Once saved, subsequent changes to the phone's system timezone alter neither the saved
site nor its civil clock.

The astronomy tests need no Robolectric environment: `AstronomyCalculator` and everything under it
are free of Android types, so they run as plain JUnit against published USNO, JPL Horizons, and
Hipparcos reference data. They need no network and no device. See [astronomy/calculations.md](astronomy/calculations.md) for
the frames, tolerances, and what remains unverified.

The debug APK is `app/build/outputs/apk/debug/app-debug.apk`, with application ID
`io.github.godaniya.astronomicalclockswallpaper.debug`. `scripts/verify-apk.sh` checks its ID, SDK metadata,
wallpaper declaration, that the only requested permission is `ACCESS_COARSE_LOCATION`, the debug flag
and signature, and the complete bundled Astronomy Engine license, then records SHA-256.
The stable release ID is `io.github.godaniya.astronomicalclockswallpaper`; release signing belongs to #7.
Debug signing keys are disposable and local/CI APKs may require uninstalling the previous debug app.

GitHub Actions runs on pull requests and pushes to `main`. Actions use immutable commit references,
and the job has only `contents: read`. Open the **Android quality gate** run and download
`debug-apk-<source revision>` or `check-reports-<source revision>`. The PR run checks GitHub's merge
revision, recorded in the artifact name and `toolchain.txt`. Check reports upload even on failure;
the APK uploads only after a successful gate and APK verification. No release credentials are used.

Install a downloaded debug APK with `adb install -r app-debug.apk`, open **Astro Clocks**, and
tap **Open wallpaper preview**. See [device-testing.md](device-testing.md) for the physical-device
procedure and [2026-09-28-feat-2-device-feasibility.md](testing/reports/2026-09-28-feat-2-device-feasibility.md) for the #2 acceptance results.
