# Unreleased review candidate — Eufy NVR Local

**Not a stable release. No version/tag has been assigned or published for this prep.**

Eufy NVR Local by Efficient X (EFX) is an experimental Home Assistant app and integration for Eufy PoE recorders. It aims to bring fixed and tracking feeds into shared camera tiles that open into larger views. The unreleased candidate fixes a reproduced packet-parser rejection and a browser registration race. Offline tests pass; fresh-install live-camera acceptance is still pending. Eufy internet sign-in and signalling remain required. Keep an existing working camera setup in place. Independent project; not endorsed by eufy or Home Assistant. Optional support: https://buymeacoffee.com/efficientx

## Tested offline

- Four synthetic notification pipelines for four/eight dual-lens ports fail on
  the immutable alpha.6 parser and pass with the correction; packet bounds remain.
- Actual Chrome reproduces the scoped-registry replacement failure; four local
  module-loading scenarios pass with the registration repair.
- 646 Python tests and 16 Python subtests pass; five environment-dependent cases
  skip. All 16 Node test units pass.
- Recovery simulations plus real Windows subprocess/loopback HTTP checks cover
  accepted timeouts, delayed starts and truthful deadline failures. They do not
  validate Home Assistant Supervisor completion fences or real-camera recovery.

## Still required before recommending a fresh installation

Native AMD64/ARM64/S6 tests for these exact bytes, isolated HA/Supervisor and
Galaxy/browser checks, real NVR discovery/all lenses, latency/CPU and restart/
bounded-fallback acceptance. Passing a synthetic test is not a working-camera
claim. Do not run two NVR video engines concurrently.

Current published alpha.6 status: https://github.com/Efficient-X/Eufy-NVR-Local/releases/tag/v0.8.0-alpha.6
That older release does **not** contain these unreleased fixes.
