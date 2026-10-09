# Product design

This is the intended product contract. Implementation progress and release
planning live in the [issues](https://github.com/godaniya/astronomical-clocks-wallpaper/issues)
and [milestones](https://github.com/godaniya/astronomical-clocks-wallpaper/milestones).

## Product purpose and newcomer comprehension

Bring the Prague Orloj experience beyond Old Town Square and onto everyday Android
screens. Visual appeal brings people in; astronomy and cultural interpretation give
them reasons to explore. The first dial is original artwork inspired by the Orloj
and adapted to the selected observing site. Learning to read it is a product
aspiration to validate, not a demonstrated comprehension benefit.

Validate aesthetic appreciation and comprehension separately: liking the dial does
not show that someone understands it, and understanding it does not show that they
find it appealing. For a lightweight newcomer evaluation, show people unfamiliar
with the Orloj a representative dial and ask what they find appealing or confusing.
Ask them to interpret the 24-hour civil hand, Sun marker and horizon/twilight plate,
Moon phase sphere, and zodiac ring before and after a brief explanation. Record the
material shown (including its source revision and depicted site/instant), prompts,
assistance, observed answers, and limitations. This defines a method, not results,
user demand, or a success threshold.

Distinguish **product intent** (design hypotheses, learning aspirations, and intended
outcomes) from **validated evidence**: observations from validation relevant to the
claim. Comprehension claims require user observations; device-behavior claims require
appropriate device verification; numerical-accuracy claims require independent
astronomical references, as described in the
[contributor verification guidance](../CONTRIBUTING.md#verification). None substitutes
for the others. Do not assert demand, intuitive clarity, or comprehension benefits
without supporting user evidence.

New feature and architecture proposals follow the existing
[product feature gate](../CONTRIBUTING.md#product-feature-gate), including its evidence
requirements. Routine documentation fixes do not expand that gate’s scope.

## Future possibilities

Other clock traditions and calendar representations could offer further ways to
explore the same sky: see the [clock-family roadmap](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/41)
and [solar-term proposal](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/66).
Configurable 節令／中氣 displays remain a future possibility, not a current capability.
Keep astronomical calculations distinct from cultural interpretations and calendar
conventions. New feature or architecture proposals must record the six product
feature gate answers before a proposal PR or implementation; existing roadmap
references do not establish approval or supporting evidence.

## One observing site and one instant

Prague Orloj is the visual and projection reference. A selected observing site
supplies the coordinates for astronomy and the geographic timezone for civil
time, including daylight-saving changes. Both displays use the same instant.
Changing the site updates both; changing the phone timezone does not change the
saved site or its civil clock. Before a site is selected, the civil clock uses
the phone timezone and site-dependent astronomy is hidden.

[Issue #41](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/41)
generalizes this single-dial contract into a pluggable family of astronomical
clocks; this section describes the Prague dial until that seam lands.

Adapt the Orloj geometry to the selected site's latitude and longitude, including
southern-hemisphere and polar sites. Draw original artwork. Detailed projection
mathematics belong in the maintained [Orloj geometry guide](orloj.md).

## Location selection and offline operation

The intended contract accepts approximate current location and keeps offline city
selection and coordinate entry available after denial, disabled location, failure,
or timeout. Manual selection must also be directly available, with explicit refresh
or a different site in settings. Persist the selection without continuous background
location tracking.

Currently, settings provide approximate acquisition through Android’s built-in
network location provider and offline manual coordinate entry with a geographic
timezone selector; a city chooser is not implemented. Provider availability depends
on Android’s services and device configuration, while manual setup works offline.
A timezone selector alone supplies no coordinates. The current nearest-IANA-reference
lookup estimates a timezone rather than checking geographic boundaries, and can be
wrong near borders; review the estimate and choose the correct zone when needed.

Every input method must establish the site's geographic timezone. Capturing the
phone timezone alone is insufficient; use a geographic IANA timezone with its
daylight-saving rules. Manual setup and runtime calculations must work offline,
without proprietary SDKs. Bundled data and artwork require recorded provenance
and licenses.

## Display scope

Support home and lit lock screens, subject to physical-device verification.
Render only while visible and restore the current instant and saved settings
after wake or recreation. Always On Display and interactive sky exploration are
outside the first release.

## Orloj layers and display controls

v0.2 retains the civil clock, **Zodiac ring** with equinox marker, **Sun** with its
horizon/day/twilight/night plate, and **Moon** with illuminated phase. The Sun and
plate remain one toggle. No additional dial designs are introduced. Future
planet/bright-star displays (#117, linked to #41/#73) and sunrise/sunset/twilight
readouts (#118, linked to #89) require supporting user/device evidence before
implementation or visual design. The existing plate already shows the Sun's
horizon crossing; additional event text is outside this acceptance slice.

Four native sliders have persistent labels and integer percentage readouts:

| Control | Bounds | Default | Meaning |
| --- | --- | --- | --- |
| Size | 50–115% | 100% | Outer radius relative to 0.43 × shortest usable dimension |
| Horizontal position | 0–100% | 50% | Left-to-right safe placement range |
| Vertical position | 0–100% | 50% | Top-to-bottom safe placement range |
| Brightness | 80–100% | 100% | Black overlay across the whole rendered wallpaper |

Safe placement includes the outer rim's stroke. All dial artwork shares one
translation and uniform scale. Brightness changes neither Settings nor device
screen brightness. **Reset display** restores only these four defaults, preserving
the observing site, layer choices and appearance. Display controls remain enabled
without a site, when only the civil clock renders.

The separate `dial_display` store persists explicit adjustments immediately.
Missing/malformed values default independently; numeric outliers clamp. Reads do
not repair or write preferences. Every engine owns its viewport and observes the
shared controls until destruction; changes appear on its next visible tick.
Hidden, destroyed or surface-less engines perform no rendering.

Resolve the actual Canvas size, engine display resources, reported pixel offsets
and system insets; never force a wallpaper surface size. Offsets pan the visible
window only across the slack the surface has beyond it, so a launcher that reports
a scroll for a surface no larger than the display still leaves the default centred
and full-size. Recompute on each visible frame after surface/orientation changes. A
viewport too small for safe geometry is logged and skipped. API 29+ uses the
engine's [display context](https://developer.android.com/reference/android/service/wallpaper/WallpaperService.Engine#getDisplayContext())
so preview/active displays can have different resources. Cropping/insets unreported
by a launcher cannot be inferred; physical acceptance remains required.

#5 and v0.2 remain open until the owner merges the relevant PRs; the combined
physical-device pass is recorded in
[`testing/reports/2026-10-08-feat-5-display-controls.md`](testing/reports/2026-10-08-feat-5-display-controls.md),
with the coverage it could not exercise tracked in #122. Full battery qualification
and the signed personal release remain v0.3 work; this change creates no release or
tag.
