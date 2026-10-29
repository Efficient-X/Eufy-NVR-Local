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

References: [HA image configuration](https://contributors.home-assistant.io/apps/reference/configuration/#image),
[Supervisor image export](https://github.com/home-assistant/supervisor/blob/2026.10.4/supervisor/apps/app.py),
[PyAV 17.1.0 recipe](https://github.com/PyAV-Org/PyAV/blob/v17.1.0/scripts/ffmpeg-8.1.json).
