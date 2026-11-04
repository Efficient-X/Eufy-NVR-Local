# 0.8.0-alpha.8 - public experimental preview

**Published prerelease. Fresh-install live-camera acceptance has not passed.**

## Recorder test update - 4 November 2026

In the latest physical-recorder test, discovery reached its shared time limit
and startup stopped because no saved camera configuration was available. The
working household engine was restored. An earlier household build has shown
four dual-lens cameras as eight moving views with working enlargement. That is
a different build and is not a beta.1 download available from these releases.
It does not validate the public alpha.8 preview.

Alpha.8 remains available for experimental testing and feedback. Keep a working
camera installation until a replacement passes on your recorder, and do not run
two competing recorder video engines. See the [installation steps](README.md#install-the-experimental-preview)
and [report your results](https://github.com/Efficient-X/Eufy-NVR-Local/issues/new).
Do not post passwords, tokens, private account files, HA backups, unreviewed raw
logs or private camera pictures.

## What alpha.8 changed

Guided startup uses one shared 120-second discovery window with at most four
connection attempts. A synthetic 116-second successful start passes its timing
regression check. Status records and cancellation/cleanup have bounded paths.
Those offline and container checks do not prove real-recorder discovery or
live-camera playback. No new product fix is claimed by this documentation update.

The source tag and versioned images remain unchanged. This is an independent
Efficient X (EFX) project, not endorsed by eufy or Home Assistant. Optional support:
https://buymeacoffee.com/efficientx

[Alpha.8 release](https://github.com/Efficient-X/Eufy-NVR-Local/releases/tag/v0.8.0-alpha.8)

# 0.8.0-alpha.7 — experimental

**Experimental software. Not a stable release. Real-camera acceptance is pending.**

This version packages the reviewed dual-lens notification-parser correction,
camera-card registration repair, matching app/integration versions and EFX artwork.
The published alpha.6 tag, images and assets remain unchanged.

## Validation and limits

Check GitHub Actions for the exact tagged commit's native AMD64/ARM64 builds,
Python/Node tests, S6 startup checks and image/source audits. Local Windows
release-packaging checks passed, with symlink cases skipped where Windows could
not create the test links. These results do not prove household video playback.

Owner sign-in, recorder discovery, all fixed/tracking lenses, enlargement,
Galaxy/browser playback, latency/CPU, restart and recovery require real-system
acceptance. Keep the working engine. Do not run both video engines concurrently.
No three-minute recovery guarantee is made.

Eufy internet sign-in and signalling are required. Video is designed for the home
network; this is not a fully offline connection. Independent Efficient X (EFX)
project, not endorsed by eufy or Home Assistant. Free software; optional support:
https://buymeacoffee.com/efficientx

[This release](https://github.com/Efficient-X/Eufy-NVR-Local/releases/tag/v0.8.0-alpha.7)
· [Previous alpha.6 status](https://github.com/Efficient-X/Eufy-NVR-Local/releases/tag/v0.8.0-alpha.6)
