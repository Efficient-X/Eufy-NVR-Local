# Downloadable app images: build and acceptance plan

A downloaded image lets Home Assistant fetch the program from a registry instead
of building it on the household computer. Supervisor can then back up the app's
saved data without exporting its locally built program image into every backup.
It still stores the installed image on disk. This is a backup-size improvement,
not a promise to remove all the app's installed storage or improve video CPU use.

## Current status

The `Prebuilt image audit` workflow builds the exact existing app on native amd64
and aarch64 runners. It tests imports, transport self-tests and synthetic Linux
worker lifecycle without a network connection, and records software versions and
installed licence notices. It does not sign in to Eufy or use cameras.

This workflow does **not** publish an image, change app versions, add an `image`
setting, or alter an installation. Its read-only GitHub token cannot publish
packages. The resulting evidence explicitly says `distribution_review: pending`
and `hardware_acceptance: not_tested`; passing a build must not erase those gaps.

The runtime stage keeps the bridge, pinned Python packages, Node, FFmpeg and HA
startup tooling. Compilers and development headers stay in the build stage.
Synthetic H.264/H.265 video and Opus/AAC audio are encoded by the installed FFmpeg
CLI and decoded by the installed PyAV, on both architectures. These checks guard
the media libraries; they do not prove real account or live-camera behaviour.

## Before the first downloadable image

1. Review both architecture inventories. Preserve the actual Debian, Python,
   go2rtc and base-image notices and applicable corresponding source, including
   libraries bundled inside Python wheels. A list of package names is not source.
2. In particular, pinned PyAV 17.1.0 bundles FFmpeg 8.1.1, independently of the
   system FFmpeg CLI. Its pinned source recipe references pyav-ffmpeg 8.1.1-2.
   Check its actual wheel libraries, build recipe and source dependencies. Do not
   describe the combined image as covered only by the EFX MIT licence.
3. Publish versioned architecture images and matching source/notice material.
   Verify anonymous pulls by exact digest before pointing the HA app at them.
   A restored installation will need access to those retained registry versions.
4. Add `image: ghcr.io/efficient-x/eufy-nvr-local-{arch}` only after the matching
   version tags exist. Keep existing app slug, saved data and port assignments.
   Do not publish a `latest` tag that silently changes a versioned installation.
5. Test installation, stop/start, restore, account access and real live lenses.
   Reuse the same video player and keep resource acceptance separate from build
   success. An existing older engine must not be replaced as an incidental
   packaging change; prepare and verify its migration/rollback first.
6. Before the household change, verify a fresh full backup and the exact candidate
   being installed. Compare backup component sizes only after a successful change.
   Report measured savings, not the entire old app backup as a guaranteed saving.

The source-only installation remains available while these steps are completed.
No household credentials, backups or camera pictures belong in image evidence.

## Source collection audit

The separate `Image source collection audit` builds the candidate on each native
architecture. It checks the installed packages against that image's inventory
before collecting the exact Debian source versions through signed official APT
indexes, and Python source distributions through official PyPI SHA-256 metadata.
It also reads PyAV's pinned FFmpeg build recipe as data and downloads its source
inputs using the recipe's hashes. Downloaded source code is never executed.

This command belongs only in a disposable CI container. It rejects HA data/config
mounts, changes only that container's temporary APT indexes, and receives no Eufy
account or household configuration. The workflow token has read access only.

The report retains missing versions and explicitly lists the remaining review:
Go dependency notices, base-overlay components, and other libraries/Rust crates
inside Python wheels. Collecting Debian/Python archives alone does not satisfy
those additional requirements. The report remains `distribution_review: pending`.
Any missing or changed archive fails the audit. Three-day CI artifacts are review
evidence only; final source and notice assets must be retained with the release
before an image is advertised for installation.

Supplemental collection uses the wheels' own SBOM hashes for registry Rust crates
and OpenSSL source archives. Go programs are inspected without being executed:
their embedded clean Git revision must match the pinned upstream source. The
collector vendors their Go modules, verifies that go.mod/go.sum did not change,
and saves the matching Go toolchain source. The pinned HA base, Bashio, S6 overlay
and PyAV FFmpeg build recipes are also retained. Only disposable CI containers
and public upstream repositories are used; the NVR app is never started.

Source evidence is split into parts of at most 400 MiB with SHA-256 hashes for
each part and the joined tar archive. A separate small artifact keeps the reports,
notices and inventory. This avoids making reviewers download a single oversized
artifact. CI artifacts still expire: release publication must preserve the parts,
their manifest and matching notices as permanent release assets. Normal app users
will not need these developer/source archives to install the app.

The base image does not retain S6's installed licence files. The supplemental
collector therefore checks the pinned S6 recipe against reviewed source inputs,
retains those source archives and their original COPYING text, and collects
licences from the pinned repositories and vendored Go modules. The musl archive
is explicitly a licence reference, not evidence of the exact libc build version.
All original terms and copyright notices are preserved; the combined image is
not described as solely covered by the project's MIT licence.

References: [HA image configuration](https://contributors.home-assistant.io/apps/reference/configuration/#image),
[Supervisor image export](https://github.com/home-assistant/supervisor/blob/2026.10.4/supervisor/apps/app.py),
[PyAV 17.1.0 recipe](https://github.com/PyAV-Org/PyAV/blob/v17.1.0/scripts/ffmpeg-8.1.json).
