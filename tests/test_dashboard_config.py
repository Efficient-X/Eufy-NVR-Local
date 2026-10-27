"""Full-capacity card generation without HA or camera connections."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from dashboard_config import build_card, main
from live_layout import manifest_layout

ENTITY = "camera.example_shared_grid"


def layout(count=4):
    return manifest_layout([{"channel": ch, "sensor_num": 2, "name": f"Camera {ch}"}
                            for ch in range(count)])


@pytest.mark.parametrize("count", range(1, 9))
def test_every_physical_camera_count_produces_one_player_and_all_lenses(count):
    original = layout(count)
    card = build_card(original, ENTITY)
    assert card["type"] == "custom:eufy-shared-tiles"
    assert len(card["tiles"]) == count * 2
    assert card["grid_columns"] == original["grid_columns"]
    assert {tile["index"] for tile in card["tiles"]} == {tile["index"] for tile in original["tiles"]}
    for i in range(count):
        assert card["tiles"][i * 2]["name"] == f"Camera {i} · Fixed"
        assert card["tiles"][i * 2 + 1]["name"] == f"Camera {i} · Tracking"
    assert json.dumps(card).count('"entity"') == 1


def test_custom_household_order_and_labels_do_not_change_grid_indices():
    card = build_card(layout(), ENTITY, channel_order=[0, 2, 1, 3])
    assert [tile["index"] for tile in card["tiles"]] == [0, 3, 2, 5, 1, 4, 6, 7]


def test_single_and_mixed_lenses_do_not_create_phantom_tracking_views():
    source = manifest_layout([{"channel": 0, "sensor_num": 1, "name": "Single"},
                              {"channel": 1, "sensor_num": 2, "name": "Dual"}])
    assert len(build_card(source, ENTITY)["tiles"]) == 3


def test_projection_drops_any_unrelated_private_fields():
    source = layout()
    source["authToken"] = "private-token"
    source["tiles"][0]["password"] = "private-password"
    original = copy.deepcopy(source)
    assert "private-" not in json.dumps(build_card(source, ENTITY))
    assert source == original


@pytest.mark.parametrize("change", ["duplicate_index", "duplicate_lens", "overflow", "total", "boolean", "name" ])
def test_invalid_descriptor_is_rejected(change):
    source = layout()
    if change == "duplicate_index":
        source["tiles"][1]["index"] = source["tiles"][0]["index"]
    elif change == "duplicate_lens":
        source["tiles"][1].update(channel=0, sensor=0)
    elif change == "overflow":
        source["tiles"][0]["index"] = 16
    elif change == "total":
        source["lens_views"] = 16
    elif change == "boolean":
        source["tiles"][0]["sensor"] = False
    else:
        source["tiles"][0]["name"] = "bad\nname"
    with pytest.raises(ValueError):
        build_card(source, ENTITY)


@pytest.mark.parametrize("order", [[0, 1], [0, 1, 1, 3], [0, 1, 2, True]])
def test_order_may_not_hide_cameras(order):
    with pytest.raises(ValueError):
        build_card(layout(), ENTITY, channel_order=order)


def test_cli_exports_usable_card_only(tmp_path, capsys):
    source = tmp_path / "grid_layout.json"
    source.write_text(json.dumps(layout(8)))
    assert main([str(source), "--entity", ENTITY]) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output["tiles"]) == 16 and output["grid_rows"] == 4
    source.write_text("private-invalid-json")
    assert main([str(source), "--entity", ENTITY]) == 2
    assert "private-invalid" not in capsys.readouterr().err
