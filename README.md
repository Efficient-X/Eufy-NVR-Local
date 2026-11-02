# Eufy NVR Local

<img src="custom_components/eufy_nvr/brand/logo.png" alt="Eufy NVR Local by Efficient X" width="500">

**Independent project by Efficient X (EFX). Unreleased experimental review candidate.**

Eufy NVR Local by Efficient X (EFX) is an experimental Home Assistant app and integration for Eufy PoE recorders. It aims to bring fixed and tracking feeds into shared camera tiles that open into larger views. The unreleased candidate fixes a reproduced packet-parser rejection and a browser registration race. Offline tests pass; fresh-install live-camera acceptance is still pending. Eufy internet sign-in and signalling remain required. Keep an existing working camera setup in place. Independent project; not endorsed by eufy or Home Assistant. Optional support: https://buymeacoffee.com/efficientx

## Current status

The existing household engine and eight-view card have been verified separately.
That does not validate the replacement app. The replacement's hardware discovery
did not complete in its last alpha.6 trial. The local correction and card repair
need the acceptance gates listed in [release notes](RELEASE_NOTES.md).

[Published alpha.6 status](https://github.com/Efficient-X/Eufy-NVR-Local/releases/tag/v0.8.0-alpha.6) · [Release history](https://github.com/Efficient-X/Eufy-NVR-Local/releases)

## Two parts

The **app** receives the recorder's video. The **HACS integration** supplies HA
camera entities and its shared-player card. The software targets S4/T8N00; test
fixtures cover up to eight physical ports and sixteen lens tiles. This is not
sixteen-feed hardware acceptance or a guarantee of low CPU usage.

Read the [setup guide](docs/SETUP_GUIDE.md) and [acceptance checklist](docs/RELEASE_ACCEPTANCE.md)
before any isolated test. Keep a working engine and its data until a replacement
passes. Do not change credentials, recordings or security settings to bypass an
installation error. Never post tokens, passwords, camera pictures or HA backups.

## Support and licences

[Buy Efficient X a coffee](https://buymeacoffee.com/efficientx) if you wish. Support
is optional and does not buy guaranteed features or service. This project is not
affiliated with or endorsed by Anker/eufy or Home Assistant.

Original code retains the [MIT licence](LICENSE), including HallyAus attribution.
Adapted mega-yfue/eufy-sdk code retains Apache-2.0 and provenance under
`eufy_nvr/licenses/`. See [third-party notices](THIRD_PARTY.md). Original camera/
recorder artwork is MIT-licensed in [artwork/LICENSE](artwork/LICENSE).

This prep does not publish a version or change the existing alpha.6 tag/images.
