# Installation and upgrades

## Available now: development APKs

No public GitHub Releases exist yet. Release tooling creates private drafts for owner
qualification and publication; ordinary Obtainium users cannot see or install drafts.
This preparation does not complete [#6](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/6),
[#7](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/7), or
[#71](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/71).

Download `debug-apk-<source revision>` from a successful
[Android quality gate run](https://github.com/godaniya/astronomical-clocks-wallpaper/actions/workflows/build.yml)
(GitHub sign-in may be required), extract `app-debug.apk`, or
[build locally](development.md#local-setup). Install it through Android's package
installer, allowing **Install unknown apps** for the source app when prompted, or:

```sh
adb install -r app-debug.apk
```

Open **Astro Clocks**, set the observing site, and tap **Open wallpaper preview**.
Follow [device installation and launch](device-testing.md#install-and-launch).
Debug keys are disposable: an update signed by a different debug key may require
uninstalling the old debug app, which removes its settings.

## After owner publication: Obtainium

[Obtainium](https://obtainium.imranr.dev/) tracks published release assets directly
from their source. This setup becomes usable only after an owner publishes a qualified
release with its APK. It has not been tested for this application.

1. Install Obtainium using its official instructions.
2. Open **Add App** and paste
   `https://github.com/godaniya/astronomical-clocks-wallpaper`.
3. Enable **Include prereleases** for `v0.*` milestones and suffixed versions such as
   `v1.0.0-beta.1`; this workflow marks those releases as prereleases.
4. Add the app and follow Obtainium's installation prompt.

On a device with Obtainium installed, the documented shortcut is
[Add this repository](obtainium://add/https://github.com/godaniya/astronomical-clocks-wallpaper).
See [Obtainium's URL documentation](https://wiki.obtainium.imranr.dev/deep_links/).
Scheduled background checks and update notifications do not guarantee unattended
installation. Installation behavior depends on Android, installer permissions, and
Obtainium settings; follow its prompts.

## After owner publication: direct download and verification

From the [Releases page](https://github.com/godaniya/astronomical-clocks-wallpaper/releases),
download the matching three assets (replace `X.Y.Z` with the full version):

- `AstronomicalClocksWallpaper-vX.Y.Z.apk`
- `AstronomicalClocksWallpaper-vX.Y.Z-sha256sums.txt`
- `AstronomicalClocksWallpaper-vX.Y.Z-signing-cert-sha256.txt`

In the download directory:

```sh
shasum -a 256 -c AstronomicalClocksWallpaper-vX.Y.Z-sha256sums.txt
"$ANDROID_HOME/build-tools/36.0.0/apksigner" verify --verbose --print-certs \
  AstronomicalClocksWallpaper-vX.Y.Z.apk
```

Compare the reported signer certificate SHA-256 with a previously trusted owner
fingerprint and the certificate asset. The checksum detects changes to APK bytes;
a checksum downloaded alongside an APK does not independently establish publisher
identity. The certificate asset exposes key continuity, but initial trust requires
an independently trusted fingerprint. Then install with Android's package installer
or `adb install -r AstronomicalClocksWallpaper-vX.Y.Z.apk`.

## Identities and upgrades

Release uses `io.github.godaniya.astronomicalclockswallpaper`; debug uses
`io.github.godaniya.astronomicalclockswallpaper.debug`. These are separate apps and
can coexist. Installing release requires no debug uninstall and does not migrate
debug preferences; configure the release app separately.

Android updates an existing release app when application ID and signing identity
match and the version code permits the upgrade. Version codes must increase.
Settings preservation still requires the actual same-key upgrade qualification in
#7, including location, layers, size, position, and brightness; it is not established
by successful signature verification alone.

Accrescent, IzzyOnDroid, F-Droid, and commercial channels remain evaluations governed
by milestone qualification. No submissions, store support, or cross-store signing
continuity are established by this PR.
