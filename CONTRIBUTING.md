# Contributing to Astronomical Clock Wallpaper

Thank you for your interest in contributing to Astronomical Clock Wallpaper. We
welcome bug reports, physical-device testing, documentation improvements, and
code contributions that share our commitment to mathematical precision, battery
efficiency, and software quality.

This project is guided by open-source craft and reproducible engineering.
Whether you are reporting a defect or submitting code, please review these
standards.

## Ways to contribute

1. **Bug reports and defect discovery**: Report unexpected rendering, crashes, or
   lifecycle anomalies using the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
2. **Physical-device testing**: Verify live wallpaper behavior across diverse Android
   versions and screen configurations. Physical verification on real hardware is
   essential; see [Physical device testing and privacy](#physical-device-testing-and-privacy).
3. **Astronomy and dial projections**: Help verify astronomical ephemeris against
   independent reference data, particularly for southern-hemisphere and polar sites.
4. **Code and documentation contributions**: Resolve open issues through focused,
   well-tested pull requests following our [Workflow and pull requests](#workflow-and-pull-requests).

## Quality, reputation, and release principles

Astronomical Clock Wallpaper runs persistently as a system-level `WallpaperService`.
Because it operates continuously in the background of a user's daily device,
reliability and developer reputation are indivisible:

- **Stability and resource budget first**: A live wallpaper must never freeze or
  crash the Android SystemUI, run invisible drawing loops, or hold background CPU
  wake locks. Render only while visible; halt execution when obscured or asleep.
- **Milestone-governed releases**: Trunk (`main`) is an active development branch,
  not a continuous public release channel. Public distribution catalogs (such as
  F-Droid) are reserved strictly for tagged milestone releases that have satisfied
  complete physical-device qualification and acceptance criteria.
- **Inviolable quality gates**: Compiler warnings, type-resolved detekt analysis,
  ktlint formatting, and Android Lint are strictly enforced with warnings treated
  as errors. Never resolve a build issue by lowering severity, adding blanket
  suppressions, or introducing lint baseline files. Fix the root cause.
  Narrowly-scoped, justified exceptions are recorded in
  [docs/development.md](docs/development.md#rule-exceptions); preserve those
  annotations and record any new one there and in the PR.
- **Dependency and provenance integrity**: Third-party libraries, bundled assets,
  and astronomical data must be auditable, licensed compatibly with the intended
  distribution, and pinned to an exact upstream version, revision, or checksum in
  [docs/dependencies.md](docs/dependencies.md). Zero analytics or proprietary
  tracking SDKs are permitted.

## Physical device testing and privacy

Live wallpapers interact deeply with Android surface lifecycles, lit lock screens,
and OEM power management. Testing on physical devices is indispensable.

Testers may record full device identity (OEM/model) and firmware build in private
evidence, but must keep those values out of Git and public reports (see
[docs/device-testing.md](docs/device-testing.md) and Issue #20):

- **What to include in public reports**:
  - Android platform version (e.g. Android 14) and API level (e.g. API 34).
  - Surface context: preview, home screen, or lit lock screen.
  - The exact APK SHA-256 checksum or Git commit SHA.
  - Observable behavior and reproduction steps.
- **What to withhold from public reports**:
  - Device manufacturer (OEM) and hardware marketing model name.
  - Serial numbers, IMEI, or hardware identifiers.
  - Firmware build fingerprints or proprietary build strings.
  - Precise personal coordinates or identifiable home locations (use non-personal
    reference coordinates, such as a major city center, when verifying sites).

## Test scope and proportionality

Tests protect observable production behavior against regressions. We value focused,
meaningful tests over sheer volume or coverage quotas:

- Before adding a test, identify the distinct, plausible production regression it
  catches and check whether existing coverage already catches it.
- Prefer extending an existing test or adding a small table of cases over creating
  new test classes for individual implementation branches.
- Test observable production behavior at the lowest effective layer. Avoid tests of
  language guarantees, generated getters, or assertions that merely mirror the
  implementation.
- Choose meaningful boundary cases (e.g., polar latitudes, daylight-saving transitions,
  null location providers) instead of multiplying combinatorial variations.
- Preserve independent astronomical references, lifecycle transitions, and
  permission-race coverage.
- Remove obsolete or redundant tests when modifying behavior, ensuring unique
  assertions remain covered.

## Development quick start

The project uses a pinned, reproducible toolchain. Review
[docs/development.md](docs/development.md) for complete macOS/Linux setup instructions.

```sh
# Verify environment and toolchain
java -version
./gradlew --version

# Run the complete quality gate and build the debug APK
./gradlew qualityGate :app:assembleDebug

# Verify the resulting APK against manifest, permission, and license contracts
scripts/verify-apk.sh

# Reformat Kotlin code and Gradle scripts explicitly (CI never auto-formats)
./gradlew formatKotlin
```

## Workflow and pull requests

- **Issue-linked worktrees**: Develop on issue-linked branches in native-filesystem
  sibling worktrees named `astronomical-clocks-wallpaper-<issue-number>-<short-description>`.
  Keep the primary clone on `main`.
- **Conventional Commits**: Commit messages follow
  [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), for example
  `feat(dial): render zodiac constellation marks`. Link issues using native GitHub
  references (e.g. `Refs #41` or `Closes #41`).
- **Small, reviewable PRs**: Open a pull request linking the issue. Follow
  [.github/pull_request_template.md](.github/pull_request_template.md), leading with
  concise outcome bullets and an honest verification checklist.
- **Squash merge default**: The repository defaults to squash merging through the
  GitHub UI to maintain a clean history on `main`.

## AI-assisted contributions

We recognize and support the use of AI coding assistants (such as Antigravity,
Claude, or Copilot). However, automated contributions must meet the exact same
standards of craftsmanship, transparency, and accountability as human work.

Contributors using AI tools must follow [AGENTS.md](AGENTS.md) for mandatory
commit body sections (Motivation, Approach, Alternatives Considered, Verification),
`Co-Authored-By` model disclosures, reviewer credits, and PR Accountability Index
maintenance.
