# Guided setup: developer test package

**Not a public release or an approved household upgrade.** This package is for
a fresh, isolated Home Assistant test installation. It uses the same app slug
and video ports as the existing app. Do not install it beside, or over, the
working household app without a separate migration plan and approval.

## 27 October 2026 protocol replacement

Candidates dev.9 and dev.10 replace the vendor framing JS/WASM and the historical cloud
crypto/request wrappers with attributed Apache-2.0 SDK adaptations. The image
build and isolated Linux lifecycle checks passed on amd64. This is still a
hardware acceptance candidate: a live owner sign-in and real NVR feeds have not
been exercised with this replacement. It is not installed in the household.
See [PUBLISHING.md](PUBLISHING.md) for the planned source-first EFX release.

## What this package changes

The guided build opens a setup page inside Home Assistant. The final release
stage in its Dockerfile selects the startup mode; the sealed guided package
and the public source preview use the guided stage. It waits for an administrator to sign in
to Eufy and choose the recorder before starting the video service.

The guided package requests **Home Assistant API access**. The code uses this
to read the administrator list and the saved Home Assistant addresses. This
lets it check who may use setup, without asking the user to find technical IDs.
However, the permission itself is administrator-level, not read-only.
Disclose and approve it before installation. Do not turn off Protection mode.

## Prepare it on a development computer

From the repository root, with Python and PyYAML installed:

```sh
python tools/stage_guided_candidate.py --output ../eufy-guided-test
```

The parent folder must exist and the destination must not already exist.
This creates a new source package; it does not build an image, contact Home
Assistant, install anything or upload files. It copies an explicit list of
source files. It does not copy auth caches, camera inventories or private options.
It includes the matching development integration under
`custom_components/eufy_nvr`. Both staged version numbers match; the source
release versions and installed household versions are unchanged.

Staging also writes `SOURCE_MANIFEST.json`: a list of exact file sizes and
SHA-256 fingerprints. Record the printed manifest fingerprint outside the
candidate folder. Before building or transferring the package, run:

```sh
python tools/stage_guided_candidate.py --verify ../eufy-guided-test --expected-sha256 RECORDED_FINGERPRINT
```

Replace `RECORDED_FINGERPRINT` with the 64-character fingerprint printed when
staging. The check reads files only. It rejects extra, missing or changed files.
Without the recorded fingerprint, it checks internal consistency only: someone
could change both a file and its manifest. This is an integrity check, not a
signature, malware scan, licence clearance or proof that the app works.
Linux shell scripts must use LF line endings; staging refuses CRLF scripts.

The package also carries a standard-library verifier for a separate Linux
machine that does not have PyYAML or the source repository:

```sh
python3 test_tools/verify_candidate.py . --expected-sha256 RECORDED_FINGERPRINT
```

Run it from the candidate folder, using the fingerprint recorded on the original
machine. It assumes the verifier itself is trusted. The private test tools are
excluded from the app image; the lifecycle check is mounted read-only when run.

To build it on a test machine with Docker:

```sh
docker build --tag eufy-nvr-guided-test ../eufy-guided-test
```

A successful build is not proof of working sign-in or video. Both amd64 and
aarch64 builds and the real test steps below must pass before release.

CI also runs `tools/linux_candidate_smoke.py` inside each built image with
external networking disabled and synthetic temporary state. It starts a tiny
local test worker, checks that a second start does not duplicate it, then checks
graceful cleanup, an unresponsive child and a child in a separate process group.
It exercises the real HTTP health probe and checks that health access cannot
open the account setup page. These checks are not a live-camera or HA ingress
test. A separate image check sends a container stop through its S6 init process.
Windows explicitly skips the Linux process test; a skip is not a pass. The
amd64 image has passed the real Linux process/health smoke test and S6 stop-path
checks. The licensed replacement dev.10 also passed the process/health check.
ARM builds and actual Supervisor/live-camera acceptance remain outstanding.

The launcher requires Linux pidfd support to track its own processes safely. It
will refuse to launch the camera worker on an unsupported kernel rather than
guess which processes to stop. The guided candidate requests a 60-second
Supervisor stop window so its bounded shutdown has time to complete.

## Before testing in Home Assistant

1. Use a separate test installation, not the working household system.
2. Review the API permission above. Set a correct internal Home Assistant URL
   in its Network settings. The app accepts only configured addresses, so it
   does not accidentally trust another website.
3. Install the matching staged integration on the isolated test installation.
   Start the candidate app manually, leaving its local video password blank.
4. Follow the first-time steps in SETUP_GUIDE.md: the integration offers an
   explicit confirmation to generate the first local password and restart only
   this app. Then use Open Web UI to sign in to Eufy, and return to finish the
   integration setup. No Eufy password goes in the app Configuration in guided
   mode. Do not rotate or copy any household password for this test.
5. Leave Start on boot off during development. Do not enable Watchdog until
   the health behaviour below has been checked in the test installation.

The existing integration still expects local video credentials in app options.
This package preserves that connection method; it does not introduce a new
secret-sharing channel.

The new integration action writes app options through Home Assistant's existing
Supervisor client, after confirmation. It does not grant the app additional
Supervisor write permission. It preserves unrelated options, checks the selected
app again, reads back the saved settings and then restarts only that app.
Supervisor does not provide a conditional atomic options write here, so avoid
editing the app Configuration concurrently. The code blocks duplicate setup
actions within this Home Assistant process and refuses an existing integration
using the same app or matching local video endpoint.

## Why waiting for sign-in is healthy

A health check asks, "Is the app itself still responding?" It does not ask,
"Is a camera showing video right now?"

The guided Docker check calls a private loopback endpoint in the same app.
A new random nonce binds the check to the current launch. The endpoint checks
the control and session-expiry tasks; it does not contact Eufy, read account
details or create a camera connection.

- Waiting for a password, picture challenge or email code is healthy.
- Eufy being unavailable, or a video startup error shown with a Retry button,
  is also healthy: restarting the whole app does not solve those conditions.
- A dead control task, stuck control loop or unresponsive web server is unhealthy.
- Healthy does **not** mean the picture is live or recent.

The candidate removes the old go2rtc TCP watchdog URL and uses Docker health
instead. Supervisor can act on unhealthy-container events when its Watchdog
switch is enabled. The dynamic ingress port is not inserted into an invented
watchdog URL. See the official
[configuration reference](https://contributors.home-assistant.io/apps/reference/configuration/)
and [Supervisor watchdog implementation](https://github.com/home-assistant/supervisor/blob/main/supervisor/apps/app.py)
(reviewed 25 October 2026).

## Test before approving a household upgrade

- Build both supported architectures; test real Linux startup and shutdown.
- Leave setup unfinished: it must remain healthy without starting video.
- Confirm non-admin and direct-LAN requests cannot use setup.
- Confirm local-password creation is offered only for a blank guided app, asks
  before acting, preserves unrelated options and never rotates a nonempty
  password. Confirm the restarted app loads the generated password and the
  integration can read it without exposing it in the form or diagnostics.
- Complete sign-in through actual Home Assistant ingress, including a challenge
  if Eufy requests one. Verify the correct recorder and its real lens layout.
- Check stop/restart cleanup: no orphaned video worker may keep the ports busy.
- Check a temporarily unavailable account service does not cause a restart loop.
- Confirm an expired token asks for sign-in again, rather than repeated guessing.
- Verify the integration connects with the separate local video password.

Optional remembered sign-in is now implemented with explicit consent (off by
default), private persistence, a Forget button and one bounded renewal attempt
near a known expiry or after a saved-token rejection. A challenge, failure or uncertain response pauses
automatic attempts across restarts. Existing private files/backups may contain
the remembered password; the app itself does not encrypt it.

Verify opt-in/off, forgetting, challenge pauses, same-account/recorder checks
and token replacement through real HA before release. The app now retains a
valid Unix-seconds expiry field, schedules checks near it, and uses 15-minute
checks when expiry is unknown. Automatic password attempts have a persistent
one-hour minimum interval. A still-valid worker stays running during proactive
renewal; after validation it restarts once to load the new token. Verify actual
vendor expiry semantics and recovery timing; offline tests do not prove
uninterrupted video or successful real Eufy renewal.

The eight-physical-camera capacity is software-tested, not hardware-proven.
The household has verified four dual-lens units. Tablet, extended performance
and soak tests were deferred and are not being silently marked as passed.

## Stop or undo a test

The matching integration now bundles and loads the card from its own static
path. The card picker offers **Eufy NVR cameras**, with an NVR-group selector.
Validated grid metadata is fetched from the app and attached to the shared-grid
camera, so the new card can arrange all views automatically. It does not create
or overwrite a dashboard. Actual HA registration, picker and playback acceptance
remain required. The separate frontend source is retained for developer use.

Read `THIRD_PARTY.md` and `PUBLISHING.md` before distributing source or images.
The candidate no longer downloads or loads Eufy framing JS/WASM; it uses the
attributed Apache SDK adaptation. The chosen initial release distributes source
only. A later prebuilt-image release still needs its complete dependency notices
and source-obligation review. Real sign-in and camera acceptance are not implied.

Stop the candidate in the separate test installation. Do not delete auth or
retry-protection files to bypass a cooldown. No household rollback is needed
because this procedure makes no household changes. An in-place upgrade would
need its own backup, preservation of private state, and a documented rollback.

