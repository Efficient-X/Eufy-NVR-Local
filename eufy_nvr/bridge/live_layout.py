"""Bounded NVR layouts: physical devices and lens channels are distinct.

Vendor stitch modes 2/3/4 mean 2x2/3x3/4x4. Retain the verified legacy
four-dual-camera packing, and use explicit split cells for larger/mixed grids.
"""

MAX_LENS_CHANNELS = 16


def discovered_channels(path, disabled=""):
    """Resolve CLI 'all' from discovery instead of assuming four ports."""
    import json
    from pathlib import Path
    omitted = {int(value.strip()) for value in disabled.split(",") if value.strip()}
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    channels = [camera["channel"] for camera in manifest["cameras"]
                if camera.get("status") not in (0, "0") and camera["channel"] not in omitted]
    validate_channels(channels)
    return channels


def runtime_layout_args(environ, channels=None):
    """Read only discovery metadata, never auth, when building a live request."""
    import json
    from pathlib import Path
    path = environ.get("EUFY_CAMERAS")
    counts = None
    if path:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
        selected = [camera for camera in manifest["cameras"]
                    if camera.get("status") not in (0, "0")
                    and (channels is None or camera["channel"] in channels)]
        validate_channels([camera["channel"] for camera in selected])
        counts = {camera["channel"]: sensor_count(camera) for camera in selected}
    return {"counts": counts, "columns": int(environ.get("EUFY_GRID_COLUMNS", "0"))}


def sensor_count(camera):
    """Prefer reported capabilities; use only a known model fallback.

    Old persisted S4 manifests omitted sensor_num. T8E00/dev_type 301 is the
    observed dual-lens S4. Unknown devices are never guessed to have two lenses.
    """
    value = camera.get("sensor_num")
    if value is not None:
        if type(value) is not int or value not in (1, 2):
            raise ValueError("Unsupported camera sensor_num; expected 1 or 2")
        return value
    if str(camera.get("sn") or "").startswith("T8E00") or camera.get("dev_type") == 301:
        return 2
    if str(camera.get("sn") or "").startswith("T8P10"):
        return 1
    raise ValueError("Camera lens count is unknown; refresh discovery before using shared grid")


def validate_channels(channels):
    if not 1 <= len(channels) <= MAX_LENS_CHANNELS:
        raise ValueError("Shared grid requires 1 to 16 distinct camera channels")
    if any(type(ch) is not int or not 0 <= ch <= 255 for ch in channels):
        raise ValueError("Grid channels must be integers from 0 to 255")
    if len(set(channels)) != len(channels):
        raise ValueError("Duplicate camera channel")


def selected_sensors(channels, lens, counts=None):
    validate_channels(channels)
    if lens not in ("fixed", "movable", "both"):
        raise ValueError("grid_lens must be fixed, movable or both")
    # counts=None preserves the original explicit dual-lens API for callers.
    counts = {ch: 2 for ch in channels} if counts is None else counts
    result = {}
    for channel in channels:
        count = counts.get(channel)
        if type(count) is not int or count not in (1, 2):
            raise ValueError("Every selected camera needs a validated lens count")
        result[channel] = [0] if count == 1 else ([0, 1] if lens == "both" else [grid_sensor(lens)])
    if sum(counts[ch] for ch in channels) > MAX_LENS_CHANNELS:
        raise ValueError("Physical cameras exceed the NVR's 16 lens-channel capacity")
    if sum(map(len, result.values())) > MAX_LENS_CHANNELS:
        raise ValueError("NVR supports at most 16 selected lens channels")
    return result


def grid_columns(channels, lens, counts=None, columns=0):
    sensors = selected_sensors(channels, lens, counts)
    cells = sum(map(len, sensors.values()))
    if type(columns) is not int or columns not in (0, 2, 3, 4):
        raise ValueError("grid_columns must be 0 (automatic), 2, 3 or 4")
    automatic = 2 if cells <= 4 else 3 if cells <= 9 else 4
    if columns and columns < automatic:
        raise ValueError("Selected grid is too small for the discovered lenses")
    return columns or automatic


def grid_sensor(lens):
    """Official NVR player mapping; reject unknown values rather than guess."""
    if lens not in ('fixed', 'movable'):
        raise ValueError('grid_lens must be fixed or movable')
    return 0 if lens == 'fixed' else 1


def layout_options(channels: list[int], grid: bool = False, lens: str = "movable",
                   counts=None, columns=0) -> dict:
    if not grid:
        return {"stitch_mode": 1}
    # Keep the verified four-view source unchanged. The live vendor 3x3
    # dual-lens reference uses 1080p; 720p both-lens requests returned no video.
    size = grid_columns(channels, lens, counts, columns)
    return {"stitch_mode": size,
            "stitch_resolution": {"width": 1920, "height": 1080} if lens == "both" or size > 2
            else {"width": 1280, "height": 720}}


def shared_lens_entries(channels: list[int], lens: str, counts=None, columns=0) -> list[dict]:
    """Use one physical channel list, with explicit unique lens/grid positions.

    Match the verified vendor arrangement: paired fixed/moved rows first,
    then split any remaining pair into the final row of a 3x3 layout.
    This is still one NVR session, not eight independent producers.
    """
    size = grid_columns(channels, lens, counts, columns)
    sensors = selected_sensors(channels, lens, counts)
    all_dual = all(len(values) == 2 for values in sensors.values())
    if lens != "both" or not all_dual or size == 4:
        # Explicit split lenses avoid automatic pair expansion or duplicated
        # single-lens cameras. Pair adjacent rows for a uniform dual-lens grid.
        ordered = []
        if all_dual and size == 4:
            for start in range(0, len(channels), size):
                group = channels[start:start + size]
                for sensor in (0, 1):
                    for column, channel in enumerate(group):
                        ordered.append({"index": (start // size * 2 + sensor) * size + column,
                                        "chn": channel, "sensor": sensor, "was_split": 1})
        else:
            for channel in channels:
                for sensor in sensors[channel]:
                    entry = {"index": len(ordered), "chn": channel, "sensor": sensor}
                    if lens == "both":
                        entry["was_split"] = 1
                    ordered.append(entry)
        return ordered
    columns = size
    paired = channels[:columns]
    entries = [{"index": column + sensor * columns, "chn": channel, "sensor": sensor}
               for sensor in (0, 1) for column, channel in enumerate(paired)]
    for channel in channels[columns:]:
        for sensor in (0, 1):
            entries.append({"index": 2 * columns + sensor, "chn": channel,
                            "sensor": sensor, "was_split": 1})
    return entries


def manifest_layout(cameras, lens="both", columns=0):
    """Build a credential-free descriptor for diagnostics/card generation."""
    channels = [camera["channel"] for camera in cameras]
    counts = {camera["channel"]: sensor_count(camera) for camera in cameras}
    size = grid_columns(channels, lens, counts, columns)
    entries = shared_lens_entries(channels, lens, counts, columns)
    by_channel = {camera["channel"]: camera for camera in cameras}
    tiles = []
    for entry in entries:
        camera = by_channel[entry["chn"]]
        suffix = "Fixed" if entry["sensor"] == 0 else "Tracking"
        tiles.append({"index": entry["index"], "channel": entry["chn"],
                      "sensor": entry["sensor"],
                      "name": f"{camera.get('name') or 'Camera ' + str(entry['chn'])} · {suffix}"})
    return {"version": 1, "physical_cameras": len(cameras), "lens_views": len(entries),
            "grid_columns": size, "grid_rows": size, "tiles": tiles,
            "counts": counts}
