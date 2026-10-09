# Release preparation and owner publication

This tooling prepares signed **draft** releases. Building, signing, physical-device
qualification, and publication are separate steps. Version `0.2.0` / code `2` is
preparatory metadata, not a qualified release or completion of #6, #7, or #71.

## Retained identity and private backup

Before the first durable release, the owner must establish a retained APK signing
key and independently record its public SHA-256 certificate fingerprint. Privately
back up the keystore and recovery information, including alias and passwords, in
access-controlled encrypted storage with a separate recovery copy. Verify recovery
privately before relying on it. Do not put keys, passwords, base64 keystores, or
private recovery details in Git, logs, artifacts, or PRs. Git SSH commit signing
is separate from APK signing. Disposable verification keys are never production keys.

## Protected GitHub configuration

On the upstream repository, the `release` environment requires `cmp0xff` approval,
disables administrator bypass, and permits only `v*` tag deployments. Self-approval
is allowed because the owner is currently the sole collaborator: this is owner
authorization, not independent review. Review the exact tagged scripts and artifact
before approving access to signing material.

The active [creation ruleset](https://github.com/godaniya/astronomical-clocks-wallpaper/rules/24784123)
allows only `cmp0xff` to create `v*` tags. The separate
[immutable-tag ruleset](https://github.com/godaniya/astronomical-clocks-wallpaper/rules/24784124)
prohibits updates and deletion with no bypass actors. These controls must remain
enforced before credentials are configured; do not weaken them to retry a release.

Configure these four secrets **only in that environment**, never repository or
organization secrets: `RELEASE_KEYSTORE_BASE64`, `RELEASE_KEYSTORE_PASSWORD`,
`RELEASE_KEY_ALIAS`, and `RELEASE_KEY_PASSWORD`. Configure its public environment
variable `RELEASE_CERT_SHA256` with the retained certificate's 64 hexadecimal digits
(colon-separated fingerprints are accepted too). They are intentionally absent until
the owner establishes the key. No credentials were created or uploaded in this PR.
Missing secrets or a missing/mismatched fingerprint stop signing or verification.

## Local build, then sign, then package

Use the [pinned development toolchain](development.md#local-setup), including
command-line tools 23.0 and Build Tools 36.0.0. No tool fallback is allowed.
Build in a shell without any signing credentials exported:

```sh
./gradlew qualityGate :app:assembleDebug :app:assembleRelease
scripts/verify-apk.sh
```

In a separate signing shell, supply `RELEASE_KEYSTORE_PATH`, both password variables,
`RELEASE_KEY_ALIAS`, and the trusted `RELEASE_CERT_SHA256` through private local means.
Do not type passwords into shared command history. Run from the repository root:

```sh
scripts/sign-release-apk.sh \
  app/build/outputs/apk/release/app-release-unsigned.apk build/signed-release.apk
scripts/package-release.sh v0.2.0 build/signed-release.apk
```

The helper aligns the unsigned APK, copies the keystore into a restricted temporary
directory, passes passwords through environment-based apksigner arguments, and
removes the temporary directory on success or failure. It runs no Gradle commands.
Packaging verifies the signature, explicit v2 success, exactly one expected signer,
non-debug identity, declared version name/code, SDK, permissions, wallpaper service,
and complete license. It creates exactly named APK, checksum, and certificate assets
under `build/dist/`; preserve the owner backup separately.

## CI draft and publication

Only after reviewing the source on current `main`, create an owner-authorized tag
`vX.Y.Z` (optional prerelease suffix) matching `versionName`. Use monotonically
increasing `versionCode`. Do not create a tag merely to test this tooling.

The read-only build job validates tag syntax and current-main ancestry, rejects a
declared `versionCode` that does not exceed the highest across existing `v*` tags
(so a lowered code cannot prepare a draft), runs the quality gate, and uploads the
unsigned APK. The protected draft job downloads that exact artifact from the same
workflow run; only its signing step receives secrets.
It verifies and packages the signed APK and creates a draft with the three exact
assets. Only this job has `contents: write`. Existing releases, including drafts,
are rejected; the workflow never overwrites them or publishes automatically.
`v0.*` and suffixed versions are marked prereleases.

Before owner publication, complete #6 qualification and #7's device install and
same-key upgrade from a lower version code. Record source revision, APK checksum,
public certificate, version metadata, and actual results, keeping private device
data private. Confirm draft assets match the qualified APK, review release notes and
limitations, then publish manually. Test public Obtainium parsing/installation for
#71 after publication: ordinary Obtainium users cannot access drafts.

Production signing, physical-device installation, same-key upgrade preservation,
and Obtainium installation remain untested by this preparation. Other distribution
channels need their own evaluation and signing-continuity evidence.
