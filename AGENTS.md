# Astronomical Clock Wallpaper agent guidance

Everyone contributing to this repository must read [CONTRIBUTING.md](CONTRIBUTING.md),
the authoritative shared contributor guide. Agents must additionally follow this
supplement; it adds agent instructions without redefining shared policy.

Read [README.md](README.md) for public project context and
[docs/README.md](docs/README.md) for documentation architecture.

## Working defaults

- Preserve unrelated working-tree changes and existing signed commits. Follow
  current user instructions and repository configuration and CI requirements.
  Do not rewrite existing commits or live PRs solely to apply metadata policy.
- Use applicable workflow skills when available, including `git-metadata` for
  commit and PR metadata. Follow the mandatory
  [contributor workflow](CONTRIBUTING.md#workflow-and-pull-requests) and
  [AI-assisted contribution requirements](CONTRIBUTING.md#ai-assisted-contributions).
- Preserve the configured repository-local author identity:
  `cmp0xff <5564164+cmp0xff@users.noreply.github.com>`. Do not replace it with
  an employer identity or change global Git settings. Follow
  [commit signing](CONTRIBUTING.md#commit-signing); confirm each commit you create
  is signed and verify its signature before handoff.
- Follow the mandatory [quality and release requirements](CONTRIBUTING.md#quality-reputation-and-release-principles),
  [engineering contracts](CONTRIBUTING.md#engineering-and-product-contracts), and
  [verification guidance](CONTRIBUTING.md#verification). Record actual results
  and limitations according to those shared policies.

## Issue metadata and attribution

Follow the shared [issue attribution policy](CONTRIBUTING.md#issue-attribution).
Conclude AI-authored or AI-assisted issue descriptions with `Co-Authored-By`
trailers naming the actual disclosed models and provider no-reply addresses.
For material revisions, preserve existing trailers in chronological order and
append each newly contributing model once. Never invent model identities or
edit existing issues solely to apply this policy. Human-only issues omit attribution.

## Product feature gate

Agents must perform the [contributor product feature gate](CONTRIBUTING.md#product-feature-gate)
before new feature or architecture proposals, following its scope and evidence
requirements. Follow [test scope and proportionality](CONTRIBUTING.md#test-scope-and-proportionality).

## Architecture and product constraints

Follow the authoritative [product and observing-site contract](docs/design.md),
[Orloj projection geometry](docs/orloj.md), and
[astronomy specifications and implementation limits](docs/astronomy/README.md).
Use [development guidance](docs/development.md) for the pinned toolchain, commands,
and recorded narrow rule exceptions.

## Shared-device access

The physical device is a single shared resource; do not hold it for real-time waits.
Follow [device verification tiers](CONTRIBUTING.md#device-verification-tiers) and
[device procedures](docs/device-testing.md). Ask before installing over, resizing,
or otherwise changing a device another session is using, and restore any setting
you change.
