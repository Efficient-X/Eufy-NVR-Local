"""Execute the actual diagnostics function without importing the HA runtime."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/eufy_nvr"
spec = importlib.util.spec_from_file_location("diagnostics_api", ROOT / "go2rtc_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.mark.asyncio
async def test_diagnostic_export_never_renders_private_values_or_exception_text():
    class PrivateError(Exception):
        def __str__(self):
            raise AssertionError("Diagnostics must not render arbitrary exceptions")

    tree = ast.parse((ROOT / "diagnostics.py").read_text())
    # Only HA import/type boundaries are omitted; execute the shipping function.
    tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 or (isinstance(node, ast.ImportFrom) and node.module == "__future__")]
    namespace = {"CONF_HOST": "host", "CONF_API_PORT": "api_port", "CONF_RTSP_PORT": "rtsp_port",
                 "summarize_streams": api.summarize_streams}
    exec(compile(tree, "diagnostics.py", "exec"), namespace)
    entry = SimpleNamespace(data={"host": "private-host-canary", "api_port": 1985,
        "rtsp_port": 8556, "username": "private-user-canary", "password": "private-password-canary"},
        runtime_data=SimpleNamespace(last_update_success=False, last_exception=PrivateError(),
            data={"eufy_private-camera-serial-canary": {"producers": [{"url": "private-url-canary"}],
                "consumers": [{"remote_addr": "private-client-canary"}]}}))
    result = await namespace["async_get_config_entry_diagnostics"](None, entry)
    assert "canary" not in json.dumps(result)
    assert result["endpoint"] == {"host": "REDACTED", "api_port": 1985, "rtsp_port": 8556}
    assert result["coordinator"] == {"last_update_success": False, "has_error": True}
    assert result["streams"] == {"stream_1": {"producers": 1, "consumers": 1, "streaming": True}}
    assert entry.data["host"] == "private-host-canary"


def test_anonymous_stream_counts_do_not_change_normal_entity_names():
    streams = {"eufy_room_b": {}, "eufy_room_a": {"consumers": [{}]}}
    normal = api.summarize_streams(streams)
    anonymous = api.summarize_streams(streams, anonymous=True)
    assert list(normal) == ["eufy_room_a", "eufy_room_b"]
    assert list(anonymous) == ["stream_1", "stream_2"]
    assert list(normal.values()) == list(anonymous.values())
