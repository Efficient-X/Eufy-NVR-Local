# Draft 0.8.0-alpha.8 - bounded camera startup

This source candidate fixes an early startup timeout. Guided startup now has one
shared two-minute discovery window and up to four connection attempts, instead of
two nested groups of three attempts. A valid slow discovery can finish after the
old110-second limit. Normal success returns as soon as the video service is ready;
it does not wait for the deadline. Local setup and cleanup have finite limits too.

Setup shows the last startup step and keeps only fixed, secret-safe status labels.
A local connection that does not answer is not described as a bad Eufy password.

This is a draft development candidate. Native Linux/S6, image-build and physical
recorder checks are required before release or household deployment. The reproduced
timing defect has not been established as the cause of a specific failed installation.

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
