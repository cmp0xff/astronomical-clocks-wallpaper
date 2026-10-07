# Astronomy documentation series

This directory documents the astronomical calculations, coordinate systems, projection geometry,
and cultural nomenclature comparisons for the Astronomical Clock Wallpaper.

## Series roadmap

The astronomy documentation is organized into modular specifications:

| Document | Scope and content |
| --- | --- |
| [Calculations and engine specification](calculations.md) | The Kotlin calculation engine (`AstronomyCalculator`, `AstronomyEngineCalculator`), units, atmospheric refraction fits, Hipparcos star reduction, UTC event windows, failure behavior, and independent test tolerances against JPL Horizons, USNO, and IAU SOFA fixtures. |
| [Ecliptic frames, tropical zodiac, and solar terms](ecliptic-and-solar-terms.md) | The shared apparent-longitude mapping of tropical signs and Dingqi solar terms, historical 十二次 caveats, an attributed 月將 convention, and the implemented hemisphere projection. Fulfills [#65](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/65). |

## Related specifications

- [Orloj dial foundation](../orloj.md): Detailed Prague Orloj dial geometry, astrolabe-style stereographic projection mathematics, Sun/Moon marker conventions, and WCAG palette contrast contracts.
- [Product design](../design.md): The observing-site contract anchoring both civil time and celestial mechanics to one geographic location.
- [Dependency provenance](../dependencies.md): Licenses and pinned revisions for Astronomy Engine and Hipparcos stellar data.

## Architectural horizon (v0.4 Pluggable Clock Family)

As part of the multi-dial expansion framework ([Issue #41](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/41)),
future documentation in this series will expand into non-European historical clocks and coordinate systems,
including:
- Equatorial armillary spheres and the 28 Lunar Mansions (二十八宿).
- Historical temporal hour systems (unequal/seasonal hours, Roman vigils, and traditional Chinese *Ke* 刻 and *Geng-Dian* 更點 systems).
- Lunisolar calendrical mechanics and leap-month rules (置閏規則).
