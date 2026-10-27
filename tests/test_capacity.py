"""Capacity boundaries, mixed lenses and actual serialized 4x4 commands."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
import live_layout as layout
import gen_go2rtc as gen


def cameras(count, sensors=2):
    return [{"channel": ch, "name": f"Camera {ch}", "sensor_num": sensors,
             "status": 1} for ch in range(count)]


@pytest.mark.parametrize("count", range(1, 9))
def test_all_dual_camera_counts_have_unique_bounded_cells(count):
    result = layout.manifest_layout(cameras(count))
    assert result["physical_cameras"] == count
    assert result["lens_views"] == count * 2
    tiles = result["tiles"]
    assert len({t["index"] for t in tiles}) == count * 2
    assert max(t["index"] for t in tiles) < result["grid_columns"] ** 2
    assert {(t["channel"], t["sensor"]) for t in tiles} == {
        (ch, sensor) for ch in range(count) for sensor in (0, 1)}


def test_full_16_single_lens_grid_and_mixed_capacity():
    single = layout.manifest_layout(cameras(16, 1))
    assert single["lens_views"] == 16
    assert single["grid_columns"] == 4
    assert all(tile["sensor"] == 0 for tile in single["tiles"])
    mixed = cameras(4) + [{**cam, "channel": cam["channel"] + 4} for cam in cameras(8, 1)]
    result = layout.manifest_layout(mixed)
    assert result["physical_cameras"] == 12 and result["lens_views"] == 16
    assert [t["index"] for t in result["tiles"]] == list(range(16))
    with pytest.raises(ValueError):
        layout.manifest_layout(mixed + [{"channel": 20, "sensor_num": 1}])


def test_unknown_capabilities_fail_without_silent_dual_lens_guess():
    with pytest.raises(ValueError, match="unknown"):
        layout.manifest_layout([{"channel": 0, "sn": "FUTURE_MODEL"}])
    assert layout.sensor_count({"dev_type": 301}) == 2
    assert layout.sensor_count({"sn": "T8E00_TEST"}) == 2
    assert layout.sensor_count({"sn": "T8P10_TEST"}) == 1
    assert layout.sensor_count({"dev_type": 301, "sensor_num": 1}) == 1
    for value in (0, 3, True, "2"):
        with pytest.raises(ValueError):
            layout.sensor_count({"sensor_num": value})


def test_grid_override_cannot_crop_away_cameras():
    with pytest.raises(ValueError, match="too small"):
        layout.manifest_layout(cameras(8), columns=3)
    forced = layout.manifest_layout(cameras(4), columns=4)
    assert [t["index"] for t in forced["tiles"]] == list(range(8))
    assert forced["grid_columns"] == 4
    with pytest.raises(ValueError):
        layout.manifest_layout(cameras(9), lens="fixed")
    forced_three = layout.manifest_layout(cameras(2), columns=3)
    assert [t["index"] for t in forced_three["tiles"]] == [0, 1, 3, 4]


def test_runtime_ignores_unselected_unknown_devices_and_rejects_duplicates(tmp_path):
    path = tmp_path / "cameras.json"
    selected = cameras(1, 1)
    path.write_text(json.dumps({"cameras": selected + [{"channel": 7, "status": 1}]}))
    assert layout.runtime_layout_args({"EUFY_CAMERAS": str(path)}, [0])["counts"] == {0: 1}
    with pytest.raises(ValueError, match="unknown"):
        layout.runtime_layout_args({"EUFY_CAMERAS": str(path)}, [7])
    path.write_text(json.dumps({"cameras": selected * 2}))
    with pytest.raises(ValueError, match="Duplicate"):
        layout.runtime_layout_args({"EUFY_CAMERAS": str(path)}, [0])


def test_all_uses_discovery_and_honours_offline_and_disabled(tmp_path):
    path = tmp_path / "cameras.json"
    path.write_text(json.dumps({"cameras": cameras(8) + [{"channel": 10, "status": 0}]}))
    assert layout.discovered_channels(path) == list(range(8))
    assert layout.discovered_channels(path, "1,7") == [0, 2, 3, 4, 5, 6]


def test_eight_units_generate_only_one_shared_producer():
    named = [(f"eufy_camera_{c['channel']}", c) for c in cameras(8)]
    config = gen.render_config(named, "test", "not-a-real-password", shared_grid=True, grid_lens="both")
    streams = yaml.safe_load(config)["streams"]
    assert len(streams) == 9
    assert config.count("--grid-2x2") == 1
    assert "0,1,2,3,4,5,6,7 --grid-2x2" in streams["eufy_shared_grid"]


def test_actual_runtime_packet_is_16_unique_lenses_and_1080p(tmp_path, monkeypatch):
    path = tmp_path / "cameras.json"
    path.write_text(json.dumps({"cameras": cameras(8)}))
    monkeypatch.setenv("EUFY_CAMERAS", str(path))
    monkeypatch.setenv("EUFY_GRID_LENS", "both")
    monkeypatch.setitem(sys.modules, "live_layout", layout)
    tree = ast.parse((ROOT / "bridge/eufy_stream.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_startstream")
    scope = {"os": os, "json": json, "struct": struct}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "builder", "exec"), scope)
    packet = scope["build_startstream"]("test-user", list(range(8)), grid=True)
    payload = json.loads(packet[16:])["payload"]
    assert payload["stitch_mode"] == 4
    assert payload["stitch_resolution"] == {"width": 1920, "height": 1080}
    assert len(payload["chn_list"]) == 16
    assert [v["index"] for v in payload["chn_list"]] == list(range(16))
    assert all(v["was_split"] == 1 for v in payload["chn_list"])
    assert struct.unpack_from("<I", packet, 6)[0] == len(packet) - 16
    path.write_text(json.dumps({"cameras": cameras(1, 1)}))
    packet = scope["build_startstream"]("test-user", [0], grid=False)
    assert json.loads(packet[16:])["payload"]["chn_list"] == [{"index": 0, "chn": 0, "sensor": 0}]


def test_generation_preserves_existing_stream_ids_when_camera_added(tmp_path):
    source, output, registry = [tmp_path / name for name in ("cameras.json", "go2rtc.yaml", "names.json")]
    source.write_text(json.dumps({"nvr_sn": "TEST", "cameras": cameras(4)}))
    first = gen.generate(source, output, registry, username="test", password="not-a-real-password",
                         shared_grid=True, grid_lens="both")
    source.write_text(json.dumps({"nvr_sn": "TEST", "cameras": cameras(8)}))
    second = gen.generate(source, output, registry, username="test", password="not-a-real-password",
                          shared_grid=True, grid_lens="both")
    assert [name for name, _ in first] == [name for name, _ in second[:4]]
