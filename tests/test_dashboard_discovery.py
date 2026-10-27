"""Exercise generated metadata and the actual local HTTP reader, no NVR access."""
import importlib.util
import json
from pathlib import Path
import sys

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
import gen_go2rtc as gen

spec = importlib.util.spec_from_file_location("layout_api", ROOT / "custom_components/eufy_nvr/go2rtc_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
AUTH = {"username": "test", "password": "synthetic-password-only"}


def generate(tmp_path, count=4, shared=True):
    source, config, registry = [tmp_path / p for p in ("cameras.json", "go2rtc.yaml", "names.json")]
    source.write_text(json.dumps({"nvr_sn": "private-serial", "cameras": [
        {"channel": i, "name": f"Camera {i}", "sn": f"private-{i}",
         "sensor_num": 2, "status": 1} for i in range(count)]}))
    gen.generate(source, config, registry, **AUTH, shared_grid=shared, grid_lens="both")
    settings = yaml.safe_load(config.read_text())
    path = Path(settings["api"]["static_dir"]) / "eufy-grid-layout.json"
    return settings, path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("count", range(1, 9))
def test_complete_layout_has_only_presentation_fields(tmp_path, count):
    settings, path, layout = generate(tmp_path, count)
    clean = api.validate_grid_layout(layout)
    assert len(clean["tiles"]) == count * 2
    assert "/" in settings["api"]["allow_paths"]
    assert "/api/config" not in settings["api"]["allow_paths"]
    assert set(path.parent.iterdir()) == {path}
    assert path.parent != tmp_path
    assert "private-" not in path.read_text()
    assert AUTH["password"] not in path.read_text()
    for i in range(count):
        assert clean["tiles"][i * 2]["name"].endswith("Fixed")
        assert clean["tiles"][i * 2 + 1]["name"].endswith("Tracking")


def test_disabling_grid_keeps_running_configs_metadata_unchanged(tmp_path):
    _, old_path, old = generate(tmp_path)
    previous = old_path.read_bytes()
    _, new_path, new = generate(tmp_path, shared=False)
    assert new_path != old_path and old_path.read_bytes() == previous
    assert "tiles" in old and new == {"version": 1, "available": False}
    with pytest.raises(ValueError):
        api.validate_grid_layout(new)


def test_refuse_directory_containing_other_files(tmp_path):
    folder = tmp_path / "card-metadata"
    folder.mkdir()
    private = folder / "auth.json"
    private.write_text("leave untouched")
    with pytest.raises(ValueError):
        generate(tmp_path)
    assert private.read_text() == "leave untouched"
    assert not (tmp_path / "go2rtc.yaml").exists()


def test_reusing_same_layout_is_stable_and_tampering_fails_closed(tmp_path):
    settings, path, _ = generate(tmp_path)
    repeated, again, _ = generate(tmp_path)
    assert path == again and repeated["api"]["static_dir"] == settings["api"]["static_dir"]
    path.write_text("{}")
    with pytest.raises(ValueError):
        generate(tmp_path)


@pytest.mark.parametrize("change", [
    {"version": True}, {"grid_columns": 1}, {"grid_rows": 4},
    {"tiles": []}, {"tiles": [{"index": True, "name": "bad"}]},
    {"tiles": [{"index": 0, "name": "a"}, {"index": 0, "name": "b"}]},
    {"tiles": [{"index": 0, "name": "a\n"}]},
])
def test_invalid_metadata_is_rejected(change):
    value = {"version": 1, "grid_columns": 2, "grid_rows": 2,
             "tiles": [{"index": 0, "name": "Front"}], **change}
    with pytest.raises(ValueError):
        api.validate_grid_layout(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["valid", "old", "redirect", "oversized", "invalid"])
async def test_real_http_reader_auth_bounds_and_no_redirect(kind):
    calls = []
    async def handler(request):
        calls.append(request.path)
        assert request.headers["Authorization"] == aiohttp.encode_basic_auth(AUTH["username"], AUTH["password"])
        if kind == "old":
            return web.Response(status=404)
        if kind == "redirect":
            return web.Response(status=302, headers={"Location": "/must-not-follow"})
        if kind == "oversized":
            return web.Response(body=b"x" * 65537)
        if kind == "invalid":
            return web.json_response({"version": 1, "available": False})
        return web.json_response({"version": 1, "grid_columns": 2, "grid_rows": 2,
            "tiles": [{"index": 0, "name": "Front", "ignored": "private"}],
            "ignored": "private"})
    app = web.Application()
    app.router.add_get("/eufy-grid-layout.json", handler)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = api.Go2RtcClient(session, server.host, server.port, 10, **AUTH)
        layout = await client.async_get_grid_layout()
    assert calls == ["/eufy-grid-layout.json"]
    if kind == "valid":
        assert layout["tiles"] == [{"index": 0, "name": "Front"}]
        assert "private" not in json.dumps(layout)
    else:
        assert layout is None
