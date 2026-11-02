"""Stage an explicit, source-only EFX repository preview; never upload it.

The output has fresh-tree source only, without Git history or account state.
Verify before initializing a Git checkout; .git would correctly be an extra file.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import yaml
import stage_guided_candidate as guided

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.8.0-alpha.6"
# Unpublished app/integration UX candidate; never retarget published tags/images.
APP_VERSION = "0.8.0-alpha.6"
PUBLIC_IMAGE = "ghcr.io/efficient-x/eufy-nvr-local"
MANIFEST = "PUBLIC_SOURCE_MANIFEST.json"
SOURCE_FILES = (
    '.gitattributes',
    '.github/FUNDING.yml',
    '.github/workflows/ci.yml',
    '.github/workflows/image-audit.yml',
    '.github/workflows/source-audit.yml',
    '.github/workflows/publish-prebuilt.yml',
    '.github/workflows/hardening-tests.yml',
    '.gitignore',
    'LICENSE',
    'README.md',
    'RELEASE_NOTES.md',
    'release-blurb.txt',
    'release-summary.txt',
    'artwork/efx-camera-recorder.svg',
    'artwork/LICENSE',
    'artwork/README.txt',
    'THIRD_PARTY.md',
    'bridge/account_setup.py',
    'bridge/auth_login.py',
    'bridge/browser_session_probe.py',
    'bridge/cloud_transport.py',
    'bridge/dashboard_config.py',
    'bridge/eufy_cloud.py',
    'bridge/eufy_crypto.py',
    'bridge/eufy_run.py',
    'bridge/eufy_stream.py',
    'bridge/eufy_warm.py',
    'bridge/fetch_deps.js',
    'bridge/gen_go2rtc.py',
    'bridge/get_auth.js',
    'bridge/go2rtc.example.yaml',
    'bridge/guided_launcher.py',
    'bridge/ha_setup_trust.py',
    'bridge/live_layout.py',
    'bridge/media_route.py',
    'bridge/multichannel_probe.py',
    'bridge/process_ownership.py',
    'bridge/ptcs_transport.js',
    'bridge/remembered_login.py',
    'bridge/requirements.txt',
    'bridge/sctp_oracle.js',
    'bridge/setup_budget.py',
    'bridge/setup_ui/index.html',
    'bridge/setup_ui/setup.css',
    'bridge/setup_ui/setup.js',
    'bridge/setup_web.py',
    'bridge/start_bridge.cmd',
    'bridge/start_bridge.sh',
    'bridge/startup_controller.py',
    'bridge/token_expiry.py',
    'bridge/video_diagnostics.py',
    'bridge/video_pipeline.py',
    'custom_components/eufy_nvr/__init__.py',
    'custom_components/eufy_nvr/app_connection.py',
    'custom_components/eufy_nvr/camera.py',
    'custom_components/eufy_nvr/config_flow.py',
    'custom_components/eufy_nvr/const.py',
    'custom_components/eufy_nvr/coordinator.py',
    'custom_components/eufy_nvr/diagnostics.py',
    'custom_components/eufy_nvr/frontend.py',
    'custom_components/eufy_nvr/go2rtc_api.py',
    'custom_components/eufy_nvr/manifest.json',
    'custom_components/eufy_nvr/snapshot.py',
    'custom_components/eufy_nvr/strings.json',
    'custom_components/eufy_nvr/translations/en.json',
    'custom_components/eufy_nvr/www/eufy-shared-tiles.js',
    'custom_components/eufy_nvr/brand/icon.png',
    'custom_components/eufy_nvr/brand/LICENSE',
    'custom_components/eufy_nvr/brand/icon@2x.png',
    'custom_components/eufy_nvr/brand/logo.png',
    'custom_components/eufy_nvr/brand/logo@2x.png',
    'custom_components/eufy_nvr/brand/dark_logo.png',
    'custom_components/eufy_nvr/brand/dark_logo@2x.png',
    'docs/ACCOUNT_SETUP.md',
    'docs/DISTRIBUTION_REVIEW.md',
    'docs/GUIDED_CANDIDATE.md',
    'docs/OFFLINE_ENGINEERING_20261102.md',
    'docs/PROTOCOL.md',
    'docs/PUBLISHING.md',
    'docs/PREBUILT_IMAGES.md',
    'docs/RELEASE_ACCEPTANCE.md',
    'docs/SETUP_GUIDE.md',
    'eufy_nvr/.dockerignore',
    'eufy_nvr/CHANGELOG.md',
    'eufy_nvr/DOCS.md',
    'eufy_nvr/README.md',
    'eufy_nvr/icon.png',
    'eufy_nvr/logo.png',
    'eufy_nvr/Dockerfile',
    'eufy_nvr/THIRD_PARTY.md',
    'eufy_nvr/bridge/account_setup.py',
    'eufy_nvr/bridge/auth_login.py',
    'eufy_nvr/bridge/browser_session_probe.py',
    'eufy_nvr/bridge/cloud_transport.py',
    'eufy_nvr/bridge/dashboard_config.py',
    'eufy_nvr/bridge/eufy_cloud.py',
    'eufy_nvr/bridge/eufy_crypto.py',
    'eufy_nvr/bridge/eufy_run.py',
    'eufy_nvr/bridge/eufy_stream.py',
    'eufy_nvr/bridge/eufy_warm.py',
    'eufy_nvr/bridge/fetch_deps.js',
    'eufy_nvr/bridge/gen_go2rtc.py',
    'eufy_nvr/bridge/get_auth.js',
    'eufy_nvr/bridge/go2rtc.example.yaml',
    'eufy_nvr/bridge/guided_launcher.py',
    'eufy_nvr/bridge/ha_setup_trust.py',
    'eufy_nvr/bridge/live_layout.py',
    'eufy_nvr/bridge/media_route.py',
    'eufy_nvr/bridge/multichannel_probe.py',
    'eufy_nvr/bridge/process_ownership.py',
    'eufy_nvr/bridge/ptcs_transport.js',
    'eufy_nvr/bridge/remembered_login.py',
    'eufy_nvr/bridge/requirements.txt',
    'eufy_nvr/bridge/sctp_oracle.js',
    'eufy_nvr/bridge/setup_budget.py',
    'eufy_nvr/bridge/setup_ui/index.html',
    'eufy_nvr/bridge/setup_ui/setup.css',
    'eufy_nvr/bridge/setup_ui/setup.js',
    'eufy_nvr/bridge/setup_web.py',
    'eufy_nvr/bridge/start_bridge.cmd',
    'eufy_nvr/bridge/start_bridge.sh',
    'eufy_nvr/bridge/startup_controller.py',
    'eufy_nvr/bridge/token_expiry.py',
    'eufy_nvr/bridge/video_diagnostics.py',
    'eufy_nvr/bridge/video_pipeline.py',
    'eufy_nvr/config.yaml',
    'eufy_nvr/guided-entry.sh',
    'eufy_nvr/licenses/eufy-sdk-APACHE-2.0.txt',
    'eufy_nvr/licenses/eufy-sdk-PROVENANCE.md',
    'eufy_nvr/licenses/go2rtc-LICENSE.txt',
    'eufy_nvr/licenses/EFX-MIT-LICENSE.txt',
    'eufy_nvr/run.sh',
    'frontend/README.md',
    'frontend/eufy-shared-tiles.js',
    'frontend/test-fixture.html',
    'hacs.json',
    'pytest.ini',
    'repository.yaml',
    'tests/conftest.py',
    'tests/test_account_setup.py',
    'tests/test_adaptive_warm.py',
    'tests/test_app_connection.py',
    'tests/test_auth_hardening.py',
    'tests/test_browser_session_probe.py',
    'tests/test_camera_stream_source.py',
    'tests/test_capacity.py',
    'tests/test_cloud_transport.py',
    'tests/test_dashboard_config.py',
    'tests/test_dashboard_discovery.py',
    'tests/test_discovery_manifest_path.py',
    'tests/test_dtls_identity.py',
    'tests/test_eufy_supervisor.py',
    'tests/test_frontend_registration.py',
    'tests/test_frontend_registry_lifecycle.mjs',
    'tests/test_go2rtc_api.py',
    'tests/test_go2rtc_metadata_runtime.py',
    'tests/test_guided_health.py',
    'tests/test_guided_packaging.py',
    'tests/test_guided_startup.py',
    'tests/test_hardening_api.py',
    'tests/test_hardening_generator.py',
    'tests/test_hardening_snapshot.py',
    'tests/test_integration_startup.py',
    'tests/test_linux_candidate_smoke.py',
    'tests/test_media_route.py',
    'tests/test_multichannel_probe.py',
    'tests/test_private_diagnostics.py',
    'tests/test_process_ownership.py',
    'tests/test_protocol_release_hardening.py',
    'tests/test_ptcs_transport.mjs',
    'tests/test_ptcs_discovery_pipeline.py',
    'tests/test_recovery_plan.py',
    'tests/test_public_package.py',
    'tests/test_presentation_metadata.py',
    'tests/test_prebuilt_release.py',
    'tests/test_image_inventory.py',
    'tests/test_image_sources.py',
    'tests/test_source_release_tools.py',
    'tests/test_regions.py',
    'tests/test_release_packaging.py',
    'tests/test_remembered_login.py',
    'tests/test_sdk_crypto.py',
    'tests/test_setup_ui.mjs',
    'tests/test_setup_web.py',
    'tests/test_shared_grid.py',
    'tests/test_shared_grid_priming.py',
    'tests/test_shared_tiles.mjs',
    'tests/test_signaling_identity.py',
    'tests/test_token_expiry.py',
    'tests/test_video_diagnostics.py',
    'tests/test_video_pipeline.py',
    'tests/test_video_startup.py',
    'tests/test_video_timing_integration.py',
    'tools/linux_candidate_smoke.py',
    'tools/frontend_registration_smoke.html',
    'tools/frontend_smoke_server.py',
    'tools/recovery_plan.py',
    'tools/process_http_recovery_smoke.py',
    'tools/render_brand_assets.ps1',
    'tools/s6_guided_entrypoint_smoke.py',
    'tools/stage_guided_candidate.py',
    'tools/stage_public_repository.py',
    'tools/verify_candidate.py',
    'tools/image_inventory.py',
    'tools/image_sources.py',
    'tools/extra_image_sources.py',
    'tools/overlay_source_inputs.json',
    'tools/prebuilt_release.py',
    'tools/source_bundle.py',
    'tools/image_video_smoke.py',
)


def _read(root, name):
    path = root / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Missing, linked or external source file")
    return path.read_bytes()


def stage(destination, *, root=ROOT):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    if destination.resolve().is_relative_to(root):
        raise ValueError("Keep the export outside its source checkout")
    files = {name: _read(root, name) for name in SOURCE_FILES}
    if files["THIRD_PARTY.md"] != files["eufy_nvr/THIRD_PARTY.md"]:
        raise ValueError("Third-party notices differ")
    for name in guided.BRIDGE_FILES:
        if files["bridge/" + name] != files["eufy_nvr/bridge/" + name]:
            raise ValueError("Packaged bridge differs from source")
    if files["frontend/eufy-shared-tiles.js"] != files["custom_components/eufy_nvr/www/eufy-shared-tiles.js"]:
        raise ValueError("Packaged frontend differs from source")
    source_config = yaml.safe_load(files["eufy_nvr/config.yaml"])
    image = source_config.get("image")
    if image not in (None, PUBLIC_IMAGE):
        raise ValueError("Public image must be the reviewed EFX registry")
    config = guided.candidate_config(source_config)
    if image is not None:
        config["image"] = image
    config.update(name="Eufy NVR Local (experimental)", version=APP_VERSION,
                  url="https://github.com/Efficient-X/Eufy-NVR-Local")
    files["eufy_nvr/config.yaml"] = yaml.safe_dump(config,sort_keys=False).encode()
    component = json.loads(files["custom_components/eufy_nvr/manifest.json"])
    component["version"] = VERSION
    component["name"] = "Eufy NVR Local"
    files["custom_components/eufy_nvr/manifest.json"] = (json.dumps(component,indent=2)+"\n").encode()
    hacs = json.loads(files["hacs.json"])
    hacs.update(name="Eufy NVR Local", homeassistant="2026.10.2")
    files["hacs.json"] = (json.dumps(hacs,indent=2)+"\n").encode()
    dockerfile = files["eufy_nvr/Dockerfile"].decode()
    count = dockerfile.count("FROM legacy AS release") + dockerfile.count("FROM guided AS release")
    if count != 1:
        raise ValueError("Ambiguous release Docker stage")
    files["eufy_nvr/Dockerfile"] = dockerfile.replace("FROM legacy AS release","FROM guided AS release").encode()
    files["eufy_nvr/DOCS.md"] = files["docs/SETUP_GUIDE.md"]
    # Build contexts and Linux entry points must survive Windows checkouts.
    for name, data in files.items():
        if name.endswith('.sh') and b'\r' in data:
            raise ValueError("Shell files must use LF line endings")
    entries = {name:{"size":len(data),"sha256":hashlib.sha256(data).hexdigest()}
               for name,data in sorted(files.items())}
    document = {"format":1,"version":VERSION,"files":entries}
    files[MANIFEST] = (json.dumps(document,sort_keys=True,indent=2)+"\n").encode()
    # Complete validation before making a new directory; never overwrite one.
    destination.mkdir(parents=False,exist_ok=False)
    for name,data in files.items():
        output = destination / name
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(data)
    return hashlib.sha256(files[MANIFEST]).hexdigest(), len(entries)


def verify(destination, expected):
    destination = Path(destination).absolute()
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("A regular export directory is required")
    raw = (destination / MANIFEST).read_bytes()
    if len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Public manifest digest differs")
    data = json.loads(raw)
    if data.get("format") != 1 or data.get("version") != VERSION or set(data.get("files",{})) != set(SOURCE_FILES):
        raise ValueError("Public manifest is not the reviewed exact source set")
    paths = list(destination.rglob('*'))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Export contains a link")
    actual = {path.relative_to(destination).as_posix() for path in paths if path.is_file()}
    if actual != set(SOURCE_FILES) | {MANIFEST}:
        raise ValueError("Public source files were added or removed")
    for name,entry in data["files"].items():
        content = _read(destination,name)
        if len(content) != entry["size"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("Public source bytes differ")
    return len(data["files"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--output',type=Path)
    group.add_argument('--verify',type=Path)
    parser.add_argument('--expected-sha256')
    args=parser.parse_args()
    if args.output:
        digest,count=stage(args.output)
        print(f"Prepared {count} source files plus public manifest. Nothing uploaded.")
        print(f"Public manifest SHA-256: {digest}")
    else:
        if not args.expected_sha256:
            parser.error('--verify requires --expected-sha256')
        count=verify(args.verify,args.expected_sha256)
        print(f"Verified {count} public source files; nothing changed.")

if __name__ == '__main__':
    main()
