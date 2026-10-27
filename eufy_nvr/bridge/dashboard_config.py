"""Generate one shared-player HA card from validated grid_layout.json.

Output is JSON (also valid YAML), never a dashboard write or a live-stream test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


def build_card(layout, entity, *, channel_order=None, title="NVR cameras — Fixed + Tracking"):
    """Project only allowed presentation fields; never include discovery secrets."""
    if not isinstance(entity, str) or not re.fullmatch(r"camera\.[a-z0-9_]+", entity):
        raise ValueError("Use the shared-grid camera entity ID")
    if not isinstance(layout, dict) or layout.get("version") != 1:
        raise ValueError("Unsupported layout descriptor")
    cols, rows = layout.get("grid_columns"), layout.get("grid_rows")
    if type(cols) is not int or cols not in (2, 3, 4) or type(rows) is not int or rows != cols:
        raise ValueError("Layout must be a 2x2, 3x3 or 4x4 grid")
    tiles = layout.get("tiles")
    if not isinstance(tiles, list) or not 1 <= len(tiles) <= 16:
        raise ValueError("Layout requires 1 to 16 lens tiles")
    indices, pairs, by_channel = set(), set(), {}
    for tile in tiles:
        if not isinstance(tile, dict):
            raise ValueError("Invalid lens tile")
        index, channel, sensor = (tile.get(key) for key in ("index", "channel", "sensor"))
        if (type(index) is not int or not 0 <= index < cols * rows
                or type(channel) is not int or not 0 <= channel <= 255
                or type(sensor) is not int or sensor not in (0, 1)):
            raise ValueError("Invalid lens coordinates")
        if index in indices or (channel, sensor) in pairs:
            raise ValueError("Duplicate lens or grid position")
        name = tile.get("name")
        if (not isinstance(name, str) or not name.strip() or len(name) > 160
                or any(ord(char) < 32 for char in name)):
            raise ValueError("Invalid lens name")
        indices.add(index)
        pairs.add((channel, sensor))
        by_channel.setdefault(channel, []).append((sensor, {"index": index, "name": name}))
    if (type(layout.get("lens_views")) is not int or layout["lens_views"] != len(tiles)
            or type(layout.get("physical_cameras")) is not int
            or layout["physical_cameras"] != len(by_channel)):
        raise ValueError("Layout totals do not match lens tiles")
    order = list(by_channel) if channel_order is None else channel_order
    if (not isinstance(order, (list, tuple))
            or any(type(ch) is not int for ch in order)
            or len(order) != len(by_channel) or set(order) != set(by_channel)):
        raise ValueError("Camera order must include every discovered channel exactly once")
    if not isinstance(title, str) or not 1 <= len(title) <= 160:
        raise ValueError("Invalid card title")
    return {
        "type": "custom:eufy-shared-tiles", "entity": entity, "title": title,
        "grid_options": {"columns": 12, "rows": "auto"},
        "grid_columns": cols, "grid_rows": rows, "draw_fps": 5, "tile_columns": 2,
        "tiles": [tile for channel in order
                  for _, tile in sorted(by_channel[channel], key=lambda item: item[0])],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path, help="Generated grid_layout.json (not auth.json)")
    parser.add_argument("--entity", required=True, help="Actual HA shared-grid camera entity")
    args = parser.parse_args(argv)
    try:
        if args.layout.stat().st_size > 128_000:
            raise ValueError("Layout descriptor is too large")
        layout = json.loads(args.layout.read_text(encoding="utf-8"))
        card = build_card(layout, args.entity)
    except (OSError, ValueError, TypeError):
        print("Cannot generate card: check the layout descriptor and shared-grid entity.", file=sys.stderr)
        return 2
    print(json.dumps(card, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
