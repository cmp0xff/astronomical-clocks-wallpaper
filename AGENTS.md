# Astronomical Clock Wallpaper agent guidance

This document defines operational protocols, quality guardrails, and metadata
standards for AI coding assistants and autonomous agents operating in this
repository.

Read [README.md](README.md) for public project context, [docs/README.md](docs/README.md)
for documentation architecture, [CONTRIBUTING.md](CONTRIBUTING.md) for shared engineering
and testing standards, [docs/design.md](docs/design.md) for the product contract, and
[docs/orloj.md](docs/orloj.md) for detailed astronomical geometry. [docs/development.md](docs/development.md)
defines the pinned toolchain, strict checking policy, and justified exceptions.

## Machine operational guardrails

Agents must observe the project's quality, stability, and release boundaries:

- **Follow contributor engineering standards**: Adhere strictly to the quality,
  reputation, and testing proportionality principles established in
  [CONTRIBUTING.md](CONTRIBUTING.md).
- **Milestone and distribution boundaries**: Trunk (`main`) is an active development
  branch. Do not configure, propose, or trigger external distribution channels,
  rolling public releases (such as F-Droid or Play Store), or CI deployment actions
  without explicit milestone direction from the owner.
- **Inviolable quality gates**: Never bypass, lower, or suppress compiler warnings,
  detekt analysis, or Android Lint rules (`allWarningsAsErrors = true`). Do not
  introduce baseline files or blanket `@Suppress` annotations. Fix the underlying
  code. Narrowly-scoped, justified exceptions are recorded in
  [docs/development.md](docs/development.md#rule-exceptions); preserve those
  annotations and record any new exception there and in the PR.
- **Live wallpaper lifecycle contract**: Live wallpapers execute in the device
  background. Maintain the strict visibility contract: render exclusively when
  visible; never trigger continuous background polling, GPS location requests,
  or wake locks while the surface is hidden or destroyed.
- **Dependency hygiene**: Do not introduce new external libraries or bundled assets
  without explicit necessity, owner approval, and complete license and provenance
  verification, licensed compatibly with the intended distribution and pinned to
  an exact upstream version, revision, or checksum, recorded in
  [docs/dependencies.md](docs/dependencies.md).
- **Honest verification**: Never claim physical-device verification or firmware
  qualification without actual execution on hardware. Transparently record unrun
  checks and emulator limitations in pull requests.

## Working defaults

- Preserve unrelated changes in the working tree, and the signed commits on the
  branch you are working on. The default [squash merge](#merge-method) keeps
  those commits on the pull request's head ref instead of `main`'s ancestry;
  that is intended, not lost history, and not a reason to change the merge.
  Follow current user instructions and repository configuration and CI
  requirements.
- After the initial bootstrap on `main`, develop on issue-linked branches in
  native-filesystem sibling worktrees named
  `astronomical-clocks-wallpaper-<issue-number>-<short-description>`. Use branch names such
  as `feat/1-android-bootstrap`; keep the main checkout on `main`.
- Use GitHub issues to record scope, dependencies, and acceptance criteria. Open a
  pull request for subsequent development and link its issue. Do not claim
  acceptance criteria are met without evidence.
- Keep changes small and readable. Add meaningful tests for behavior changes;
  documentation-only work needs appropriate content and link checks.

## Architecture and product constraints

The product contract is defined authoritatively in [docs/design.md](docs/design.md),
detailed projection geometry in [docs/orloj.md](docs/orloj.md), and astronomy
implementation limits in [docs/astronomy/README.md](docs/astronomy/README.md). Key machine
constraints include:

- Use Kotlin, Canvas, and `WallpaperService` with a small settings app. Keep
  astronomy calculations separable from Android lifecycle and drawing code.
- Use `io.github.godaniya.astronomicalclockswallpaper` as the stable release application ID.
- Use Astronomy Engine, pin its version or source revision, and retain its
  notices. Record provenance and licenses for all dependencies and bundled data
  or artwork. Draw original artwork; avoid proprietary SDKs.
- Respect the observing-site contract: one selected site anchors both astronomy
  and civil time. Support home and lit lock screens; render only while visible.
- Log (don't silently swallow) render/surface no-op and failure paths so field issues
  are diagnosable.
- Keep signing keys, passwords, local SDK paths, and private device data out of
  Git. Retain and privately back up the first durable APK release key. Do not
  promise cross-store signature continuity before each store's signing path is
  verified.

## Commit metadata and signing

Use the `git-metadata` skill when available. Subjects and PR titles follow
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), for example
`feat(wallpaper): render the astronomical dial`. Link relevant issues in the body
using native GitHub references such as `Refs #5` or `Closes #5` when appropriate.

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

Preserve Git signing. Do not disable signing to work around unavailable agent
access. Confirm each commit you create is signed, and verify it before the
handoff. Merge method does not change this: under the default [squash
merge](#merge-method) the SSH-signed commits stay on the PR branch while `main`
receives a single commit signed by GitHub's web-flow key. Treat that as the
expected outcome, not as a signing failure. The repository-local author identity
is `cmp0xff <5564164+cmp0xff@users.noreply.github.com>`; do not replace it with
an employer identity or change global Git settings.

## Merge method

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

## Pull requests and Accountability Index

Follow [.github/pull_request_template.md](.github/pull_request_template.md):

- Lead with two to four concise outcome bullets, a verification checklist, and
  issue links. State unrun checks honestly.
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
- Finish with the same visible `Co-Authored-By` identity used for the source work.
  This attribution is not a claim about the merged commit: see
  [Merge method](#merge-method) for how the squash commit's trailers are formed.
- Use native, unquoted GitHub references for issues, PRs, and commits: #5,
  `owner/repo#number`, or `owner/repo@sha` (substitute actual values and remove
  code formatting in published text). Use direct links to specific discussion
  comments for review evidence. Do not automatically mention or assign reviewers;
  use the structured reviewers field only when authorized.
- Do not rewrite existing commits or live PRs solely to apply metadata policy.

This accountability policy is adapted from
[cmp0xff's pinned guidance](https://github.com/cmp0xff/pandas-stubs/blob/f72b507f64c50427ad50595a7c615b0ac3552b61/AGENTS.md),
with Android conventions replacing the upstream project's domain-specific rules.

## Verification

Run `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` for Android
changes. `./gradlew check` includes the quality gate; `./gradlew formatKotlin` is
the explicit formatter. Keep compilation, type-resolved detekt, ktlint, Android
Lint, and tests strict. Record narrow rule exceptions in docs/development.md and
the PR; do not add baselines or blanket suppressions.

For Android work, run the documented checks appropriate to the change. Astronomy
tests must cite independent reference data, units, coordinate frames, and
tolerances, including hemisphere and polar cases. Device reports must distinguish
physical-device results from emulator checks and state the Android version, source
revision, and SHA-256 of the tested APK. Keep device identifiers and precise private
locations out of public reports (see [CONTRIBUTING.md](CONTRIBUTING.md#physical-device-testing-and-privacy)).
Physical-device procedures and standard acceptance criteria are maintained in
[docs/device-testing.md](docs/device-testing.md). When a pull request requires physical-device
evidence, do not append to `docs/device-testing.md`; add a standalone historical report in
`docs/testing/reports/YYYY-MM-DD-<kind>-<issue-number>-<short-description>.md` and link it from the pull
request. The filename date is the first recorded verification date in the report, not the
feature's implementation or extraction date. Preserve original run dates, measurements, and
APK/source attribution. Historical reports may receive documented editorial or privacy
corrections; new measurements belong in new reports. Discover reports through the directory listing;
future reports do not need a new entry in this guide or the documentation hub. This decouples historical audit trails from living
operating procedures and prevents merge conflicts across concurrent worktrees.
Record limitations and unresolved failures in the issue and PR; do not silently
weaken acceptance criteria.

### Device verification tiers

The physical device is a single shared resource; do not hold it for real-time waits.

- **No device pass** for changes with no on-screen or lifecycle effect (logging, build,
  documentation, pure logic covered by host tests). State "device checks not run" and why.
- **Time-dependent visuals** (anything that moves with the clock): use the debug-only virtual clock
  from [docs/device-testing.md](docs/device-testing.md#virtual-time) instead of waiting. A 30-minute
  advance takes one broadcast, not 30 minutes. `scripts/device-smoke-test.py` runs this check.
- **Lifecycle, reboot, lock screen, and release sign-off** need the full manual pass.

Ask before installing over, resizing, or otherwise changing a device another session is using, and
restore any setting you change.
