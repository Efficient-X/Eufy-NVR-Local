import importlib.util
import ast
import json
import struct
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bridge" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_single_layout_is_unchanged():
    assert load("live_layout").layout_options([0]) == {"stitch_mode": 1}


def test_lens_selector_matches_official_player_and_rejects_unknown():
    module = load('live_layout')
    assert module.grid_sensor('fixed') == 0
    assert module.grid_sensor('movable') == 1
    with pytest.raises(ValueError):
        module.grid_sensor('both')


def test_shared_layout_is_one_bounded_720p_grid():
    assert load("live_layout").layout_options([0, 1, 2], True) == {
        "stitch_mode": 2, "stitch_resolution": {"width": 1280, "height": 720}}


@pytest.mark.parametrize("channels", [[], [0, 0], list(range(17)), [-1], [256], [True]])
def test_grid_rejects_invalid_channel_sets(channels):
    with pytest.raises(ValueError):
        load("live_layout").layout_options(channels, True)


@pytest.mark.parametrize("count", [3, 4])
def test_generated_grid_adds_one_producer_and_preserves_old_streams(count):
    gen = load("gen_go2rtc")
    named = [(f"eufy_camera_{ch}", {"channel": ch, "status": 1, "sensor_num": 2}) for ch in range(count)]
    config = gen.render_config(named, "test", "not-a-real-password", shared_grid=True)
    for ch in range(count):
        assert f"eufy_camera_{ch}:" in config
    assert config.count("--grid-2x2") == 1
    channels = ",".join(str(ch) for ch in range(count))
    assert f"eufy_run.py {channels} --grid-2x2 --rtsp {{output}}" in config
    assert "eufy_shared_grid:" in config
    assert "--grid-2x2" not in gen.render_config(named, "test", "not-a-real-password")


def test_grid_does_not_override_existing_name():
    gen = load("gen_go2rtc")
    with pytest.raises(ValueError):
        gen.render_config([("eufy_shared_grid", {"channel": 0})],
                          "test", "not-a-real-password", shared_grid=True)


def test_both_lenses_use_one_bounded_nine_cell_layout():
    layout = load("live_layout")
    channels = [0, 1, 2, 3]
    assert layout.layout_options(channels, True, "both") == {
        "stitch_mode": 3, "stitch_resolution": {"width": 1920, "height": 1080}}
    entries = layout.shared_lens_entries(channels, "both")
    assert [entry["index"] for entry in entries] == list(range(8))
    assert [(entry["chn"], entry["sensor"]) for entry in entries] == [
        (0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (3, 0), (3, 1)]
    assert len({(entry["chn"], entry["sensor"]) for entry in entries}) == 8
    assert all("was_split" not in entry for entry in entries[:6])
    assert all(entry["was_split"] == 1 for entry in entries[6:])
    assert layout.layout_options([0, 1], True, "both")["stitch_mode"] == 2
    with pytest.raises(ValueError):
        layout.shared_lens_entries(channels, "unknown")


@pytest.mark.parametrize("lens,cells,sensor,mode", [
    ("fixed", 4, 0, 2), ("movable", 4, 1, 2), ("both", 8, 0, 3)])
def test_actual_startstream_packet_lens_selection(monkeypatch, lens, cells, sensor, mode):
    # Execute only the actual pure packet builder, never the runtime entrypoint.
    tree = ast.parse((ROOT / "bridge/eufy_stream.py").read_text())
    builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "build_startstream")
    import sys
    monkeypatch.setitem(sys.modules, "live_layout", load("live_layout"))
    scope = {"json": json, "struct": struct,
             "os": SimpleNamespace(environ={"EUFY_GRID_LENS": lens})}
    exec(compile(ast.Module(body=[builder], type_ignores=[]), "packet_builder", "exec"), scope)
    packet = scope["build_startstream"]("dummy-user", [0, 1, 2, 3], grid=True)
    payload = json.loads(packet[16:])["payload"]
    assert payload["stitch_mode"] == mode
    assert len(payload["chn_list"]) == cells
    assert payload["chn_list"][0]["sensor"] == sensor
    if lens == "both":
        assert [entry["index"] for entry in payload["chn_list"] if entry.get("was_split")] == [6, 7]
        assert payload["entrytype"] == 0
        assert payload["msg_id"] == 116
        assert payload["station_video_type"] == 6
        assert payload["play_id"] == 1
        assert payload["pip_cord"] == {"x1": 0, "y1": 0, "x2": 0, "y2": 0}
    else:
        assert payload["entrytype"] == 1
        assert "play_id" not in payload
        assert all("was_split" not in entry for entry in payload["chn_list"])
    assert struct.unpack_from("<I", packet, 6)[0] == len(packet) - 16
    assert packet[14] == 1
    # Environment must not alter original single-camera requests.
    single = scope["build_startstream"]("dummy-user", [2], grid=False)
    assert json.loads(single[16:])["payload"]["chn_list"] == [
        {"index": 0, "chn": 2, "sensor": 1}]


@pytest.mark.parametrize("channels,indices,mode", [
    ([7], [0, 2], 2), ([7, 2], [0, 1, 2, 3], 2),
    ([7, 2, 9], [0, 1, 2, 3, 4, 5], 3)])
def test_both_layout_preserves_arbitrary_channel_ids_and_valid_positions(channels, indices, mode):
    layout = load("live_layout")
    entries = layout.shared_lens_entries(channels, "both")
    assert [entry["index"] for entry in entries] == indices
    assert {(entry["chn"], entry["sensor"]) for entry in entries} == {
        (channel, sensor) for channel in channels for sensor in (0, 1)}
    assert layout.layout_options(channels, True, "both")["stitch_mode"] == mode
