# Contributing to Astronomical Clock Wallpaper

Thank you for your interest in contributing to Astronomical Clock Wallpaper. We
welcome bug reports, physical-device testing, documentation improvements, and
code contributions that share our commitment to mathematical precision, battery
efficiency, and software quality.

This project is guided by open-source craft and reproducible engineering.
Everyone contributing to this repository, including humans and agents, must read
this guide. Shared contribution policy is defined here; agents must additionally
follow [AGENTS.md](AGENTS.md), which supplements these requirements.

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
  Never trigger continuous background polling, GPS location requests, or wake
  locks while the surface is hidden or destroyed.
- **Milestone-governed releases**: Trunk (`main`) is an active development branch,
  not a continuous public release channel. Public distribution catalogs (such as
  F-Droid) are reserved strictly for tagged milestone releases that have satisfied
  complete physical-device qualification and acceptance criteria. Do not configure,
  propose, or trigger external distribution channels, rolling public releases
  (including F-Droid or Play Store), or CI deployment actions without explicit
  milestone direction from the owner.
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
  [docs/dependencies.md](docs/dependencies.md). New external libraries or bundled
  assets require explicit necessity, owner approval, and complete license and
  provenance verification. Zero analytics or proprietary tracking SDKs are permitted.

## Engineering and product contracts

Follow the authoritative [product contract](docs/design.md),
[Orloj geometry](docs/orloj.md), and
[astronomy specifications and limits](docs/astronomy/README.md).

- Use Kotlin, Canvas, and `WallpaperService` with a small settings app. Keep
  astronomy calculations separable from Android lifecycle and drawing code.
- Use `io.github.godaniya.astronomicalclockswallpaper` as the stable release application ID.
- Use Astronomy Engine, pin its version or source revision, and retain its notices.
  Draw original artwork; avoid proprietary SDKs. Dependency and asset requirements
  are defined in [quality principles](#quality-reputation-and-release-principles).
- Log (don't silently swallow) render/surface no-op and failure paths so field
  issues are diagnosable.
- Keep signing keys, passwords, local SDK paths, and private device data out of
  Git. Retain and privately back up the first durable APK release key. Do not
  promise cross-store signature continuity before each store's signing path is verified.

## Product feature gate

Before opening an issue, writing code, or opening a PR for a new feature or
architecture proposal, answer these six questions concretely:

1. Who is the target user?
2. What real problem or friction does this solve?
3. Can a user understand its presence in under 10 seconds?
4. Does it improve visual aesthetics, celestial comprehension, or battery retention?
5. Who complains if it is omitted?
6. Is this grounded in real user/device feedback rather than hypothetical future support?

Record all six answers in the proposal issue before implementation. A PR may link
those answers, but must update any answer affected by its scope. Identify specific
users and problems, and reference available user or device feedback. Explicitly
label anticipated benefits, assumptions, and missing evidence; do not present them
as measured outcomes or invent complaints.

When supporting evidence is missing, defer implementation. An evidence-gathering
issue may proceed only if it records all six answers, identifies the unknowns,
and defines a bounded validation step and completion criterion. Implementation
waits for supporting user or device feedback; completing the validation step alone
does not establish support for the proposal.

For internal architecture proposals, explain the user outcome they enable. If
direct visibility is inapplicable, explain why rather than inventing a user-facing
presence. Apply this gate to new proposals and newly expanded feature or
architecture scope; do not retroactively rewrite existing issues. Routine fixes,
maintenance, documentation, and tests that introduce neither new feature nor
architecture scope remain governed by existing standards.

Agents must also read the [agent supplement](AGENTS.md). Preserve
[test scope and proportionality](#test-scope-and-proportionality); this gate
requires no new per-test forms or reports.

## Verification

Keep changes small and readable. Add meaningful tests for behavior changes;
documentation-only work needs appropriate content and link checks. Never claim
physical-device verification or firmware qualification without actual execution
on hardware. Transparently record unrun checks and emulator limitations in PRs.

For Android changes, run `./gradlew qualityGate :app:assembleDebug` and
`scripts/verify-apk.sh`. Follow [development guidance](docs/development.md#checking-policy)
for commands and procedures; `./gradlew check` includes the quality gate and
`./gradlew formatKotlin` is the explicit formatter. Keep compilation, type-resolved
detekt, ktlint, Android Lint, and tests strict. Astronomy tests must cite independent reference data, units, coordinate frames, and
tolerances, including hemisphere and polar cases. Device reports must distinguish
physical-device results from emulator checks and state the Android version, source
revision, and SHA-256 of the tested APK. Keep device identifiers and precise private
locations out of public reports (see [physical-device privacy](#physical-device-testing-and-privacy)).
Physical-device procedures and standard acceptance criteria are maintained in
[docs/device-testing.md](docs/device-testing.md). When a pull request requires physical-device
evidence, do not append to `docs/device-testing.md`; add a standalone historical report in
`docs/testing/reports/YYYY-MM-DD-<kind>-<issue-number>-<short-description>.md` and link it from the pull
request. The filename date is the first recorded verification date in the report, not the
feature's implementation or extraction date. Preserve original run dates, measurements, and
APK/source attribution. Historical reports may receive documented editorial or privacy
corrections; new measurements belong in new reports. Discover reports through the directory listing;
future reports do not need a new entry in this guide or the documentation hub.
This decouples historical audit trails from living operating procedures and
prevents merge conflicts across concurrent worktrees.
Record limitations and unresolved failures in the issue and PR; do not silently
weaken acceptance criteria.

### Device verification tiers

- **No device pass** for changes with no on-screen or lifecycle effect (logging, build,
  documentation, pure logic covered by host tests). State "device checks not run" and why.
- **Time-dependent visuals** (anything that moves with the clock): use the debug-only virtual clock
  from [docs/device-testing.md](docs/device-testing.md#virtual-time) instead of waiting. A 30-minute
  advance takes one broadcast, not 30 minutes. `scripts/device_smoke.py` runs this check.
- **Lifecycle, reboot, lock screen, and release sign-off** need the full manual pass.

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

- **Issues and scope**: Use GitHub issues to record scope, dependencies, and
  acceptance criteria. Do not claim acceptance criteria are met without evidence.
- **Issue-linked worktrees**: After the initial bootstrap on `main`, develop on
  issue-linked branches in native-filesystem sibling worktrees named
  `astronomical-clocks-wallpaper-<issue-number>-<short-description>`.
  Use branch names such as `feat/1-android-bootstrap`; keep the primary clone on `main`.
- **Conventional Commits**: Commit subjects and PR titles follow
  [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), for example
  `feat(dial): render zodiac constellation marks`. Link issues using native GitHub
  references (e.g. `Refs #41` or `Closes #41`).
- **Small, reviewable PRs**: Open a pull request linking the issue. Follow
  [.github/pull_request_template.md](.github/pull_request_template.md), leading with
  two to four concise outcome bullets and an honest verification checklist.

### Commit signing

Preserve Git signing. Do not disable signing to work around unavailable agent
access. Merge method does not change this: under the default [squash
merge](#merge-method) the SSH-signed commits stay on the PR branch while `main`
receives a single commit signed by GitHub's web-flow key. Treat that as the
expected outcome, not as a signing failure.

### Merge method

GitHub's **squash merge** is the default for this repository, and the owner
merges through the GitHub UI. Do not use a merge commit or a rebase merge, and
do not argue for one, unless the owner explicitly asks.

- The squash merge lands one commit on `main`: with
  `squash_merge_commit_title: COMMIT_OR_PR_TITLE`, the subject defaults to the
  pull request title (with PR number) for multi-commit PRs, or to the single
  commit's title for single-commit PRs. Under
  `squash_merge_commit_message: COMMIT_MESSAGES`, original commit messages
  (subject lines and bodies) are preserved — write every commit message to
  stand alone, since it outlives its individual commit.
- The squash commit is signed by GitHub's web-flow key (committer
  `GitHub <noreply@github.com>`), not by the agent's SSH key. GitHub also appends
  a `Co-authored-by:` line for the PR author, so the merged commit can carry
  additional trailers. Both are expected.
- The PR head commits remain reachable on GitHub at `refs/pull/<number>/head`;
  they do not enter `main`'s ancestry. Cite them by SHA as usual. (To inspect
  them in a local clone: `git fetch origin pull/<number>/head`).
- The remote branch is not automatically deleted on merge
  (`delete_branch_on_merge: false`); pruning is done manually by the owner.

### Pull requests and Accountability Index

Follow [.github/pull_request_template.md](.github/pull_request_template.md):

- Add risk, compatibility, migration, or rollout details only when material.
- Include a collapsible **Accountability Index** at the bottom: a linked table of
  each commit with a brief what/why, links to discussion comments containing
  review decisions, and a short caveats note. It indexes evidence; full rationale
  belongs in commit bodies and discussion threads.
- Update the index on every push and review round, in the same pass as the
  commit or reply: add one row for every commit pushed (the table must not stop
  at the opening commits), and replace the "Review decisions" line with links to
  every resolved discussion thread. Those rows cite the PR's own commits, which
  after the default [squash merge](#merge-method) remain on the PR head ref
  rather than in `main`'s ancestry; citing them by SHA is still correct, and the
  index is not expected to match `main` commit-for-commit. A table stuck at the
  opening commits or a stale "none yet" line is itself a review finding. Link
  evidence without copying extensive rationale. This project explicitly requires
  this per-commit index, overriding the global default against one.
- Link out-of-band evidence the PR depends on — issue-body edits above all — from
  the index too. When the Summary or Verification rests on edited issue bodies,
  reference each edited issue so its audited state is traceable from the PR
  record; verification that relies on such edits but links none of them is
  itself a review finding.
- For AI-assisted work, follow the [PR attribution requirements](#ai-assisted-contributions).
  See [Merge method](#merge-method) for how the squash commit's trailers are formed.
- Use native, unquoted GitHub references for issues, PRs, and commits: #5,
  `owner/repo#number`, or `owner/repo@sha` (substitute actual values and remove
  code formatting in published text). Use direct links to specific discussion
  comments for review evidence. Do not automatically mention or assign reviewers;
  use the structured reviewers field only when authorized.
- Do not rewrite existing commits or live PRs solely to apply metadata policy.

This accountability policy is adapted from
[cmp0xff's pinned guidance](https://github.com/cmp0xff/pandas-stubs/blob/f72b507f64c50427ad50595a7c615b0ac3552b61/AGENTS.md),
with Android conventions replacing the upstream project's domain-specific rules.

## AI-assisted contributions

We recognize and support the use of AI coding assistants (such as Antigravity,
Claude, or Copilot). Contributions using AI must meet the same standards of
craftsmanship, transparency, and accountability as human work. These obligations
apply to human contributors using AI as well as autonomous agents.

Every AI-authored commit must include a body recording:

- The motivation or problem being addressed.
- The chosen approach and material constraints.
- Alternatives actually considered and why they were not chosen. If none were
  considered, say so briefly; never manufacture alternatives to fill a section.
- Verification actually performed, with unrun checks and limitations stated.

End each AI-authored commit with **one `Co-Authored-By` trailer per distinct
contributor, never duplicated**. For model contributors, a trailer names the
actual disclosed model and its provider's no-reply address, for example
`Co-Authored-By: deepseek-v4-flash-vision-exp <noreply@deepseek.com>`. Name the
model itself, not the client, harness, or tool that drove it; the client identity
is acceptable only when the model is unavailable. Inspect the complete message
before committing to prevent duplicate trailers. Never invent model versions,
tests, decisions, or review evidence.

Two cases legitimately add a further trailer:

- **A commit that addresses a review comment from another person or agent**
  credits that reviewer. Resolve the address at the time from the reviewer's
  platform account as `<numeric-id>+<login>@users.noreply.github.com` rather than
  hardcoding a fixed identity. For the account that posts Copilot's reviews the
  GitHub API reports the id `175728472` for
  `copilot-pull-request-reviewer[bot]`, giving
  `Co-authored-by: Copilot <175728472+copilot-pull-request-reviewer[bot]@users.noreply.github.com>`.
  One trailer per addressed reviewer. A reviewer credited on one commit is not
  re-credited on unrelated commits in the same pull request.
- **A commit that more than one model materially authored** — a rebase that
  resolves real conflicts, for example — names each of them, one trailer per
  model, never two lines for the same model.

Record the first case whenever the commit's change exists because of the review,
and say in the commit body what the review found, so the trailer is checkable
against the thread rather than decorative.

GitHub's squash merge consolidates `Co-authored-by:` trailers across squashed
commits and appends a further line for the pull request author, so the commit
that lands on `main` may carry more trailers still. That is expected and is not a
duplicate-trailer defect to fix.

Finish the PR with the same visible `Co-Authored-By` identity used for the source
work, as required by [PR accountability](#pull-requests-and-accountability-index).
