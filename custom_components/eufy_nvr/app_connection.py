"""Read the selected installed app's local endpoint via Supervisor.

Reads are the default. First-time guided setup can explicitly create local
credentials and restart only the selected new app. No token exports, LAN scans
or cloud requests. Credentials never become form defaults or diagnostics.
"""
from __future__ import annotations

import re
import asyncio
import socket
import secrets

APP_SLUG = re.compile(r"(?:[a-z0-9]+_)?eufy_nvr\Z")


class LocalVideoSetupRequired(ValueError):
    """A new guided app needs its first local-video password."""


class LocalVideoSetupError(Exception):
    """Fixed, non-sensitive failure; never expose Supervisor response text."""


class LocalVideoAlreadyConfigured(LocalVideoSetupError):
    """The selected app already belongs to an integration; nothing was written."""


def needs_local_video_setup(info):
    return (bool(APP_SLUG.fullmatch(info.slug)) and str(info.state) == "started"
            and getattr(info, "ingress", False) is True
            and info.options.get("setup_mode") == "guided"
            and info.options.get("go2rtc_password") == ""
            and not any(info.options.get(key) for key in ("email", "password", "browser_session_url")))


async def provision_local_video(client, slug, options_type):
    """Explicit first-time action; caller serializes and checks existing entries.

    No rotation, no install, no stop/start of other apps. Read back after writing
    before restarting. On an uncertain reply, do not repeat the write blindly.
    """
    from .go2rtc_api import validate_credentials
    if not isinstance(slug, str) or not APP_SLUG.fullmatch(slug):
        raise LocalVideoSetupError("Local video setup could not be completed")
    try:
        async with asyncio.timeout(120):
            first = await client.addon_info(slug)
            if first.slug != slug:
                raise LocalVideoSetupError("App selection could not be confirmed")
            if not needs_local_video_setup(first):
                # Another completed attempt may already have saved the password.
                # Never generate a replacement or restart an established app.
                connection_from_info(first)
                return False
            previous = dict(first.options)
            username = previous.get("go2rtc_username") or "eufy"
            password = secrets.token_urlsafe(32)
            validate_credentials(username, password)
            proposed = {**previous, "go2rtc_username": username, "go2rtc_password": password}
            current = await client.addon_info(slug)
            if current.slug != slug or not needs_local_video_setup(current) or current.options != previous:
                raise LocalVideoSetupError("App settings changed during setup")
            # The API replaces the options object; preserve all unrelated fields.
            await client.set_addon_options(slug, options_type(config=proposed))
            saved = await client.addon_info(slug)
            if (saved.slug != slug or str(saved.state) != "started"
                    or saved.options != proposed):
                raise LocalVideoSetupError("Local video settings could not be confirmed")
            await client.restart_addon(slug)
            return True
    except Exception:
        raise LocalVideoSetupError("Local video setup could not be completed") from None


async def create_installed_local_video(hass, slug):
    """Only called by the explicit confirmation form, never by discovery."""
    from homeassistant.components.hassio import get_supervisor_client
    from aiohasupervisor.models.addons import AddonsOptions
    from .const import DOMAIN
    if not isinstance(slug, str) or not APP_SLUG.fullmatch(slug):
        raise LocalVideoSetupError("Invalid app selection")
    locks = hass.data.setdefault("eufy_nvr_local_video_setup_locks", {})
    async with locks.setdefault(slug, asyncio.Lock()):
        # App-private credentials are separate even when two host-network apps
        # use the same future video endpoint. Preparing a fresh guided app does
        # not change the existing entry or start its media worker. The worker
        # refuses occupied ports; the connection flow still rejects duplicates.
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.data.get("app_slug") == slug:
                raise LocalVideoAlreadyConfigured("This app is already configured")
        return await provision_local_video(get_supervisor_client(hass).addons, slug, AddonsOptions)


async def same_endpoint(host, other_host, port):
    """Recognise a configured IP and its hostname without starting video."""
    if host == other_host:
        return True
    loop = asyncio.get_running_loop()
    try:
        first, second = await asyncio.wait_for(asyncio.gather(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
            loop.getaddrinfo(other_host, port, type=socket.SOCK_STREAM)), timeout=5)
    except (OSError, TimeoutError):
        return False
    return bool({record[4][0] for record in first} & {record[4][0] for record in second})


def candidate_apps(apps):
    return {app.slug: f"{app.name} ({app.version})"
            for app in apps
            if APP_SLUG.fullmatch(app.slug) and str(app.state) == "started"}


def connection_from_info(info):
    from .const import DEFAULT_API_PORT, DEFAULT_HOST, DEFAULT_RTSP_PORT
    from .go2rtc_api import normalize_host, validate_credentials, validate_port
    if not APP_SLUG.fullmatch(info.slug) or str(info.state) != "started":
        raise ValueError("Select a running Eufy NVR app")
    options = info.options
    if needs_local_video_setup(info):
        raise LocalVideoSetupRequired("Create the first local video password")
    username, password = validate_credentials(options.get("go2rtc_username", ""),
                                               options.get("go2rtc_password", ""))
    host = DEFAULT_HOST if info.host_network else info.hostname
    return {"host": normalize_host(host), "api_port": DEFAULT_API_PORT,
            "rtsp_port": validate_port(options.get("rtsp_port", DEFAULT_RTSP_PORT)),
            "username": username, "password": password}


async def installed_apps(hass):
    if "hassio" not in hass.config.components:
        return {}
    from homeassistant.components.hassio import get_supervisor_client
    return candidate_apps(await get_supervisor_client(hass).addons.list())


async def installed_connection(hass, slug):
    if not APP_SLUG.fullmatch(slug):
        raise ValueError("Invalid Eufy NVR app")
    from homeassistant.components.hassio import get_supervisor_client
    info = await get_supervisor_client(hass).addons.addon_info(slug)
    return connection_from_info(info)
