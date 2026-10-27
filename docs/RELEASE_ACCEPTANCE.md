# From development package to a usable release

Status: **28 October 2026 - experimental source publication authorised; stable
release acceptance remains incomplete.** The latest sealed build candidate is
`0.8.0-dev.10`; subsequent documentation/source corrections are being prepared
for a private `0.8.0-alpha.1` repository preview. Household apps are unchanged.

Completed evidence includes amd64 builds, isolated Linux lifecycle/health,
synthetic protocol compatibility, isolated Core integration checks and automated
suites. These do not replace real sign-in, Supervisor or new-port camera tests.

## What still has to happen

| Step | Why it matters | Evidence required |
| --- | --- | --- |
| Build on amd64 and aarch64 | Home Assistant computers use different processor types. Source tests alone cannot prove both packages start. | Successful build and smoke-test records for each exact package. |
| Test a fresh Home Assistant installation | A new user has no saved passwords, camera items or dashboard code. | Complete the installer from an empty setup, without copying technical IDs or tokens. |
| Test real sign-in and live video | A pretend login in a test cannot prove that Eufy accepts the real request. | Owner account sign-in, any requested verification, recorder selection and correct live lenses. |
| Test access renewal | Saved access eventually expires. | Optional remembering, successful renewal and a safe pause if verification is needed. |
| Prepare an existing-install upgrade | A new installer must not lose a working household's connection. | Separate migration, private-state preservation and rollback rehearsal. |
| Preserve source licences and image obligations | The new port uses attributed Apache SDK code. | Initial publication is source-only; follow PUBLISHING.md. A later prebuilt image needs its full component notices/source review. |
| Publish the reviewed source preview | A clear experimental label prevents it being mistaken for hardware acceptance. | Human publication approval is recorded; review the outgoing source inventory, upload to staging and verify the resulting commit. |

“Source tests passed” does not mean all rows above have passed. Eight physical
cameras / sixteen lenses remain software-tested capacity; the available
household has four physical cameras / eight lenses. Previously deferred tablet,
performance and long-running tests remain unverified.

## Build records

1. Stage a new source folder using `tools/stage_guided_candidate.py`. Keep its
   printed manifest fingerprint separately. Do not reuse an older output folder.
2. Verify the folder against that fingerprint immediately before building.
3. Build privately on a machine intended for development. Do not use the live
   household recorder or Home Assistant as a build server.
4. Run the import and Linux lifecycle checks in `.github/workflows/ci.yml`.
   The lifecycle test uses a tiny pretend worker and loopback networking only.
   It must not use a household password, recordings or camera feed.
5. Record candidate fingerprint, architecture, image ID, build result and test
   result. Record installed Debian/Python packages from the built image. These
   are dependency evidence, not automatic licence clearance.
6. Keep the build logs private until checked. Do not upload private configuration
   files or use credentials as Docker build arguments.

The same source fingerprint may produce different image IDs on different
architectures. Dependency repositories also change over time, even where the
base image and Python versions are pinned. Do not describe the final image as
bit-for-bit reproducible until that has actually been demonstrated.

## Fresh-install acceptance: isolated Home Assistant

Use a separate test Home Assistant installation. The candidate deliberately uses
the same app slug and video ports as the working version, so it is **not** a
side-by-side household install.

Keep a results sheet with `PASS`, `FAIL` or `NOT TESTED` for each row. Record only
the result and non-sensitive version numbers. Never record sign-in values.

| Action | Expected result and reason |
| --- | --- |
| Install the matching app and integration; leave the app's local password empty. | The app waits. It must not start an unprotected video service or repeatedly log into Eufy. |
| Open setup as an HA administrator. | The setup page opens through HA. Normal non-admin users and direct LAN requests cannot use it. |
| Select the app from Add integration. | The user sees an unchecked confirmation to create the first local video password. This protects video without requiring password copying. |
| Cancel that confirmation. | No password change and no restart. |
| Confirm it once. | A random local password is saved, unrelated settings are preserved, and only this test app restarts. |
| Repeat the step after a successful save or an uncertain reply. | It must not replace an existing password. A timeout should explain that the save may already have happened. |
| Sign in through Open Web UI. | Password fields start empty. Remembered sign-in starts off. Complete Eufy's actual verification if requested. |
| Choose the intended supported recorder. | Account ownership is checked. Saving starts the app's camera service. No recorder settings or recordings change. |
| Finish the integration setup. | It reads the app's local password and creates the camera connection without asking the user to copy a token or serial number. |
| Add the Eufy NVR cameras card. | The recorder selector appears; names and layout arrive automatically. No manual resource registration or crop geometry is needed. |
| Open every available lens and enlarge each tile. | The correct fixed/tracking picture appears; enlargement reuses the same video player. A running service alone is not a live-video pass. |
| Close or hide the card. | Its player is released. Counted connections should return toward idle after the configured short warm period. |
| Stop and start only the test app. | Its own worker/children stop and its ports become reusable. HA and other apps stay running. |
| Download integration diagnostics from this test setup. | Export contains counts and generic status, not passwords, host addresses, camera names, serial numbers or raw error text. |

Do not deliberately submit wrong real passwords to manufacture failure cases.
Use the existing synthetic tests for those cases; handle a real challenge if
Eufy presents one naturally. Do not delete the retry-protection file to speed
up a test or bypass a cooldown.

The guided candidate uses Linux process handles to track its own worker and
nested descendants during shutdown. It gives those processes time to close
cleanly, then targets any still-tracked processes. It requires Linux pidfd
support and refuses to launch a worker when that support is unavailable. The
candidate also asks Supervisor for a 60-second stop window and configures S6 to
forward the stop signal to its coordinator. These changes have synthetic tests;
actual image/S6 behaviour and real camera shutdown remain NOT TESTED until
observed. A sudden process crash or kernel failure is not proven fully covered.

## Remembered sign-in acceptance

Start with remembering **off**. Restart the test app after successful setup:
the saved access token may be reused, but no Eufy password should be retained by
this new wizard. Then perform a separately agreed test with remembering **on**.

Confirm that the real login reply either supplies the expected Unix-seconds
expiry or omits it. Do not guess an expiry value from the current clock. Record
only whether the field exists and whether the scheduling rule was followed.

When a usable expiry approaches, the app attempts renewal within its bounded
window. Existing valid video should remain running until a replacement token
is validated, then its worker reconnects once. If the reply is uncertain, or
Eufy requires a challenge, automatic password attempts must pause rather than
repeat on every restart. A full real expiry cycle may take longer than a short
acceptance session: leave its result NOT TESTED until observed.

Use **Forget saved sign-in** and verify that the current remembered credentials
are removed while the active token and recorder remain. This does not erase
older backups. No test should print private state to prove this; checks can
report field presence and state transitions only.

## Household upgrade: separate approval after the test install

This is a proposal, not an automatic migration tool or installation approval.
Keep the existing legacy launch path available until these steps are ready.

1. Read current app/integration versions, selected app slug, port assignments,
   current status and available backup coverage. Existing notes may be stale.
2. Identify the exact candidate fingerprint and built image, plus the matching
   integration. Describe expected short camera downtime and the new
   administrator-level HA API permission before requesting approval.
3. Make and verify the required fresh full backup, unless the user explicitly
   waives it for this upgrade. Confirm whether it includes the NVR app's private
   data; a backup that omits the app is not an NVR restore point.
4. Preserve the old image/source and integration version, app options, private
   account state and retry state locally. Never copy them into the repository
   or a support attachment. Do not silently remove legacy saved credentials.
5. Determine whether the existing cache passes the guided format checks without
   changing it. Format compatibility alone does not prove Eufy still accepts it.
   If it does not pass, plan an explicit owner sign-in; do not fabricate fields.
6. Rehearse the actual option-schema transition in the isolated installation.
   The candidate removes legacy login options; Supervisor's treatment of saved
   extra options must be verified. Do not guess that changing the version alone
   safely migrates them.
7. Install the agreed app and matching integration in small reversible steps.
   Preserve current local video credentials and port overrides. Do not run the
   old and new app against the recorder at the same time.
8. Verify existing entity IDs, legacy dashboard cards and all eight available
   lens views. Add the automatic card only where approved. Stop if these differ
   from the agreed plan; do not keep changing unrelated HA settings to compensate.

Rollback must restore a matching old app/integration pair and the appropriate
private state/options. A newly issued Eufy token may change which old token is
accepted, so restoring files alone is not proof that playback will recover.
Validate the restored connection; if it needs owner sign-in, explain that rather
than repeatedly trying passwords. Do not reinstall the whole of HA to undo one
camera-app trial.

## Public release gate

Before publishing, rescan the exact final source and the Git history being sent.
Secret scanners miss some things and flag some harmless test/protocol constants;
each finding needs review. Do not hide an unresolved finding using a blanket
ignore. Preserve required vendor provenance and notices.

Resolve the vendor distribution question before distributing a combined image.
No vendor message has been sent. A Buy Me a Coffee link needs the owner's actual
destination; do not invent one or imply that payment is required for camera use.

Use a development/pre-release label until the documented acceptance gates pass.
Publish only the reviewed source/image and instructions. Private research notes,
inventories, backups and raw diagnostics are not release assets.
