# EFX public release approach — 27 October 2026

EFX's chosen route is a source-first experimental release, with licensed
community protocol implementations and optional support through
[Buy Me a Coffee](https://buymeacoffee.com/efficientx). Asking Eufy for permission
is not being imposed as a prerequisite. No permission email has been sent.

## What other projects do

The older [bropat client](https://github.com/bropat/eufy-security-client) publishes
its community implementation under MIT, identifies itself as independent, and
links voluntary support. Its repository was archived in September 2026 and points
to [mega-yfue/eufy-sdk](https://github.com/mega-yfue/eufy-sdk).

The newer SDK publishes its implementation under Apache-2.0. Its native
`ptcs-framer.ts` describes a clean-room framer that does not ship the portal's
original framing module. Its licence, rather than the fact another integration
exists, is the concrete basis for our adaptation.

[ha-eufy-sdk](https://github.com/mega-yfue/ha-eufy-sdk) separates HA entities from
a bridge and go2rtc. The older
[eufy_security](https://github.com/fuatakgun/eufy_security) directs users to that
implementation. We cannot infer private permissions or legal advice from their
repositories, or say their authors are unaware of an issue.

## What changed in this candidate

- Replaced the vendor JS/WASM framing runtime with an Apache-2.0 adaptation of
  SDK commit `5f92bea559fa1a5b619a84a9966ccc90a541735e`.
- Removed all four vendor framing downloads and their runtime checks. They are
  not moved to first startup.
- Replaced the historical vendor-derived cryptographic and signed-request
  wrappers with attributed Python adaptations of the same SDK.
- Preserved the Apache licence, source reference and modification record in
  the app's `licenses/` directory. Original MIT notices remain; imported files
  retain Apache-2.0.
- Keep the physical NVR, normal owner authentication, account challenges and
  shared-video design. No authentication bypass is introduced.

The framer matched the previous private runtime's packet bytes for 28 synthetic
control/live cases, including 128 KB frames. This does not prove real camera
playback or live account login with this port. Hardware acceptance and ARM
builds must be reported separately.

## Source first

Publish reviewed source and installation instructions first. Home Assistant
builds the app on the user's machine, downloading open-source dependencies from
official sources. Do not initially publish a prebuilt container image. The
source licence does not cover an entire Debian/FFmpeg/PyAV/go2rtc image; a later
image release must meet the actual components' notices and source requirements.

Use an explicit file list excluding household files, vendor bundles, captures,
private backups and diagnostic images. Earlier research remains outside the
new repository. A fresh repository does not change the origin or rights of
copied code; historical provenance stays recorded.

This is a practical distribution choice, not a guarantee against a legal claim.
Community implementations, disclaimers and tips do not remove all contractual,
copyright or trademark questions.

## Identity and support

Use “Efficient X (EFX), an independent open-source project maintained by
Brendon”. Do not describe EFX as incorporated or imply Eufy approval. Software
stays free; tips do not buy features or guaranteed support.

No revenue does not settle Australian business status. The
[ABR](https://www.abr.gov.au/business-super-funds-charities/applying-abn/abn-entitlement)
says an enterprise can qualify while starting. If carried on as a business
under EFX's name,
[ASIC's business-name rules](https://www.asic.gov.au/for-business-and-companies/business-basics/sole-trader-partnership-company-trust)
apply; incorporation is a separate choice. The
[coffee platform's terms](https://buymeacoffee.com/terms) require accurate payout
and tax details. Do not describe support as tax deductible or automatically
tax free.
