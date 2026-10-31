# Eufy NVR Local

**By Efficient X (EFX)**

Live camera feeds from a Eufy PoE NVR in Home Assistant, with individual camera
tiles that open into a larger view. A shared video player avoids opening a
separate server pipeline for every displayed tile.

**Experimental release.** This is for a separate test installation.
The new licensed protocol implementation has passed synthetic and Linux build
checks; real owner sign-in, fresh Supervisor setup, ARM and live-camera acceptance
are still outstanding. Keep a working installation until a migration is tested.

Versioned app images for AMD64 and ARM64 are available from the
[alpha.2 release](https://github.com/Efficient-X/Eufy-NVR-Local/releases/tag/v0.8.0-alpha.2),
alongside matching sources and original component notices. Home Assistant downloads
the program instead of building it, avoiding repeated locally built image exports
in backups. Actual household backup savings and live-camera acceptance are still
unmeasured. See the [image checks and acceptance plan](docs/PREBUILT_IMAGES.md).

## What it does

- Makes the NVR's feeds available to Home Assistant through go2rtc.
- Offers a guided sign-in screen, including Eufy's verification steps.
- Discovers the recorder's reported channels and supplies camera-layout metadata.
- Adds a dashboard card with individual tiles and a larger view when tapped.
- Offers optional remembered sign-in, **off by default**, for renewing access.

The recorder and its recordings stay in place. This does not replace the NVR.

The earlier household implementation used four dual-lens S4 units: eight views.
Software tests cover eight physical cameras / sixteen lens channels. That is
capacity testing, not proof of sixteen working live feeds on hardware. The new
protocol port still needs its own live-camera test.

## What you need

- A supported Eufy PoE recorder: development targets the S4 / T8N00.
- The Eufy account that owns it, plus access to its verification email.
- Home Assistant OS or another installation with the app store (formerly the
  Add-on Store), and HACS for the matching integration.
- A separate test installation. Home Assistant Core 2026.10.2 is the isolated
  integration test baseline; this is not an older-version compatibility claim.

**Internet is needed for Eufy sign-in and signalling.** The app serves video
locally, but the selected NVR media route can depend on the network. Check the
route diagnostic before describing your connection as LAN-only or offline.

## Install the preview

There are two parts: an **app** that brings in the video, and an **integration**
that gives Home Assistant camera items. Install both from the same source version.

1. Open the Home Assistant app store. In its menu, choose **Repositories**, add
   `https://github.com/Efficient-X/Eufy-NVR-Local`, and install
   **Eufy NVR Local (experimental)**. Home Assistant downloads the versioned app
   image for your processor. Leave it stopped while any older NVR app is running:
   the apps use the same video ports and must not run together.
2. In HACS, add the same URL as a **custom repository**, category **Integration**.
   Download **Eufy NVR Local**, then restart Home Assistant to load it.
3. Start the new app. Open **Settings > Devices & services > Add integration >
   Eufy NVR Local**, then choose **Connect installed Eufy NVR app**.
4. If asked, confirm creation of its first local-video password. This protects
   video on your network; it does not change your Eufy password. The integration
   saves it for you and restarts only the new app.
5. Keep that setup open. In another Home Assistant tab, open the app's
   **Open Web UI**, sign in to Eufy, complete any verification and choose the NVR.
   Return to the integration setup and submit once the camera service is running.
6. Add the **Eufy NVR cameras** dashboard card and choose the recorder group.
   The integration loads the card and its layout; no manual JavaScript resource
   entry or crop coordinates are required.

The preview starts manually by default. Once it works, you can enable the app's
**Start on boot** switch if you want it to return after a Home Assistant restart.
Do not turn off Protection mode. The guided app requests Home Assistant API access
so it can identify authorised administrators; that permission is broad even though
these setup operations read administrator and address information.

See the [plain-English setup guide](docs/SETUP_GUIDE.md) for each setting, what it
achieves, and what to do when Eufy asks for a code or picture check.

## Status and troubleshooting

See the [acceptance checklist](docs/RELEASE_ACCEPTANCE.md) for what remains to be
verified. Passing source tests does not prove a working installation on your NVR,
CPU load, browser or tablet. Do not repeatedly restart/sign in if Eufy limits
requests; follow the setup screen's cooldown guidance.

Before reporting an issue, remove passwords, email codes, tokens, camera pictures
and household details. Do not upload configuration files or Home Assistant
backups. The project does not require these to be posted publicly.

Developers can run `python -m pytest -q tests` and the Node suites in
`.github/workflows/ci.yml`. The bridge source and packaged copy must match.
The guided candidate builder creates a separately inventoried build package;
it does not install, upload or publish anything.

## Support EFX

The software is free. If it helps you, you can
[buy Efficient X a coffee](https://buymeacoffee.com/efficientx).
Support is optional and does not buy extra features or guaranteed support.

Efficient X (EFX) is an independent open-source project maintained by Brendon.
It is not affiliated with or endorsed by Anker/eufy.

## Licence and acknowledgements

Original project contributions retain the [MIT licence](LICENSE), including
HallyAus attribution. Native PTCS framing, cloud cryptographic primitives and
signed-request adaptations from [mega-yfue/eufy-sdk](https://github.com/mega-yfue/eufy-sdk)
retain Apache-2.0, with the full licence and modification record under
`eufy_nvr/licenses/`. This preview does not download or bundle Eufy's former
proprietary framing JS/WASM.

Dependencies have their own licences. See [THIRD_PARTY.md](THIRD_PARTY.md) and the
[source-first publication approach](docs/PUBLISHING.md). The repository's licence
does not describe a complete Debian/FFmpeg/PyAV/go2rtc image.
