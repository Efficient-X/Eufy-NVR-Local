# Guided account setup: development contract

Status: local development only. The guided screen, protected aiohttp application,
startup coordinator and candidate Linux launcher have offline tests. They are not
enabled by the app manifest/Docker entry point, installed or published. Existing
headless startup remains the default. No household credentials were used in tests.

User-facing copy is written in plain language: each setting explains what to
enter, why it is needed and what it changes. See [the setup guide](SETUP_GUIDE.md).

## Setup state machine

`bridge/account_setup.py` provides `AccountSetup`:

1. `start(email, password, region=..., country=...)` attempts encrypted vendor login.
2. A human answers `captcha` using `answer_captcha(text)`, or supplies the emailed
   six-digit code using `verify_email(code)` when state is `email_code`.
3. `select_nvr` exposes opaque selection IDs and device names, never serials or
   authentication tokens. The user must explicitly choose a supported T8N00 NVR.
4. `select_nvr(selection_id, trusted_auth_path)` requires owner identity metadata
   for that NVR and atomically writes the existing account-bound private cache.
5. Success, cancellation and expiry clear references to sensitive session data.

Only `public_status()` may be serialized to a client. Never serialize the object's
attributes or log arguments, exception bodies, vendor responses or cache contents.
Device names are untrusted text and must be rendered as text, never HTML.

The default session lifetime is ten minutes. The web session manager checks expiry
at interactions and sweeps abandoned sessions every five seconds. The standalone
state machine still uses interaction-based expiry. Python reference clearing is
not a guarantee of cryptographic memory erasure. CAPTCHA images are bounded
PNG/JPEG data, not remote URLs or SVG.

Requests have a 45-second overall deadline and 15-second vendor operation timeout.
The object enforces five attempts per flow and a rolling five-per-minute budget;
cancel/start does not reset that budget. A provider rate-limit response imposes a
conservative 24-hour local cooldown, not a guaranteed provider retry interval.
The web factory also injects a private, persistent app-wide budget, shared across
users and surviving service restarts. It is deliberately stricter than a
per-account budget: it permits five total login steps per minute, and a vendor
limit pauses all setup logins for 24 hours. No email or account identifier is
stored in that budget. Reservations are written before a vendor request; damaged
or unwritable protection files fail closed. It requires a single service process.

## Implemented HTTP boundary

`setup_web.create_app` requires trusted administrator IDs, exact Home Assistant
origins, and fixed server-owned cache/budget paths. It rejects requests unless
their actual TCP peer is Supervisor (172.30.32.2) and the Supervisor-supplied user
is allowed. Forwarded-IP headers are not trusted. POSTs require JSON and an
allowlisted Origin; cross-site fetches are rejected. Session/CSRF secrets are
memory-only client headers, bound to that user, never query parameters or browser
storage. Body size is limited to 8 KiB. Setup responses use no-store and a
restrictive content policy. Device names render as text, not HTML.

This follows Home Assistant's [official ingress guidance](https://contributors.home-assistant.io/apps/guides/ingress/)
(accessed 2026-10-25): trust user headers only behind the Supervisor peer check,
use relative frontend URLs, and allocate an ingress port dynamically for a
host-network app. A production launcher must disable HTTP access logging.

Only one user can hold an active login/challenge/recorder-selection flow at a
time. Other approved users receive a plain conflict message. Cancelling an
in-flight attempt invalidates its result; a late vendor rate limit still persists.

## Startup and permission integration

`ha_setup_trust.py` retrieves only `config/auth/list` and `get_config` through
the Supervisor Core WebSocket proxy. It accepts active human owners/admins and
configured internal/external URL origins, not forwarded request headers. The
policy expires after 30 seconds; a failed refresh drops stale privileges.
Missing configured URLs fail closed rather than guessing aliases. These details
were checked against the official [Core proxy documentation](https://contributors.home-assistant.io/apps/supervisor-api/core/),
[user-list implementation](https://github.com/home-assistant/core/blob/dev/homeassistant/components/config/auth.py)
and [configuration fields](https://github.com/home-assistant/core/blob/dev/homeassistant/core_config.py)
on 2026-10-25.

This requires `homeassistant_api: true` at deployment. That permission is
administrator-level despite the code's read-only use; it has NOT been added to
the current app manifest or granted on the household installation. Installation
must explicitly disclose this expansion and receive scoped approval.

`startup_controller.py` validates the private v2 cache and checks the existing
Eufy sign endpoint, never the password-login endpoint. Valid cache starts one
owned media worker. HTTP 401/403 or the known rejection code enters the optional
remembered-login path or requires human reauthentication. Network failures,
rate limits and unexpected responses preserve
the cache. An existing running worker is retained on a temporary check outage.
Known expiry schedules earlier checks; the maximum normal interval is six hours.
Unknown/old expiry uses 15-minute checks; unavailable checks retry after 60 seconds.
Worker/discovery failures require an explicit retry, not a restart loop.

`guided_launcher.py` holds a single-process file lock, accepts only a
Supervisor-assigned ingress port (62000–65500), disables HTTP access logging,
and manages one Linux child process group. It checks that the video API port is
free before launching. Service-ready means the owned worker is alive and the
local port answers, not frame/playback verification. Shutdown targets only that
owned process group. All Linux process operations are mocked in Windows tests.

The existing `run.sh` gains an internal `EUFY_GUIDED_WORKER=1` branch that skips
legacy login/cache writes, browser-session import/restore and password-refresh
timers. Region is read from the saved cache. Supervisor/cloud-login secrets are
removed from the child environment and cleared again after `with-contenv`.
The normal entry point and legacy behaviour remain unchanged.

The HTTP setup rejects account writes while the runtime is starting/running or
its saved connection is temporarily unavailable. On save it schedules startup
without blocking the HTTP response. Runtime states are explained on the page.

## Required before enabling the guided version

- Build and verify the separately staged guided candidate and its dynamic
  authenticated ingress. The code obtains admin/origin trust server-side;
  no user-entered internal IDs should be needed. Staging is not installation.
- Exercise the implemented security boundary through actual HA ingress. Offline
  fake-Supervisor tests are not a substitute for production ingress acceptance.
- UI/service country allowlists currently cover 22 countries. These are valid
  country codes, not a claim of vendor acceptance testing in every country.
  Keep country separate from server group and expand only with appropriate tests.
- Validate token lifetime/user experience, opt-in renewal and challenge recovery.
  Known-expiry scheduling and bounded remembered sign-in are implemented.
  Verify the actual NVR expiry field and recovery timing; do not claim immediate
  recovery or uninterrupted playback from synthetic tests.
- Verify the implemented private health probe on real Linux/Supervisor. The
  candidate drops the go2rtc-only watchdog and uses Docker health; waiting for
  setup and handled provider failures are healthy, not reasons to restart.
- Verify the new first-time local-video credential flow in actual HA: explicit
  confirmation, app-options save/readback, app-only restart and integration
  connection. Offline tests are not production acceptance.
- Architecture builds, real Linux process cleanup, real-browser/ingress tests
  and an approved real login check remain outstanding.

## Candidate packaging and health checks

### Opt-in remembered sign-in (26 October 2026)

User approved offering this installer choice, OFF by default. A strict boolean
consent accompanies login. Only saving the explicitly chosen recorder persists
the email/password, in a `rememberedLogin` field alongside the account-bound
cache. One atomic owner-only file write commits both. Without consent, the field
is omitted (including when replacing a previously remembered connection).

This is private-file storage, not application-level encryption; admin/root or
backup access can reveal the password. The screen and user guide disclose that.
The media/cache-check helper strips remembered fields from its return value.
Public status exposes only remembered/paused booleans. No credentials enter URLs,
logs, browser storage, diagnostics or form defaults.

`remembered_login.py` acts near a known expiry or after token rejection, never
because of a temporary token-check outage. It persists a paused marker before reserving the shared retry
budget and sending a password request. Thus cancellation/crash cannot replay an
unattended login. It validates the same user and selected NVR administrator,
atomically saves a replacement token and checks that token before restarting the
owned media worker. Rejected replacement tokens also pause automatic renewal.

CAPTCHA/email verification, credential rejection, network/unknown errors and
storage/protection failures require human sign-in. Provider rate limits additionally
block the shared budget for 24 hours. There is no unattended challenge solver or
blind password retry. Password renewal may itself trigger one verification email.

The protected session/CSRF-checked Forget action removes only remembered login
fields, preserving the live token and local-video credentials. A synchronous
compare-before-replace prevents an in-flight renewal from restoring forgotten
credentials in the single launcher process. External edits to private account
state are unsupported; Python reference clearing is not guaranteed secure erasure.
Older backups are not modified. Cancelling an unfinished setup does not forget
previously saved credentials; the separate Forget action does.

Research evidence: the [upstream client's renewal code](https://github.com/bropat/eufy-security-client/blob/master/src/http/api.ts)
uses a fresh password login, not evidence of a password-free NVR refresh endpoint.
The inspected official-web index had no matching refresh-token call; that alone
does not prove no such endpoint exists. The local SDK reader recognises optional
token expiry metadata. The optional token_expires_at field is now preserved only
when it is an unambiguous Unix-seconds integer (or a ten-digit ASCII integer
string) in the supported 2020–2100 range. Milliseconds, durations, booleans and
ambiguous values are ignored rather than guessed. Actual NVR response semantics
still need live acceptance; the mobile client is supporting evidence, not proof
that every NVR firmware returns the same field.

The wizard and renewal persist valid expiry metadata. Renewal removes a stale
expiry when the new reply omits it. With opt-in eligible renewal, a valid token
within five minutes of its supplied expiry permits one attempt. The existing
worker continues during that attempt and only restarts after the replacement
token passes validation. If validation is temporarily unavailable, it keeps the
worker and retries the check; the pending restart happens after later validation.
Challenges leave existing valid video running while automatic attempts pause.

lastRenewalAttemptAt is persisted before a password request. It enforces a
one-hour minimum between automatic attempts across restarts, in addition to the
shared request budget and failure-pause marker. Unknown/past expiry uses a
15-minute check interval; known expiry sets an earlier check (60-second minimum,
six-hour maximum). Disabled or paused remembered sign-in schedules the expiry
check without a proactive password request. Unexpected early revocation or wall
clock changes can still interrupt service; this is not an instant push signal.

Tests use synthetic accounts. A full-suite run exposed an older test replacing
the shared cloud module, leaving earlier imports outside the intended mock.
That test now uses the canonical module; the suite additionally rejects external
DNS/socket connections and reports attempted use even if application code catches
the socket error. Subsequent full-suite tests passed with this guard enabled.

### First-time local-video credentials (26 October 2026)

Only the staged config has `setup_mode: guided`. The launcher reads its bounded
startup options without returning secrets and waits in `local_video_setup_required`
if local credentials are missing/invalid; no vendor check or media process starts.
The UI directs the user to the matching integration's installed-app flow.

`app_connection.py` offers initialization only for a running ingress-enabled
guided app with an explicitly empty local password and no legacy cloud-login
configuration. The integration asks for confirmation before generating a
256-bit random password, saving app options, reading back and restarting that
selected app. Existing/nonempty passwords are never replaced, even if invalid.
No new app-level Supervisor write permission is requested. The integration uses
the existing Core Supervisor client, not a browser credential-sharing endpoint.

The helper preserves unrelated options; a second read catches intervening
changes before writing. Supervisor's options write is not conditional/atomic
against an external writer, so simultaneous manual configuration edits remain
unsupported. Per-app locks serialize these flows in one HA process. Existing
entries for the app or matching local endpoint block initialization. An uncertain
write/readback/restart result does not trigger a blind retry or password rollback.
The fixed error instructs the user to check settings and restart only that app
if necessary; no credential is returned in a form or log.

The API names and `AddonsOptions(config=...)` model were checked against the
[official Supervisor client](https://github.com/home-assistant-libs/python-supervisor-client/blob/main/aiohasupervisor/addons.py)
and [Supervisor options implementation](https://github.com/home-assistant/supervisor/blob/main/supervisor/api/apps.py)
on 2026-10-26. Real HA write/restart behavior is not yet acceptance-tested.

The stager now also includes an allowlisted matching integration, with both
staged versions set to the same development number. It excludes that integration
from the Docker context. Neither main release version is changed.

### Build and health

The default Docker target and main config remain legacy. An explicit `guided`
target uses `guided-entry.sh` to read the assigned ingress port and exec the
launcher. `tools/stage_guided_candidate.py` writes a new, source-only test folder
from an explicit file allowlist. It refuses an existing destination and source
mirror mismatches. Its generated config enables admin-only ingress/API access,
uses manual boot, omits cloud-password options and removes the old TCP watchdog.
The generated Dockerfile selects the guided target. No deployment or publishing
is performed. See [the candidate test procedure](GUIDED_CANDIDATE.md).

The launcher atomically writes a private `/run/eufy-guided-health.json` containing
only the assigned port and a fresh per-launch nonce. `--health` validates this
small file, requests only loopback `/healthz`, disallows redirects/environment
proxies, uses a three-second timeout and accepts only the exact success object.
It never prints failure bodies. Metadata is removed while still holding the
launcher lock on exit. Stale metadata alone cannot yield a successful probe.

The HTTP health route requires actual loopback TCP peer plus the nonce; it does
not accept forwarded identity, Supervisor ingress, LAN clients or a browser
session as a substitute. It bypasses HA trust lookups so a Core outage does not
restart an otherwise functioning service. The account routes remain protected.

Health checks the session-expiry task (30-second heartbeat allowance) and control
watch task (300 seconds, allowing bounded account checks, a 45-second sign-in
renewal, worker cleanup and 120-second media startup). Completed/cancelled tasks, stale heartbeats, or app
shutdown fail health. Handling setup waits, provider outages and explicit media
retry states succeeds. Cleanup gathers task failures before clearing sessions
and stopping the owned worker. None of this proves video freshness.

CI now describes native amd64 and ARM64 runner builds for both the normal image
and guided target. These jobs have not been executed by this local preparation.

## Shared-card configuration generator

`bridge/dashboard_config.py` projects a validated `grid_layout.json` into a single
`custom:eufy-shared-tiles` card. It supports 2x2, 3x3 and 4x4 source grids, up to
16 lens tiles, unique positions, matching device/lens totals, optional explicit
channel ordering and adjacent fixed/tracking pairs. It preserves the single-player
design rather than creating one decoder per tile.

Example (use the actual shared-grid entity and generated layout):

```sh
python bridge/dashboard_config.py grid_layout.json --entity camera.example_shared_grid
```

Output is JSON, which is also valid YAML. It does not write a dashboard or modify
existing household cards. Only presentation fields are projected from the layout.

## Evidence and limits

### Automatic dashboard card — 27 October 2026

The companion integration bundles the card JavaScript, serves only that file
through HA's async_register_static_paths and adds a content-versioned module URL
using frontend.add_extra_js_url. A shared lock/refcount prevents duplicate
registration; the last entry unload removes the module URL. No dashboard storage
is edited. The frontend dependency ensures the module API is initialized.
These APIs were checked against official Core 2026.10.2 source.

The picker offers custom:eufy-nvr-cameras with a recorder selector. Its distinct
type avoids collision with older manually installed shared-tiles code. Legacy
explicit layouts still work. Automatic layout uses the selected shared-grid
camera's eufy_grid_layout attribute. Missing/invalid data does not produce
guessed crops. Identical state updates do not restart the player.

The generator projects only dimensions/names/indices into a dedicated,
content-addressed directory containing one JSON file. Each config refers to its
own immutable directory, keeping an older running stream's layout unchanged
while a replacement is generated. Hash mismatches, links and unexpected
directory contents fail closed. Old directories are not deleted while a running
config might still reference them.

go2rtc v1.9.14 serves this directory using its static handler. The path allowlist
adds the static root handler but still excludes config/log/restart handlers.
The served directory never contains private configuration/state. Existing LAN
Basic authentication and the existing trusted-loopback bypass are unchanged.
HA's reader uses local credentials, refuses redirects, caps responses at 64 KiB
and waits at most two seconds. Only validated presentation fields become camera
attributes. Older apps without metadata can still provide video.

Tests include a real, hash-verified official Windows go2rtc v1.9.14 binary,
bound to loopback with only the API module and no streams. The test forces
authentication even on loopback, checks the generated metadata and rejects
unauthenticated reads, private-file/traversal/config/control requests. The owned
process is stopped in a finally block. This does not prove Linux image behaviour,
real HA frontend compatibility, NVR playback or tablet performance.

Offline tests cover all eight physical-camera counts, dual/single-lens layouts,
ordering, malformed descriptors, challenge handling, cancellation, expiration,
cache identity, bounded retries and redacted failures. Tests use synthetic data.

Four physical dual-lens cameras were verified previously; eight physical units
and sixteen concurrent live lens feeds are not hardware-verified. Further
performance, tablet and soak checks were deferred by the user and remain
unverified. Both architecture builds, ingress security, dependency/vendor-asset
licensing review and publication approval remain release gates.
