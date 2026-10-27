# Third-party software and release status

Reviewed 27 October 2026. This is an inventory of known dependencies, not a
completed licence clearance or a claim that every dependency has been audited.

The repository's MIT licence covers the project's own contributions. It does
not grant rights to Eufy assets or replace the licences of other components.

| Component | Where it enters the package | Current evidence / remaining work |
| --- | --- | --- |
| go2rtc v1.9.14 | Official release binary downloaded during the image build | Upstream MIT notice is preserved in `licenses/go2rtc-LICENSE.txt` inside the app source and image. Audit the binary's transitive dependencies for the final release. |
| PTCS framing and cloud cryptographic primitives | Adaptations of mega-yfue/eufy-sdk 0.5.0, pinned commit | Apache-2.0 licence and provenance are included under `licenses/`. The candidate no longer downloads or loads vendor framing JS/WASM. |
| Eufy-derived protocol implementation | `bridge/eufy_cloud.py` and its packaged copy | Historical source recorded transcription from the vendor web bundle. The primitives and signed HTTP request wrappers are now replaced by licensed SDK adaptations. Historical provenance remains documented. This is not an EFX clean-room claim or a legal guarantee. |
| Python dependencies | `bridge/requirements.txt` | Versions are pinned. Collect the exact built environment's package licences, notices and source obligations; a dependency name or classifier alone is insufficient. |
| Debian packages, FFmpeg and Home Assistant base image | Docker base image and apt packages | Preserve installed copyright information. Record package versions and satisfy applicable source/redistribution requirements for the actual image. The image as a whole is not simply MIT. |
| Browser card and companion integration | Project source | Included with the project licence; dependency and provenance review still applies to imported code. |

Historical builds used four vendor files: `libsctp_0_0_4.js`, `libsctp_0_0_4.wasm`,
`worker_sctp_send_0_0_4.js` and `worker_sctp_recv_0_0_4.js`.
They are no longer runtime dependencies of this candidate. Their historical source was
https://security.eufy.com/plugin/ .

Sources reviewed:
- [go2rtc v1.9.14 licence](https://github.com/AlexxIT/go2rtc/blob/v1.9.14/LICENSE).
- [Eufy's published terms, section 6](https://www.eufy.com/policies/terms-of-service):
  redistribution is restricted unless material is expressly made available for it.
  This is evidence of an unresolved permission issue, not a determination of which
  regional terms or legal exceptions apply to this particular project.

Before a public image release, obtain a documented distribution basis for the
vendor components or replace them with appropriately licensed equivalents and
test the replacement. Moving the download to first startup is not, by itself,
proof that all permission issues are resolved. Do not publish private captures,
account files, images from cameras, or household inventories.

This notice changes neither the running household installation nor its recordings.

