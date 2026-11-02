# Unreleased offline candidate — 2 November 2026

This folder is a review candidate based on immutable alpha.6, NOT a new release.
Do not install it on a household system or publish it as alpha.6. Version fields,
image pointers and the original PUBLIC_SOURCE_MANIFEST describe that baseline,
not proof that edited files correspond to its published images. The separately
generated OFFLINE_SOURCE_MANIFEST identifies the actual reviewed candidate bytes.
No household settings, credentials, recordings, releases or tags were changed.

## Notification parser

Retains the prior isolated 0x0800 fix. Observed header facts are v3, notification
channel 2, flags 0x4e9b, 667-byte frame/695-byte packet. Synthetic payloads only.
The strict type nibble, size, chunk, duplicate/conflict, age and aggregate memory
bounds remain. It does not invent ACK/compression meanings for the flag.

`test_ptcs_discovery_pipeline.py` exercises the real Node JSON-lines adapter,
then AST-extracted **actual** Python on_frame/handle_devlist functions, atomic
manifest output and actual go2rtc layout generation for four/eight dual-lens
ports. Single and reordered/duplicated fragmented notifications are tested.
This is a synthetic component pipeline, not authenticated recorder discovery.
The real 667-byte notification body was never retained and its contents remain
unknown. Other real-NVR discovery faults may still exist.

## Actual browser registration failure and repair

Home Assistant Core 2026.10.4 pins frontend 20260930.5. Its app entry imports
`@webcomponents/scoped-custom-element-registry` 0.0.10 before home-assistant;
the index starts app and extra-module imports independently. The polyfill
replaces `window.customElements` and `window.HTMLElement`.

Local Chrome reproduction with that **actual**, integrity-verified npm polyfill:
alpha.6 defines the card successfully in the native registry; the polyfill then
replaces the registry; the card disappears from get(). Re-importing the SAME
module URL returns the cached ES module and does not re-run registration. This
explains why adding an otherwise valid Lovelace resource can fail to repair it.
It reproduces a matching mechanism; the household's precise failed browser
state has not been re-inspected, so this is not proof of its sole root cause.

The candidate keeps immediate registration, then checks the CURRENT registry
until HA's own home-assistant element exists and restores only missing Eufy
definitions. It preserves existing/legacy constructors, unrelated resources and
the picker entry, and neither reloads the page nor patches global registries.
Waiting is bounded to 60 seconds with a named console error if HA never becomes
ready. An unusually slow HA app bundle (>60 seconds) requires a page reload;
this guard does not promise indefinite automatic recovery.

Real Chrome checks: polyfill late/first, legacy coexistence, clean load, cached
same-URL import, different query URL import, construction, missing metadata,
eight tile render and one picker entry. No HA player, credentials or camera
connection is involved. No Safari/Galaxy hardware acceptance is claimed.

Reproduce locally: obtain npm's 0.0.10 polyfill separately, verify its registry
integrity, then run `python tools/frontend_smoke_server.py --polyfill PATH`.
Open its printed loopback URL with mode=clean, legacy, polyfill-first or
polyfill-late. Use a new `revision` query value after source edits to avoid an
old browser HTTP cache. Stop the loopback server afterwards. Vendor code is a
separate test fixture; it is not bundled in this candidate or production card.

Primary references (accessed 2 November 2026):
- https://github.com/home-assistant/core/blob/2026.10.4/homeassistant/components/frontend/manifest.json
- https://github.com/home-assistant/frontend/blob/20260930.5/src/entrypoints/app.ts
- https://github.com/home-assistant/frontend/blob/20260930.5/src/html/index.html.template
- https://github.com/home-assistant/frontend/blob/20260930.5/src/panels/lovelace/common/load-resources.ts
- https://github.com/home-assistant/frontend/issues/53890 (corroborating upstream report, not an instruction)

## Bounded recovery: tested decision model, not a production executor

`tools/recovery_plan.py` performs no networking or household actions. A simulated
Supervisor queues operations per engine and lets accepted operations continue
after a response timeout. It asserts that the engines never run concurrently.

The decision model journals each mutation intent before dispatch, uses monotonic
absolute deadlines, never repeats uncertain mutations, and resumes its journal.
It requires a known working baseline and stopped candidate before an outage.

| Absolute time from initial stable-stop request | Decision |
| --- | --- |
| 0 seconds | Record stable-stop intent once. |
| 20 seconds | If stable stop has not completed, abandon the candidate trial. |
| Before 55 seconds | Accept only completed candidate-start plus all required moving views. |
| 55 seconds | Request candidate stop once; do not wait for more 40-second discovery retries. |
| After fenced candidate-stop completion | Start the stable engine once. |
| 180 seconds | If full stable moving-view evidence is missing, report manual recovery required. |

Important contract: a sampled process state is NOT an operation completion
fence. An accepted start may still be queued while the process looks stopped.
An eventual executor must prove that prior operations completed/cannot run late
(for example a verified operation/job completion contract). No such HA adapter
is implemented or validated here. Without that evidence the model refuses to
start the other engine, even if this misses the recovery target. No API/service
security changes are proposed to obtain it.

Simulation includes lost/blocked commands, queued delayed start, accepted HTTP
timeout, stable start taking 60 or 150 seconds, insufficient moving views,
baseline rejection, clock reversal and journal resume. A 25-second candidate
stop plus 60-second stable start restores at 140 seconds. An unbounded stop or
150-second stable start correctly fails the 180-second budget. This is a
bounded **decision/reporting** design, not a guarantee that HA can recover in
three minutes. Caller scheduling, durable storage, command fences and actual
moving-frame checks must still be implemented/validated before household use.

## Next acceptance gates

1. Review candidate delta and hashes; choose a new unpublished version before
   any later packaging for distribution. Never replace alpha.6 assets/tag.
2. Native Linux AMD64/ARM64/S6 tests using this exact candidate, including stop
   during discovery. Existing alpha.6 native evidence does not cover this patch.
3. Validate recovery adapter receipts, timeouts, durable journal and startup
   fences against a non-household HA/Supervisor fixture before any live outage.
4. Isolated HA/browser cold-cache card loading on supported frontend plus Galaxy
   tablet, with delayed module loading and legacy-resource coexistence.
5. Only then plan a separately authorized recorder test: genuine device list,
   all physical ports/lenses, eight/16 moving crops, enlargement, latency,
   producer/CPU bounds, restart and pre-staged recovery. Do not infer actual
   NVR discovery or LAN-only video from synthetic tests.
