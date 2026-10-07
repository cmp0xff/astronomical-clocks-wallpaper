# Zodiac compartments and vernal-equinox star verification (#43)

Test build: local debug `app-debug.apk` from `feat/43-zodiac-compartments` at 2f4660f (APK
SHA-256 460349fbd0c91c78fe83407cfd3c13fc956a3ae17da0a840f57c8fee717ccaaf), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The star is drawn at `eclipticPoint(0.0)`, which is `equatorRadius` from the dial centre, and it
shares that point with the divider between the ARI and PIS compartments. It is 0.04 sky radii across
against a 0.0075 divider stroke, so on this surface the star is about 13.6 px wide where a divider is
2.5 px, but both are `DialStyle.GOLD` and neither a screenshot nor the eye separates them by colour.
The star is therefore measured as the extra gold at one boundary relative to another: a 7 px disc is
centred on the sidereal-implied vernal equinox, which carries the star, and on the autumn equinox
180 degrees away, which carries only a divider. Both are on the equator circle, and the band's gold
rim starts 12.7 px from the ring centreline, so the rim is outside the disc. The expected bearing is
the local sidereal angle, Greenwich apparent sidereal time plus east longitude, computed from the
device clock and the saved site rather than read back from the renderer.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | build + install | `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` passed at the revision above; `adb install -r` replaced the previous build in place and the existing wallpaper binding re-rendered without being reapplied |
| 2026-10-03 | compartments, Prague | Twelve sign labels (ARI through PIS) around the ring, each centred in its own compartment, with a gold radial divider on every boundary between them |
| 2026-10-03 | star against a divider, Prague | 72 gold px in the 7 px disc at the vernal equinox against 33 px at the autumn equinox, 180 degrees away on the same circle, so the star is drawn rather than only the divider |
| 2026-10-03 | star bearing, Prague | Gold centroid bearing 340.514 deg against 340.549 deg implied by local sidereal time, 0.035 deg apart |
| 2026-10-03 | compartments, Sydney | Twelve labels each centred in its compartment with a divider on every boundary, at the placement the southern projection gives for -33.86785, 151.20732 |
| 2026-10-03 | star against a divider, Sydney | 71 gold px at the vernal equinox against 34 px at the autumn equinox, so the southern projection draws the star on the same terms |
| 2026-10-03 | star bearing, Sydney | Gold centroid bearing 117.979 deg against 117.994 deg implied by local sidereal time, 0.015 deg apart |
| 2026-10-03 | star follows sidereal time, Sydney | Re-captured 26 min later at the same site: the measured bearing moved from 117.979 to 124.456 deg while local sidereal time advanced from 117.994 to 124.416 deg, and that capture again read 69 px against 35 px at the autumn equinox |

Both sites were entered through the settings app's coordinate fields and Save button, not from the
device's own position, and the wallpaper binding stayed applied throughout: the app was never
force-stopped, so the captures above are of the same running process that the install replaced. The
device was left on the saved Prague coordinates after the pass; manual entry changes the site's
provenance from current location to manual, which only the display label reflects, and Use current
location restores it. This pass re-checked the ring and the star only: the cadence, surface
recreation, lock-screen, and location rows recorded for the preceding revisions were not re-run
against this build. The two compartment rows describe the dividers as they were drawn before the
reorientation recorded in the next subsection.

## Reoriented zodiac dividers (#43)

Test build: local debug `app-debug.apk` from `feat/43-zodiac-compartments` at d231ce8 (APK
SHA-256 2ab46dcaf5dfe8ddb9d5ce29c2a640fc252002b8c4f37c9aaa5cd21a60ecd922), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The dividers now run along rays from the dial centre rather than along the zodiac ring's own
radius, so a check that assumed the ring radius would no longer describe them. Frames come from
`adb exec-out screencap`. The dial centre is located from the gold hand hub and the scale from the
outer gold rim, both read off the frame rather than assumed. The projected boundary points, the
offset ring centre, and the local sidereal angle all come from Greenwich apparent sidereal time
plus the site's east longitude, computed from the device clock and the saved site, not read back
from the renderer: the 0 degree Aries star placed by that independent geometry lands within 0.3 px
of the drawn star at both sites, which validates the frame before any divider is fitted. Each
stroke's direction is then the principal axis of the `DialStyle.GOLD` pixels that lie inside the
night band within 5 px of its predicted ray; the two gold rim arcs sit 14 px out and are excluded,
because including them pulled the fitted line toward the ring's radius. The tilt is reported both
against the ring's own radius at the boundary and against the dial-centre ray, and the equinox
counts repeat the previous pass's 7 px disc.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | build + install | `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` passed at the revision above; `adb install -r` replaced the previous build in place and the existing wallpaper binding re-rendered without being reapplied |
| 2026-10-03 | dividers point at the pivot, Prague | Twelve gold strokes, one on every boundary and spanning the band: each fitted line passes 0.1-4.1 px from the dial centre and lies 0.0-1.1 deg from the ray from the centre through its boundary point |
| 2026-10-03 | obliquity of the tilt, Prague | Departure from the ring's own radius is 0.6 deg at the 0 deg Cancer boundary and 0.5 deg at 0 deg Capricorn (coincidence, at the fit's resolution), 12.0-12.5 deg at the four boundaries at 60/120/240/300, 20.3-21.0 deg at 30/150/210/330, and 22.9 deg at 0 deg Aries and 24.5 deg at 0 deg Libra, the two equinox boundaries, against the 23.44 deg true obliquity |
| 2026-10-03 | clip to the night band, Prague | Along each stroke's ray the gold run starts and ends within 2 px of the ring's inner and outer rim circles and is continuous across the band; no stroke gold lies beyond them, the further gold found within 20 px of the band belonging to the equator and sky-boundary circles, recognised by its radius |
| 2026-10-03 | compartments stay closed, Prague | The interior of all twelve compartments, 5 deg of ecliptic longitude past each boundary on the ring centreline, reads `DialStyle.NIGHT` |
| 2026-10-03 | star against a divider, Prague | 68 gold px in the 7 px disc at the vernal equinox against 34 px at the autumn equinox 180 degrees away, so the star still adds gold at a boundary that carries a reoriented divider |
| 2026-10-03 | dividers point at the pivot, Sydney | At -33.86785, 151.20732 the same twelve strokes pass 0.1-2.5 px from the centre and 0.0-0.6 deg from their rays |
| 2026-10-03 | obliquity of the tilt, Sydney | 0.5 deg at 0 deg Cancer and 0.3 deg at 0 deg Capricorn, 12.2-12.5 deg at 60/120/240/300, 20.5-20.8 deg at 30/150/210/330, and 24.0 deg at both equinox boundaries, so the southern plate tilts its dividers on the same terms |
| 2026-10-03 | star against a divider, Sydney | 68 gold px at the vernal equinox against 37 px at the autumn equinox |

Both sites were again entered through the settings app's coordinate fields and Save, and the app was
not force-stopped: the install's process restart brought up the new build, and the same running
process produced every capture. The device was left on the saved Prague coordinates, byte-identical
to those recorded before the pass. Only the ring's dividers were re-measured; the sign labels, the
compartments, the star, and the cadence, lifecycle, and location rows from the earlier passes were
not re-run against this build.
