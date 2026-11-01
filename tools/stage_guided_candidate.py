"""Prepare a local, fresh-install guided test package; never deploy or publish it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.8.0-dev.11"
MANIFEST = "SOURCE_MANIFEST.json"
BRIDGE_FILES = (
    "account_setup.py", "auth_login.py", "browser_session_probe.py",
    "cloud_transport.py", "dashboard_config.py", "eufy_cloud.py", "eufy_crypto.py", "eufy_run.py", "eufy_stream.py",
    "eufy_warm.py", "fetch_deps.js", "gen_go2rtc.py", "get_auth.js",
    "go2rtc.example.yaml", "guided_launcher.py", "ha_setup_trust.py",
    "live_layout.py", "media_route.py", "multichannel_probe.py", "process_ownership.py", "requirements.txt", "remembered_login.py",
    "sctp_oracle.js", "ptcs_transport.js", "setup_budget.py", "setup_web.py", "startup_controller.py", "token_expiry.py",
    "start_bridge.cmd", "start_bridge.sh", "video_diagnostics.py", "video_pipeline.py",
    "setup_ui/index.html", "setup_ui/setup.css", "setup_ui/setup.js",
)
TOP_FILES = ("Dockerfile", "run.sh", "guided-entry.sh", ".dockerignore",
             "THIRD_PARTY.md", "licenses/go2rtc-LICENSE.txt",
             "licenses/eufy-sdk-APACHE-2.0.txt", "licenses/eufy-sdk-PROVENANCE.md",
             "licenses/EFX-MIT-LICENSE.txt")
FRONTEND_FILES = ("eufy-shared-tiles.js", "README.md")
TEST_TOOLS = ("linux_candidate_smoke.py", "verify_candidate.py", "image_video_smoke.py",
              "s6_guided_entrypoint_smoke.py")
COMPONENT_FILES = (
    "__init__.py", "app_connection.py", "camera.py", "config_flow.py", "const.py",
    "coordinator.py", "diagnostics.py", "go2rtc_api.py", "manifest.json",
    "snapshot.py", "strings.json", "translations/en.json", "frontend.py",
    "www/eufy-shared-tiles.js",
)
# Only video options are retained. Never copy account, challenge, import or refresh options.
VIDEO_OPTIONS = {
    "signaling_mode", "signaling_identity", "log_level", "keep_warm",
    "adaptive_warm_seconds", "video_copy", "shared_grid", "grid_lens",
    "grid_columns", "disabled_channels", "rtsp_port",
    "go2rtc_username", "go2rtc_password",
}


def candidate_config(source):
    config = dict(source)
    config.update(
        name="Eufy NVR Local (guided development test)",
        version=VERSION, boot="manual", ingress=True, ingress_port=0,
        panel_admin=True, homeassistant_api=True,
        timeout=60,
    )
    config.pop("webui", None)
    config.pop("image", None)  # Development candidates must build their reviewed source.
    config.pop("watchdog", None)  # Docker health, not the old go2rtc-only TCP check.
    config["options"] = {key: value for key, value in source["options"].items()
                         if key in VIDEO_OPTIONS}
    config["options"].update(go2rtc_username="eufy", go2rtc_password="", setup_mode="guided")
    config["schema"] = {key: value for key, value in source["schema"].items()
                        if key in VIDEO_OPTIONS}
    config["schema"]["setup_mode"] = "list(guided)"
    config["ports_description"] = dict(source["ports_description"])
    config["ports_description"]["1985/tcp"] = "Authenticated local video API"
    return config


def _read_source(root, relative):
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Source links outside the reviewed tree are not allowed")
    if not path.is_file():
        raise ValueError("A required source file is missing")
    return path.read_bytes()


def payload_names():
    """The manifest cannot grant itself permission to include extra files."""
    return (set(TOP_FILES) | {"config.yaml", "DOCS.md", "GUIDED_CANDIDATE.md",
        "SETUP_GUIDE.md", "PUBLISHING.md", "LICENSE"}
        | {"bridge/" + name for name in BRIDGE_FILES}
        | {"frontend/" + name for name in FRONTEND_FILES}
        | {"test_tools/" + name for name in TEST_TOOLS}
        | {"custom_components/eufy_nvr/" + name for name in COMPONENT_FILES})


def source_manifest(files):
    if set(files) != payload_names():
        raise ValueError("Candidate contents differ from the reviewed file list")
    entries = {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
               for name, data in sorted(files.items())}
    return (json.dumps({"format": 1, "version": VERSION, "files": entries},
                       sort_keys=True, indent=2) + "\n").encode("utf-8")


def verify(destination, *, expected_manifest_sha256=None):
    """Read-only exact-set/hash check. An external digest binds an earlier review."""
    destination = Path(destination).absolute()
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("A regular candidate directory is required")
    paths = list(destination.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Candidate links are not allowed")
    actual = {path.relative_to(destination).as_posix() for path in paths if path.is_file()}
    if actual != payload_names() | {MANIFEST}:
        raise ValueError("Candidate file list changed")
    manifest = _read_source(destination, MANIFEST)
    digest = hashlib.sha256(manifest).hexdigest()
    if expected_manifest_sha256 is not None and digest != expected_manifest_sha256:
        raise ValueError("The candidate differs from the approved manifest")
    files = {name: _read_source(destination, name) for name in payload_names()}
    if manifest != source_manifest(files):
        raise ValueError("Candidate content changed")
    config = yaml.safe_load(files["config.yaml"])
    component = json.loads(files["custom_components/eufy_nvr/manifest.json"])
    if config.get("version") != VERSION or component.get("version") != VERSION:
        raise ValueError("Candidate versions do not match")
    return digest


def stage(destination, *, root=ROOT):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    # Collect and validate all inputs before creating anything.
    files = {name: _read_source(root, Path("eufy_nvr") / name) for name in TOP_FILES}
    if files["THIRD_PARTY.md"] != _read_source(root, "THIRD_PARTY.md"):
        raise ValueError("The packaged third-party notice differs from source")
    for name in FRONTEND_FILES:
        files["frontend/" + name] = _read_source(root, Path("frontend") / name)
    for name in TEST_TOOLS:
        files["test_tools/" + name] = _read_source(root, Path("tools") / name)
    for name in BRIDGE_FILES:
        packaged = _read_source(root, Path("eufy_nvr/bridge") / name)
        if packaged != _read_source(root, Path("bridge") / name):
            raise ValueError("The packaged bridge does not match the reviewed source")
        files["bridge/" + name] = packaged
    config = yaml.safe_load(_read_source(root, "eufy_nvr/config.yaml"))
    files["config.yaml"] = yaml.safe_dump(candidate_config(config), sort_keys=False).encode()
    for name in COMPONENT_FILES:
        relative = "custom_components/eufy_nvr/" + name
        files[relative] = _read_source(root, relative)
    manifest_path = "custom_components/eufy_nvr/manifest.json"
    manifest = json.loads(files[manifest_path])
    manifest["version"] = candidate_config(config)["version"]
    files[manifest_path] = (json.dumps(manifest, indent=2) + "\n").encode()
    # The matching integration is installed in HA, not inside the app image.
    files[".dockerignore"] += b"\ncustom_components/\nfrontend/\ntest_tools/\n"
    dockerfile = files["Dockerfile"].decode("utf-8")
    endings = dockerfile.count("FROM legacy AS release") + dockerfile.count("FROM guided AS release")
    if endings != 1:
        raise ValueError("The candidate Docker target needs review")
    files["Dockerfile"] = dockerfile.replace("FROM legacy AS release", "FROM guided AS release").encode()
    files["DOCS.md"] = _read_source(root, "docs/GUIDED_CANDIDATE.md")
    files["GUIDED_CANDIDATE.md"] = files["DOCS.md"]
    files["SETUP_GUIDE.md"] = _read_source(root, "docs/SETUP_GUIDE.md")
    files["PUBLISHING.md"] = _read_source(root, "docs/PUBLISHING.md")
    files["LICENSE"] = _read_source(root, "LICENSE")
    # Linux shebangs fail with CRLF. Refuse a Windows-transformed source checkout
    # rather than silently shipping bytes different from the reviewed scripts.
    for name, contents in files.items():
        if name.endswith(".sh") and b"\r" in contents:
            raise ValueError("Linux shell source must use LF line endings")
    files[MANIFEST] = source_manifest(files)
    # Refuse even an empty existing directory; never overwrite an earlier package.
    destination.mkdir(parents=False, exist_ok=False)
    for relative, contents in files.items():
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(contents)
    return len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path,
                        help="A new folder under an existing local parent folder")
    mode.add_argument("--verify", type=Path, help="Read-only check of an existing candidate")
    parser.add_argument("--expected-sha256", help="Previously recorded manifest digest")
    args = parser.parse_args(argv)
    try:
        if args.verify is not None:
            digest = verify(args.verify, expected_manifest_sha256=args.expected_sha256)
            print(f"Candidate files verified. Manifest SHA-256: {digest}")
            return 0
        if args.expected_sha256 is not None:
            parser.error("--expected-sha256 requires --verify")
        count = stage(args.output)
        digest = verify(args.output)
    except (OSError, ValueError, KeyError, yaml.YAMLError):
        print("Package not prepared. Check the new output folder and source validation.", file=sys.stderr)
        return 1
    print(f"Prepared {count} source files locally. Nothing installed or published.")
    print(f"Manifest SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

