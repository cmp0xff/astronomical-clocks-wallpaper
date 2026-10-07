# Astronomical Clocks Wallpaper Documentation Hub

This directory contains architectural specifications, mathematical and astronomical references, development policies, and verification records for the Astronomical Clocks Wallpaper project.

The documentation is organized following the [Diátaxis framework](https://diataxis.fr/) into distinct documentation roles:

```mermaid
graph TD
    DocsHub["docs/README.md<br/>Documentation Hub"]
    Design["Product & Design<br/>Architecture & Invariants"]
    Astronomy["Astronomy & Mathematics<br/>Projection Geometry & Limits"]
    Dev["Toolchain & Standards<br/>Build, Linting & Hygiene"]
    Testing["Testing & Verification<br/>Procedures & Historical Logs"]
    
    DocsHub --> Design
    DocsHub --> Astronomy
    DocsHub --> Dev
    DocsHub --> Testing

    Design --> D1["design.md<br/>Observing-site contract"]

    Astronomy --> A1["astronomy/README.md<br/>Astronomy reference hub"]
    Astronomy --> A2["orloj.md<br/>Dial geometry & projection"]

    Dev --> V1["development.md<br/>Toolchain & check policies"]
    Dev --> V2["dependencies.md<br/>Licenses & provenance"]

    Testing --> T1["device-testing.md<br/>Living guide & test matrix"]
    Testing --> T2["testing/reports/*<br/>Modular verification reports"]
```

---

## 1. Product & Architecture

Documents defining the product contract, requirements, and domain invariants:

- [**Product Design & Observing-Site Contract**](design.md) (`design.md`):
  Authoritative product scope, offline autonomy principles, and the single-instant / observing-site contract (one geographic site anchoring both civil time and sky projections).

---

## 2. Astronomy & Mathematics Reference

Authoritative mathematical specifications, coordinate frames, and projection geometry:

- [**Astronomy & Mathematics Reference**](astronomy/README.md) (`astronomy/README.md`):
  Ephemeris calculations, Astronomy Engine integration, coordinate systems (ecliptic coordinates of date, apparent coordinates), Delta-T models, and calculation bounds.
- [**Orloj Dial Geometry & Astrolabe Mathematics**](orloj.md) (`orloj.md`):
  Stereographic astrolabe projection mathematics, northern and southern hemisphere dial plates, horizon curves, unequal daylight hour arcs, 24-hour civil scale, zodiac ring eccentric construction, and palette contrast rules.

---

## 3. Toolchain & Engineering Standards

Environment configuration, pinned toolchain versions, and dependency governance:

- [**Development Setup, Checking Policy & Artifacts**](development.md) (`development.md`):
  Pinned toolchain (JDK, Gradle, AGP, Kotlin, Android SDK), strict checking policy (`allWarningsAsErrors = true`, detekt, ktlint, Android Lint), justified rule exceptions, and APK verification.
- [**Dependency Hygiene, Licenses & Provenance**](dependencies.md) (`dependencies.md`):
  Complete provenance and licensing records for external dependencies (Astronomy Engine), license compatibility requirements, and dependency hygiene rules.

---

## 4. Testing & Verification

Reusable procedures, diagnostic commands, acceptance test matrices, and historical verification evidence:

- [**Physical-Device Testing Guide**](device-testing.md) (`device-testing.md`):
  Living canonical guide covering device privacy policies, ADB wireless and USB connections, install and launch procedures, state diagnostic commands, lifecycle state transitions, virtual time manipulation (`DEBUG_SET_TIME`), automated smoke testing (`scripts/device_smoke.py`), and the categorized Standard Acceptance Test Matrix.
- [**Verification Reports Directory**](testing/reports/) (`testing/reports/`):
  Dated historical verification reports, discoverable through the directory listing. New reports
  belong in their own PR and do not require an entry in this hub or the living device guide.
