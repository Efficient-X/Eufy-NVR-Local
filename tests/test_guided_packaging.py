"""Candidate staging copies reviewed source only; it never deploys."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stage_guided", ROOT / "tools/stage_guided_candidate.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
verify_spec = importlib.util.spec_from_file_location("verify_candidate", ROOT / "tools/verify_candidate.py")
transfer_verifier = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(transfer_verifier)


def test_staged_candidate_is_explicit_and_does_not_change_release(tmp_path):
    manifest = ROOT / "eufy_nvr/config.yaml"
    dockerfile = ROOT / "eufy_nvr/Dockerfile"
    before = manifest.read_bytes(), dockerfile.read_bytes()
    destination = tmp_path / "candidate"
    count = stage.stage(destination)
    config = yaml.safe_load((destination / "config.yaml").read_text())
    assert config["boot"] == "manual"
    assert config["ingress"] is True and config["ingress_port"] == 0
    assert config["panel_admin"] is True and config["homeassistant_api"] is True
    assert "watchdog" not in config and "webui" not in config
    assert "image" not in config
    assert not config.get("hassio_api", False)
    assert not config.get("full_access", False)
    assert config["slug"] == "eufy_nvr"
    assert config["version"] == stage.VERSION
    assert config["timeout"] == 60
    staged_manifest = json.loads((destination / "custom_components/eufy_nvr/manifest.json").read_text())
    assert staged_manifest["version"] == config["version"]
    for name in stage.COMPONENT_FILES:
        if name != "manifest.json":
            relative = "custom_components/eufy_nvr/" + name
            assert (destination / relative).read_bytes() == (ROOT / relative).read_bytes()
    assert config["options"]["go2rtc_password"] == ""
    assert config["options"]["go2rtc_username"] == "eufy"
    assert set(config["options"]) <= stage.VIDEO_OPTIONS | {"setup_mode"}
    assert set(config["schema"]) <= stage.VIDEO_OPTIONS | {"setup_mode"}
    assert config["options"]["setup_mode"] == "guided"
    assert config["schema"]["setup_mode"] == "list(guided)"
    assert "FROM guided AS release" in (destination / "Dockerfile").read_text()
    assert (destination / "GUIDED_CANDIDATE.md").read_bytes() == (destination / "DOCS.md").read_bytes()
    assert any(target in dockerfile.read_text() for target in ("FROM legacy AS release", "FROM guided AS release"))
    assert count == len(list(path for path in destination.rglob("*") if path.is_file()))
    for name in stage.FRONTEND_FILES:
        assert (destination / "frontend" / name).read_bytes() == (ROOT / "frontend" / name).read_bytes()
    for name in stage.TEST_TOOLS:
        assert (destination / "test_tools" / name).read_bytes() == (ROOT / "tools" / name).read_bytes()
    assert "test_tools/" in (destination / ".dockerignore").read_text()
    assert (destination / "frontend/eufy-shared-tiles.js").read_bytes() == (destination / "custom_components/eufy_nvr/www/eufy-shared-tiles.js").read_bytes()
    assert (destination / "THIRD_PARTY.md").read_bytes() == (ROOT / "THIRD_PARTY.md").read_bytes()
    assert (destination / "licenses/go2rtc-LICENSE.txt").read_bytes() == (ROOT / "eufy_nvr/licenses/go2rtc-LICENSE.txt").read_bytes()
    assert "COPY licenses /usr/share/doc/eufy-nvr/licenses" in (destination / "Dockerfile").read_text()
    assert before == (manifest.read_bytes(), dockerfile.read_bytes())
    assert len(stage.verify(destination)) == 64
    version, file_count = transfer_verifier.verify(destination, stage.verify(destination))
    assert version == stage.VERSION and file_count == count - 1
    for path in (destination / "bridge").rglob("*"):
        if path.is_file():
            relative = path.relative_to(destination / "bridge")
            assert path.read_bytes() == (ROOT / "bridge" / relative).read_bytes()
    forbidden = {"auth.json", "options.json", "cameras.json", "go2rtc.yaml",
                 "stream_names.json", "grid_layout.json", "secrets.yaml"}
    assert not forbidden.intersection(path.name for path in destination.rglob("*"))


def test_candidate_file_allowlist_covers_reviewed_bridge_source():
    actual = {p.relative_to(ROOT / "bridge").as_posix()
              for p in (ROOT / "bridge").rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    assert actual == set(stage.BRIDGE_FILES)
    component = ROOT / "custom_components/eufy_nvr"
    assert {p.relative_to(component).as_posix() for p in component.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts} == set(stage.COMPONENT_FILES)


def test_no_account_configuration_can_leak_into_candidate():
    original = yaml.safe_load((ROOT / "eufy_nvr/config.yaml").read_text())
    original["options"].update(email="private", password="private",
                               go2rtc_password="private", browser_session_url="private")
    candidate = stage.candidate_config(original)
    assert "private" not in yaml.safe_dump(candidate)
    assert original["options"]["go2rtc_password"] == "private"


def test_existing_output_is_not_overwritten(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        stage.stage(target)
    assert list(target.iterdir()) == [marker]
    assert marker.read_text() == "keep"


def test_missing_input_fails_before_creating_output(tmp_path):
    with pytest.raises(ValueError):
        stage.stage(tmp_path / "candidate", root=tmp_path)
    assert not (tmp_path / "candidate").exists()


def test_source_outside_reviewed_root_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        stage._read_source(tmp_path, ROOT / "eufy_nvr/config.yaml")


def test_candidate_has_no_go2rtc_health_dependency():
    dockerfile = (ROOT / "eufy_nvr/Dockerfile").read_text()
    guided = dockerfile.split("FROM legacy AS guided", 1)[1].split("FROM legacy AS release", 1)[0]
    assert "guided_launcher.py --health" in guided
    assert 'CMD [ "/guided-entry.sh" ]' in guided
    assert "/dev/tcp/" not in guided
    script = (ROOT / "eufy_nvr/guided-entry.sh").read_text()
    assert "bashio::app.ingress_port" in script
    assert 'exec python3 /opt/eufy/bridge/guided_launcher.py --port' in script
    assert "62000" in script and "65500" in script
    assert "password" not in script.lower().replace("# no passwords or token values", "")


@pytest.mark.parametrize("change", ["edit", "extra", "missing", "manifest", "version"])
def test_integrity_rejects_changed_candidate(tmp_path, change):
    target = tmp_path / "candidate"
    stage.stage(target)
    if change == "edit":
        with (target / "bridge/startup_controller.py").open("ab") as handle:
            handle.write(b"\n# unexpected edit\n")
    elif change == "extra":
        (target / "private-options.json").write_text('{"synthetic": true}')
    elif change == "missing":
        (target / "guided-entry.sh").unlink()
    elif change == "manifest":
        (target / stage.MANIFEST).write_text("{}\n")
    else:
        config = yaml.safe_load((target / "config.yaml").read_text())
        config["version"] = "unexpected"
        (target / "config.yaml").write_text(yaml.safe_dump(config))
        files = {name: (target / name).read_bytes() for name in stage.payload_names()}
        (target / stage.MANIFEST).write_bytes(stage.source_manifest(files))
    with pytest.raises(ValueError):
        stage.verify(target)


def test_external_manifest_digest_detects_resealed_change(tmp_path):
    target = tmp_path / "candidate"
    stage.stage(target)
    approved = stage.verify(target)
    path = target / "bridge/startup_controller.py"
    path.write_bytes(path.read_bytes() + b"\n# later unreviewed edit\n")
    files = {name: (target / name).read_bytes() for name in stage.payload_names()}
    (target / stage.MANIFEST).write_bytes(stage.source_manifest(files))
    assert stage.verify(target) != approved
    with pytest.raises(ValueError):
        stage.verify(target, expected_manifest_sha256=approved)


def test_staging_is_reproducible_and_verification_is_read_only(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    stage.stage(left)
    stage.stage(right)
    before = {p.relative_to(left).as_posix(): p.read_bytes()
              for p in left.rglob("*") if p.is_file()}
    assert stage.verify(left) == stage.verify(right)
    assert before == {p.relative_to(left).as_posix(): p.read_bytes()
                      for p in left.rglob("*") if p.is_file()}


def test_manifest_cannot_allow_unreviewed_extra_file():
    with pytest.raises(ValueError):
        stage.source_manifest({"private-options.json": b"{}"})


@pytest.mark.parametrize("change", ["edit", "extra", "missing", "manifest"])
def test_standalone_transfer_verifier_rejects_mutation(tmp_path, change):
    target = tmp_path / "candidate"
    stage.stage(target)
    fingerprint = stage.verify(target)
    if change == "edit":
        with (target / "bridge/startup_controller.py").open("ab") as handle:
            handle.write(b"\n# unapproved edit\n")
    elif change == "extra":
        (target / "private-options.json").write_text('{}')
    elif change == "missing":
        (target / "guided-entry.sh").unlink()
    else:
        (target / stage.MANIFEST).write_text("{}\n")
    with pytest.raises(ValueError):
        transfer_verifier.verify(target, fingerprint)


def test_standalone_verifier_requires_an_external_fingerprint(tmp_path):
    target = tmp_path / "candidate"
    stage.stage(target)
    with pytest.raises(ValueError):
        transfer_verifier.verify(target, "0" * 64)

