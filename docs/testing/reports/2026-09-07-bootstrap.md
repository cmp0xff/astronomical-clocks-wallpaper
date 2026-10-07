# Bootstrap verification evidence

Local verification on 2026-09-07 used Apple Silicon macOS, Temurin 21.0.12.1+1,
Gradle 9.6.1, AGP 9.3.2, Kotlin 2.4.10, Android API 37.0 (SDK revision 2), and
Build Tools 36.0.0. See [development.md](../../development.md) for setup and commands.

The full `qualityGate` and debug APK build pass. Four behavioral tests run at both
Robolectric API 26 and API 36 for both debug and release: 16 test executions, zero
failures, errors, or skips. They cover the preview intent's action/component, activity
recreation, service discovery/metadata/binding protection, and independent engine
creation/hidden teardown. Real wallpaper surfaces and physical devices are not simulated
by these assertions.

The APK verifier confirms the `.debug` application ID, min SDK 26, target SDK 37,
debuggable flag, wallpaper intent filter/metadata, exported service guarded by
`BIND_WALLPAPER`, no requested permissions, and a valid Android debug signature.
It writes the actual APK checksum and signing certificate fingerprint to build reports.
Checksums vary with the local/CI debug key; no durable release signature is claimed.

## Negative checks

Each probe was introduced temporarily, run through its intended task, and removed.
Each task exited nonzero with the diagnostic below. Clean sources were restored before
rerunning the complete gate. No probe or baseline is part of the application.

| Probe | Task | Observed diagnostic |
| --- | --- | --- |
| Return a string from a function declared to return `Int` | `:app:compileDebugKotlin` | `RETURN_TYPE_MISMATCH` |
| Unused local variable in production code | `:app:compileDebugKotlin` | `UNUSED_VARIABLE`, warnings treated as errors |
| Unused local variable in test code | `:app:compileDebugUnitTestKotlin` | `UNUSED_VARIABLE`, warnings treated as errors |
| Ignore an application function's return value | `:app:compileDebugKotlin` | `RETURN_VALUE_NOT_USED` |
| `values.filter { it > 1 }.isEmpty()` in production | `:app:detektDebug` | `UnnecessaryFilter` (requires analysis of types/symbols) |
| Same filter expression in tests | `:app:detektDebugUnitTest` | `UnnecessaryFilter` |
| Missing spaces around `=` in a Kotlin source file | Root `detekt` | `SpacingAroundOperators` |
| Missing spaces around `=` in a Gradle script | Root `detekt` | `SpacingAroundOperators` |
| Hardcoded button text in an Android layout | `:app:lintDebug` | `HardcodedText`, warning promoted to error |
| Remove `inner` from `ClockEngine` | `:app:compileDebugKotlin` | `INACCESSIBLE_OUTER_CLASS_RECEIVER`, substantiating the detekt exception |

During initial configuration, deprecated AGP Lint report properties also failed Gradle
Kotlin DSL compilation with warnings-as-errors. The deprecated configuration was removed;
AGP now always generates these reports.

A fresh detached worktree also passes the documented setup, gate, APK build, and
verification with no project outputs or local configuration copied in (shared SDK and
Gradle download caches only). The draft PR records CI run/artifact links. Physical-device
testing, lit lock-screen behavior, wake/surface/process recovery, moving clock,
and battery qualification remain unrun acceptance work for #2.
