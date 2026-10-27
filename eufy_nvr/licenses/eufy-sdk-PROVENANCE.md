# eufy-sdk adaptations

Upstream: https://github.com/mega-yfue/eufy-sdk
Release reviewed: 0.5.0, commit `5f92bea559fa1a5b619a84a9966ccc90a541735e`.
Accessed: 27 October 2026. Copyright 2026 mega-yfue.
Licence: Apache-2.0; complete text in `eufy-sdk-APACHE-2.0.txt`.
No separate upstream NOTICE file was present at this commit.

Adapted source:

- `src/transport/rtc/ptcs-framer.ts` → `bridge/ptcs_transport.js`.
  Converted to CommonJS for Node 18. Added strict input checks, limits on frame
  size, incomplete frames and buffered bytes, conflicting-packet rejection, and
  the bridge's existing complete logical-channel mapping in its adapter.
- `src/core/crypto.ts` → `bridge/eufy_crypto.py`.
  Ported ECDH P-256, AES body/password encryption and request signing to Python's
  cryptography library. Retained the bridge's public function signatures and
  added length validation. Protocol bootstrap constants are public protocol
  inputs from that source, not household credentials.

The upstream framing module describes itself as a clean-room implementation.
EFX relies on the published Apache licence for these adaptations; this project
does not claim to have performed its own independent clean-room process.

Historical builds downloaded Eufy's proprietary JavaScript/WebAssembly framing
files. This candidate replaces that runtime and does not download, load or
include those files. Historical Git commits and private diagnostic images may
still contain earlier implementations. The original cloud cryptographic and
request wrappers were also replaced with SDK adaptations; `cloud_transport.py`
ports the key-exchange and signed-request flow from `mega-client.ts` and
`passport.ts`. It uses injected Python HTTP calls, no automatic login/retry,
compatibility envelope returns, and redacted errors. Discovery/login routing
and challenge handling remain project-specific compatibility code. This is
not a claim that all historical source was independently authored or that
every legal question has been settled.
