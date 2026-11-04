# Eufy NVR Local

<img src="custom_components/eufy_nvr/brand/logo.png" alt="Eufy NVR Local by Efficient X" width="500">

**An independent Home Assistant project by Efficient X (EFX). Public experimental preview: 0.8.0-alpha.8.**

Show Eufy PoE recorder cameras in Home Assistant, with fixed and tracking tiles
that you can click to enlarge. The shared-player design uses one combined video
connection for the tiles rather than opening a separate connection for each one.

## What works, and what is still being tested?

**Status updated 4 November 2026.** An earlier household build has shown four
dual-lens cameras as eight moving views, including enlargement. That earlier
build is not a downloadable beta.1 release in this repository.

The public **alpha.8 is a different build**. In its latest recorder test,
discovery reached its time limit and startup stopped because no saved camera
configuration was available. Fresh-install live video has **not** passed
acceptance for alpha.8. The working household setup does not establish that this
public preview works on a new installation.

This preview is available for testing and feedback. Keep an existing working
camera installation until the replacement has passed your own checks. Do not run
two recorder video engines at the same time.

[Experimental release](https://github.com/Efficient-X/Eufy-NVR-Local/releases/tag/v0.8.0-alpha.8)
| [Report results or a problem](https://github.com/Efficient-X/Eufy-NVR-Local/issues/new)
| [Release history](https://github.com/Efficient-X/Eufy-NVR-Local/releases)

## What you need

- A Eufy S4 / T8N00 PoE recorder and its owner account.
- Home Assistant with the Apps store, on AMD64 or ARM64 hardware, plus HACS for
  the integration. This repository declares Home Assistant 2026.10.2 or later.
- Internet access for Eufy sign-in and connection signalling. The video is
  designed to travel over your home network; this is not a fully offline system.
- A browser that can play HEVC/H.265 video when video copy is enabled.

Software tests cover layouts for up to eight physical cameras and sixteen lens
tiles. Eight physical cameras, sixteen live views, long-running performance and
other recorder models have not been verified on hardware here. The tiles share
one combined picture; enlarging a tile does not create extra image detail.

## Install the experimental preview

There are two parts. The **app** receives video from the recorder. The
**integration** adds camera entities and the dashboard card to Home Assistant.
Installing only one part does not complete the connection.

Use a separate test installation if you already rely on working cameras.
These are the published installation routes, not a claim that the full
first-install procedure has passed real-recorder acceptance.

1. In Home Assistant, open **Settings → Apps → App store → ⋮ → Repositories**.
   Add `https://github.com/Efficient-X/Eufy-NVR-Local`. This lets Home Assistant
   find the recorder app. Install **Eufy NVR Local (experimental)** and check that
   the version is **0.8.0-alpha.8**.
2. In **HACS → ⋮ → Custom repositories**, add the same repository URL and choose
   **Integration**. Find **Eufy NVR Local** and download **0.8.0-alpha.8**. If that
   version is not offered, stop and open an issue rather than mixing versions.
   Restart Home Assistant after installing the integration so it can load it.
3. Start the experimental app, then open **Settings → Devices & services → Add
   integration → Eufy NVR → Connect installed Eufy NVR app**. Select the new app.
   Follow the local-video password confirmation if one is needed; Home Assistant
   remembers this separate password for you.
4. Open the app's **Open Web UI**, sign in with the Eufy account that owns the
   recorder, complete any Eufy check and select the recorder. Return to the
   integration setup when the camera service is running.
5. Follow the [plain-English setup guide](docs/SETUP_GUIDE.md) to add the
   **Eufy NVR cameras** card. Check that the actual pictures move and that clicking
   a tile enlarges it. A saved account or a “running” status alone does not prove
   live video works.

Your Eufy password and the app's local video password do different jobs. The
first proves ownership to Eufy; the second protects video inside your home.
Neither needs to be posted in a support report. The optional remembered Eufy
sign-in setting is off by default; read its explanation before enabling it.

If discovery stops, use the feedback link below. Do not repeatedly sign in,
delete private app files, weaken video authentication or remove a working setup
to get past the error.

## Help us test it

[Open a feedback report](https://github.com/Efficient-X/Eufy-NVR-Local/issues/new)
with your app and integration versions, recorder model, camera count, browser,
and what worked or failed. Say whether you tested a new installation or an
upgrade. Useful results include moving fixed/tracking pictures, enlargement,
approximate delay and Home Assistant resource use.

Do not post passwords, tokens, email codes, account files, Home Assistant
backups, unreviewed raw logs or pictures showing private camera views. If you
include integration diagnostics, review the file before sharing it.

See the [acceptance checklist](docs/RELEASE_ACCEPTANCE.md) for the checks that
separate a successful install from a service that merely starts.

## Support and licences

This is free software. You can [buy Efficient X a coffee](https://buymeacoffee.com/efficientx)
if you find it useful. A tip is optional and does not buy guaranteed features or
support. This project is not affiliated with or endorsed by Anker/eufy or Home
Assistant.

Original code retains the [MIT licence](LICENSE), including HallyAus attribution.
Adapted mega-yfue/eufy-sdk code retains Apache-2.0 and provenance under
`eufy_nvr/licenses/`. See [third-party notices](THIRD_PARTY.md). Original
camera/recorder artwork is MIT-licensed in [artwork/LICENSE](artwork/LICENSE).
