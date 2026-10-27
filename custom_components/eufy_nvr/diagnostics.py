"""Diagnostics support for Eufy NVR (local)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import EufyNvrConfigEntry
from .const import CONF_API_PORT, CONF_HOST, CONF_RTSP_PORT
from .go2rtc_api import summarize_streams


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EufyNvrConfigEntry
) -> dict[str, Any]:
    """Return non-sensitive config and stream health information."""
    coordinator = entry.runtime_data
    return {
        "endpoint": {
            CONF_HOST: "REDACTED",
            CONF_API_PORT: entry.data[CONF_API_PORT],
            CONF_RTSP_PORT: entry.data[CONF_RTSP_PORT],
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            # Exception text can contain URLs, credentials or server replies.
            # Report the condition without serialising that arbitrary text.
            "has_error": coordinator.last_exception is not None,
        },
        # Names can contain serials or room names. Keep counts, not identities.
        "streams": summarize_streams(coordinator.data or {}, anonymous=True),
    }
