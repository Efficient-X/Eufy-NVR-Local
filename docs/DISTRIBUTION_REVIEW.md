> Historical review of the earlier vendor-based build. See [PUBLISHING.md](PUBLISHING.md) for the current replacement and source-first release decision.

# Distribution review — 26 October 2026

This records release evidence and unanswered questions. It is not a legal
opinion or a claim that public release is cleared. No vendor has been contacted.

## What the build includes

The Dockerfile runs `bridge/fetch_deps.js`, which downloads four Eufy files from
`https://security.eufy.com/plugin/` into the image. They are not present as
tracked binaries in the source tree. Being absent from Git does not mean they
are absent from the final image. The names and component inventory are in
[THIRD_PARTY.md](../THIRD_PARTY.md).

The same build downloads go2rtc v1.9.14 and installs Debian/Python packages.
The versioned go2rtc MIT notice is now retained under `eufy_nvr/licenses/` and
copied into the image. Its source is the upstream v1.9.14 tag; SHA-256:
`b0dcf4855af5a72b4dfbd9117c207b330f4cc35658576a0b5351d6e2becac546`.
That notice alone does not complete the dependency audit.

`bridge/eufy_cloud.py` describes part of its implementation as transcribed from
the vendor web bundle. Keep that provenance visible while investigating. A
translated or reformatted implementation is not automatically independently
authored code. Do not remove the comment merely to make a scanner pass.

## Evidence and decision

Eufy's [published terms](https://www.eufy.com/policies/terms-of-service), section
6, restrict redistribution unless it is expressly allowed. The current inventory
contains no verified grant covering the four downloaded files. This does not
determine the application of regional terms or interoperability exceptions.
It does mean we cannot describe the complete package as cleared for distribution.

Next acceptable routes are documented permission, a confirmed applicable
licence/distribution basis, or an appropriately licensed replacement with full
regression testing. Moving the same download to first startup is not proof that
the permission issue is resolved. Do not silently replace the working transport
or remove its features simply to ship a smaller product.

## Draft question for Eufy — not sent

> We are preparing an independent Home Assistant integration for owners of the
> T8N00 PoE NVR. It uses each owner's normal Eufy sign-in and does not bypass
> account verification. The bridge currently uses libsctp_0_0_4.js,
> libsctp_0_0_4.wasm, worker_sctp_send_0_0_4.js and
> worker_sctp_recv_0_0_4.js from your public web-client plugin directory.
> Could you confirm the licence and whether we may redistribute these files
> unchanged in an open-source Home Assistant app image? Please specify required
> notices, source availability or other conditions. We also need to clarify
> permission for the web-protocol implementation derived from your client.
> If this is not permitted, is there a supported SDK or local streaming interface
> with terms suitable for this integration? The project may offer voluntary
> donations; access to the software would not require payment.

Sending this requires the user's explicit instruction; no account or household
details should be included. The user can instead obtain independent advice on
the specific distribution basis. Keep any nonpublic correspondence private.

## Build and release evidence still needed

- Build the exact staged package on amd64 and aarch64, then record all installed
  packages and their applicable notices/source obligations. CI is prepared to
  build that package and list versions; it has not run for these changes.
- Actual HA installer, ingress, restart and remembered-sign-in acceptance;
  automatic card registration/layout hand-off now have local implementations
  awaiting real HA acceptance. Expiry scheduling is implemented locally and
  also awaits verification of actual NVR login replies and recovery timing.
- Repeat secret/provenance review of the final exact outgoing source and Git history
  after any further changes. The 27 October preliminary scan covered the staged
  candidate, outgoing worktree source and all 28 locally available commits.
  Findings were static vendor protocol constants and explicit unit-test
  passwords; no household credential was identified by that scan. This is not
  a guarantee that no secret exists. The protocol constants remain subject to
  the provenance/distribution review above; they were not blanket-ignored.
- Final public release approval and an authenticated publishing route.

This PC has no available Docker/Podman executable, and `wsl --list --quiet`
reports WSL is not installed. No system software was installed to work around
that. The connected GitHub account has read access but no push permission on
HallyAus/Eufy-Home-Assistant, which is public. Other delivery routes have not
been assumed to be authorised merely because this connector is read-only.

The lack of a local image builder does not prevent further source work. This
review is an incomplete release checklist, not a claim that the goal is blocked
or complete. Previously deferred performance tests remain unverified.
