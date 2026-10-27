"""Serve the bundled card and register its module without editing dashboards."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

KEY = "eufy_nvr_frontend"
CARD = Path(__file__).parent / "www" / "eufy-shared-tiles.js"
PATH = "/eufy_nvr_static/eufy-shared-tiles.js"


async def async_register_card(hass, entry_id):
    from homeassistant.components import frontend
    from homeassistant.components.http import StaticPathConfig

    state = hass.data.setdefault(KEY, {"lock": asyncio.Lock(), "entries": set(),
                                      "registered": False, "url": None})
    async with state["lock"]:
        if not state["registered"]:
            digest = await hass.async_add_executor_job(
                lambda: hashlib.sha256(CARD.read_bytes()).hexdigest()[:16])
            await hass.http.async_register_static_paths([
                StaticPathConfig(PATH, str(CARD), False)])
            state["url"] = PATH + "?v=" + digest
            state["registered"] = True
        if not state["entries"]:
            frontend.add_extra_js_url(hass, state["url"])
        state["entries"].add(entry_id)

    def remove():
        if entry_id not in state["entries"]:
            return
        state["entries"].remove(entry_id)
        if not state["entries"]:
            frontend.remove_extra_js_url(hass, state["url"])
    return remove
