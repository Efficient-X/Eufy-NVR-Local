"""Read-only HA metadata checks through Supervisor. Never use a browser token."""
import asyncio
import re
import time
from urllib.parse import urlsplit

import aiohttp


class TrustUnavailable(Exception):
    def __init__(self, code="home_assistant_check_unavailable"):
        self.code = code
        super().__init__("Home Assistant setup permissions could not be checked")


def trust_from_metadata(users, config):
    if not isinstance(users, list) or not isinstance(config, dict):
        raise TrustUnavailable()
    allowed = frozenset(
        user["id"] for user in users
        if isinstance(user, dict)
        and isinstance(user.get("id"), str)
        and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", user["id"])
        and user.get("is_active") is True
        and user.get("system_generated") is False
        and (user.get("is_owner") is True
             or (isinstance(user.get("group_ids"), list)
                 and "system-admin" in user["group_ids"]))
    )
    origins = set()
    for key in ("internal_url", "external_url"):
        value = config.get(key)
        if not isinstance(value, str):
            continue
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.path not in {"", "/"}
                    or parsed.query or parsed.fragment):
                continue
            port = parsed.port
            host = parsed.hostname.lower()
            if ":" in host:
                host = "[" + host + "]"
            suffix = "" if port is None or (parsed.scheme, port) in {("http", 80), ("https", 443)} else ":" + str(port)
            origins.add(parsed.scheme + "://" + host + suffix)
        except ValueError:
            continue
    if not allowed:
        raise TrustUnavailable()
    if not origins:
        raise TrustUnavailable("home_assistant_url_missing")
    return allowed, frozenset(origins)


async def fetch_trust(token):
    if not token:
        raise TrustUnavailable()
    try:
        async with asyncio.timeout(15):
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    "http://supervisor/core/websocket",
                    headers={"Authorization": "Bearer " + token},
                    max_msg_size=1024 * 1024,
                ) as ws:
                    if (await ws.receive_json()).get("type") != "auth_required":
                        raise TrustUnavailable()
                    await ws.send_json({"type": "auth", "access_token": token})
                    if (await ws.receive_json()).get("type") != "auth_ok":
                        raise TrustUnavailable()
                    results = []
                    for number, command in enumerate(("config/auth/list", "get_config"), 1):
                        await ws.send_json({"id": number, "type": command})
                        reply = await ws.receive_json()
                        if (reply.get("type") != "result" or reply.get("id") != number
                                or reply.get("success") is not True):
                            raise TrustUnavailable()
                        results.append(reply.get("result"))
                    return trust_from_metadata(*results)
    except TrustUnavailable:
        raise
    except Exception:
        raise TrustUnavailable() from None


class HomeAssistantTrust:
    """Refresh within 30 seconds; a failed refresh never keeps stale privileges."""
    def __init__(self, token, *, fetcher=fetch_trust, clock=time.monotonic):
        self._token, self._fetcher, self._clock = token, fetcher, clock
        self._expires = 0
        self._policy = None
        self._failure = None
        self._lock = asyncio.Lock()

    async def current(self):
        async with self._lock:
            if self._clock() >= self._expires:
                self._policy = None
                try:
                    self._policy = await self._fetcher(self._token)
                    self._failure = None
                    self._expires = self._clock() + 30
                except Exception as exc:
                    self._failure = (exc.code if isinstance(exc, TrustUnavailable)
                                     else "home_assistant_check_unavailable")
                    self._expires = self._clock() + 5
            if self._policy is None:
                raise TrustUnavailable(self._failure or "home_assistant_check_unavailable")
            return self._policy

