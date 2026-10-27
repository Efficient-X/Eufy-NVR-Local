"""Actual registration lifecycle against stub HA boundaries; no dashboard writes."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("card_registration", ROOT / "custom_components/eufy_nvr/frontend.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.asyncio
async def test_two_entries_share_one_registration_and_last_unload_removes(monkeypatch):
    frontend = SimpleNamespace(add_extra_js_url=Mock(), remove_extra_js_url=Mock())
    components = ModuleType("homeassistant.components")
    components.frontend = frontend
    http = ModuleType("homeassistant.components.http")
    http.StaticPathConfig = lambda url, path, cache: (url, path, cache)
    monkeypatch.setitem(sys.modules, "homeassistant.components", components)
    monkeypatch.setitem(sys.modules, "homeassistant.components.http", http)
    async def executor(fn):
        return fn()
    hass = SimpleNamespace(data={}, http=SimpleNamespace(async_register_static_paths=AsyncMock()),
                           async_add_executor_job=executor)
    remove_a, remove_b = await asyncio.gather(
        module.async_register_card(hass, "a"), module.async_register_card(hass, "b"))
    hass.http.async_register_static_paths.assert_awaited_once()
    frontend.add_extra_js_url.assert_called_once()
    url = frontend.add_extra_js_url.call_args.args[1]
    assert url.startswith("/eufy_nvr_static/eufy-shared-tiles.js?v=")
    paths = hass.http.async_register_static_paths.call_args.args[0]
    assert paths == [(module.PATH, str(module.CARD), False)]
    assert module.CARD.read_bytes() == (ROOT / "frontend/eufy-shared-tiles.js").read_bytes()
    assert remove_a() is None
    frontend.remove_extra_js_url.assert_not_called()
    assert remove_b() is None
    assert remove_b() is None
    frontend.remove_extra_js_url.assert_called_once_with(hass, url)
    remove_c = await module.async_register_card(hass, "c")
    hass.http.async_register_static_paths.assert_awaited_once()
    assert frontend.add_extra_js_url.call_count == 2
    remove_c()
