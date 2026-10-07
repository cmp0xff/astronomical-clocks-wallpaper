# Dependency and artwork provenance

All application source and the placeholder Canvas/vector dial are original project work under
[Apache-2.0](../LICENSE). No political city catalog, location library, or external artwork is bundled. The
astronomy engine and the star catalogue arrived with #4, and the IANA tzdb anchor coordinates arrived with #21,
as recorded below.

| Input | Source | License / use |
| --- | --- | --- |
| Astronomy Engine (Kotlin/JVM) commit `61dc070` | [cosinekitty/astronomy](https://github.com/cosinekitty/astronomy/tree/61dc07020aaa6885d2c7f688a4d82beaf6edb9ef), tag `v2.1.19`, built on demand by [JitPack](https://jitpack.io/#cosinekitty/astronomy) | MIT; runtime, pinned to a full commit SHA |
| Hipparcos bright-star catalogue (V < 1.65, 26 stars) | [ESA 1997, CDS I/239/hip_main](https://cdsarc.cds.unistra.fr/viz-bin/cat/I/239) via VizieR | Public astronomical data; transcribed into `StarCatalog.kt` |
| IANA Time Zone Database (tzdb `zone.tab`), release `2026e` | [IANA Time Zone Database](https://data.iana.org/time-zones/tz-link.html); [release tarball](https://data.iana.org/time-zones/releases/tzdata2026e.tar.gz) | Public domain; coordinate anchors transcribed into `TimeZoneLookup.kt` |
| Kotlin standard library 2.4.10 | [JetBrains Kotlin](https://github.com/JetBrains/kotlin/tree/v2.4.10) | Apache-2.0; runtime |
| JetBrains annotations 13.0 (transitive) | [java-annotations](https://github.com/JetBrains/java-annotations) | Apache-2.0; Kotlin's annotation dependency |
| Android framework API | [Android Open Source Project](https://source.android.com/) | Device-provided framework; SDK governed by Android SDK terms |
| Gradle wrapper 9.6.1 | [Gradle](https://github.com/gradle/gradle/tree/v9.6.1) | Apache-2.0; generated scripts/JAR retained, build only |
| Android Gradle Plugin 9.3.2 | [Android tools](https://android.googlesource.com/platform/tools/base/) | Apache-2.0; build only |
| detekt 2.0.0-alpha.6 | [detekt](https://github.com/detekt/detekt/tree/v2.0.0-alpha.6) | Apache-2.0; analysis only |
| ktlint 1.8.0 | [ktlint](https://github.com/pinterest/ktlint/tree/1.8.0) | MIT; analysis/formatting only |
| JUnit 4.13.2 | [JUnit 4](https://github.com/junit-team/junit4/tree/r4.13.2) | EPL-1.0; tests only |
| Robolectric 4.17 | [Robolectric](https://github.com/robolectric/robolectric/tree/robolectric-4.17) | MIT; tests only |
| Hamcrest (JUnit transitive dependency) | [Hamcrest](https://github.com/hamcrest/JavaHamcrest) | BSD-3-Clause; tests only |
| Eclipse Temurin 21.0.12.1+1 | [Adoptium](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.12.1%2B1); macOS via the [`temurin@21` Homebrew cask](https://formulae.brew.sh/cask/temurin%4021) | GPL-2.0 with Classpath Exception; build/test JDK only |

The tzdb anchor data was diffed against the published releases rather than taken on trust. Every one
of the 418 zones in release `2026e`'s `zone.tab` is transcribed into `TimeZoneLookup.kt` and no
transcribed identifier is absent from that table; release `2025b`'s `zone.tab` agrees zone-for-zone
with the same table. Every transcribed coordinate is within 4.44e-05 degrees of the published value,
which is the four-decimal rounding the transcription uses. Verified tarballs: release `2026e`
`sha256 b26882805f26aac59d5b222978e6580484b834ccdc98be89df2f05a6dc53a652`, release `2025b`
`sha256 11810413345fc7805017e27ea9fa4885fd74cd61b2911711ad038f5d28d71474`.

Six of those identifiers post-date the tzdb an API 26 device can carry — `America/Ciudad_Juarez`,
`America/Coyhaique`, `America/Nuuk`, `Asia/Qostanay`, `Europe/Kyiv`, and `Pacific/Kanton` are all
absent from tzdata `2017a`'s zone definitions, while every identifier `TimeZoneLookup.ZONE_ALIASES`
maps them to is present there. That map and the 2017a check are what keep a device with an old tzdb
from failing to save; `TimeZoneLookupTest` exercises the lookup with that old tzdb simulated.

Resolved dependency graphs can be inspected with `./gradlew :app:dependencies` and
`./gradlew :app:dependencyInsight --configuration debugRuntimeClasspath --dependency kotlin-stdlib`.
The detekt wrapper shades ktlint; the pinned upstream
[version catalog](https://github.com/detekt/detekt/blob/v2.0.0-alpha.6/gradle/libs.versions.toml)
records ktlint 1.8.0. Upstream artifacts retain their embedded notices. Review the full resolved graph and packaging
notices again when adding runtime dependencies or preparing distribution in #7/#8.

The Astronomy Engine artifact does not embed its MIT notice. The full
[upstream license at the pinned revision](https://github.com/cosinekitty/astronomy/blob/61dc07020aaa6885d2c7f688a4d82beaf6edb9ef/LICENSE)
is retained unchanged in
[`app/src/main/assets/licenses/astronomy-engine-LICENSE.txt`](../app/src/main/assets/licenses/astronomy-engine-LICENSE.txt),
including Don Cross's copyright notice. Android packages it as
`assets/licenses/astronomy-engine-LICENSE.txt`; `scripts/verify-apk.sh` requires the APK copy
to match the source asset byte for byte.

## Astronomy Engine maintenance assessment

Checked 2026-09-09 against the GitHub API for
[cosinekitty/astronomy](https://github.com/cosinekitty/astronomy):

- Not archived, not disabled, MIT licensed.
- Last release `v2.1.19` (2023-12-14); last commit on `master` 2025-01-27; 36 open issues.
- Upstream author publicly active on other repositories as of 2026-09-07 — dormant, not retired.
- Kotlin/JVM is distributed through [JitPack](https://jitpack.io/#cosinekitty/astronomy)
  (build-on-demand from the GitHub repo), not Maven Central.

Mitigation already required by [AGENTS.md](../AGENTS.md) and #4: astronomy calculations sit
behind a small calculation interface with no Android imports, and the engine is pinned by
version or source revision. Because Astronomy Engine is MIT, its Kotlin source can be vendored
at a pinned revision if JitPack or upstream becomes unavailable — preferred over switching
engines, since the JVM/Kotlin astronomical-calculation field is otherwise thin.

Rechecked 2026-09-29 for #4, immediately before integration: still not archived, still MIT, still at
`v2.1.19` (2023-12-14) with its last commit on `master` on 2025-01-27 and 36 open issues. That
counts issues only; GitHub's `open_issues_count`, which the earlier check did not distinguish,
also counts the 12 open pull requests and so reads 48. The dormancy is unchanged, so the
mitigation stands as written. Upstream is **not** vendored: the Kotlin/JVM artifact resolves from
JitPack, pinned to commit `61dc07020aaa6885d2c7f688a4d82beaf6edb9ef`, which is what the annotated
tag `v2.1.19` dereferences to. JitPack builds a revision once and caches the result per SHA, so the
coordinate — not a version range — is the pin. The JitPack build for that SHA is already `ok`.

Its only direct transitive dependency is `kotlin-stdlib-jdk8`. Conflict resolution raises that from
the 1.6.10 the engine asks for to 1.8.0, the version constrained by this project's declared
`kotlin-stdlib:2.4.10`; `jdk8` in turn pulls `kotlin-stdlib-jdk7`, and both forward to
`kotlin-stdlib`, which resolves to 2.4.10 as declared. The `jdk7` and `jdk8` artifacts have been
empty since Kotlin 1.8.0 — each resolved 1.8.0 jar holds under a kilobyte of class data across five
entries — so `kotlin-stdlib:2.4.10` is the only Kotlin runtime on the classpath.
`./gradlew :app:dependencyInsight --configuration debugRuntimeClasspath --dependency
kotlin-stdlib-jdk8` prints the chain, and `unzip -l` on the two jars under
`~/.gradle/caches/modules-2/files-2.1/org.jetbrains.kotlin/` shows the sizes.

Vendoring remains the fallback if JitPack or upstream disappears: MIT permits it, and the Kotlin
source at that revision is a single 10,674-line file. That count is taken at the pinned SHA, so it
is a property of the pin rather than of upstream's current state, and it does not drift. Prefer
vendoring over switching engines.

## Which Hipparcos stars are bundled, and how to reproduce the list

The bundled catalogue is the result of this query, run 2026-09-29: 27 rows, less Alpha Centauri B
(HIP 71681, about fifteen arcseconds from Rigil Kentaurus, so one naked-eye point and two labels on
one spot of the dial) — the 26 stars in `StarCatalog.kt`. The query's `Vmag=<1.65` is VizieR's
strictly-less-than constraint, matching the `magnitude < 1.65` the code enforces; it is what
excludes Elnath (HIP 25428), whose `Vmag` is exactly 1.65. The magnitudes and catalogue columns in
`StarCatalog.kt` are transcribed from that result:

```sh
curl -s -G "https://vizier.cds.unistra.fr/viz-bin/asu-tsv" \
  --data-urlencode "-source=I/239/hip_main" \
  --data-urlencode "-out=HIP,RAICRS,DEICRS,_RA.icrs,_DE.icrs,pmRA,pmDE,Vmag" \
  --data-urlencode "Vmag=<1.65" --data-urlencode "-sort=Vmag"
```

- `_RA.icrs` and `_DE.icrs` are the J2000 place with proper motion applied; these become
  `rightAscensionDeg` and `declinationDeg`.
- `RAICRS` and `DEICRS` are the same stars at the catalogue's own epoch, J1991.25. They are not
  bundled — they are the test fixture that pins the proper-motion arithmetic; see
  [astronomy/calculations.md](astronomy/calculations.md).
- `pmRA` is `mu_alpha * cos(delta)` and `pmDE` is `mu_delta`, in milliarcseconds per year.
- Proper names are the IAU-approved names, cross-checked against
  [SIMBAD](https://simbad.cds.unistra.fr) identifiers.

The result was checked against the committed rows on 2026-09-29: all 26 of them match this query
row for row on right ascension, declination, both proper motions, and magnitude, and the only row
the query returns that the code does not carry is HIP 71681. That check needs the network, so it
is not part of the build; re-running the query is what repeats it.

Nothing about the catalogue is secret, and nothing about it is restrictively licensed: it is
published astronomical data, reproduced here as 26 rows of numbers rather than as a bundled file
with its own notice. The [CDS VizieR terms](https://cds.unistra.fr/vizier-org/licences_vizier.html)
are satisfied by attribution, which this section and the `StarCatalog.kt` header — which names CDS,
VizieR, and the `I/239/hip_main` table — provide. Re-check them before distribution in #7 and #8.
