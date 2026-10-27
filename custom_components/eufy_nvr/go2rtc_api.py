"""Small client and pure helpers for the local go2rtc API."""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

API_STREAMS_PATH = "/api/streams"
API_FRAME_PATH = "/api/frame.jpeg"
STREAM_PREFIX = "eufy_"
MAX_FRAME_BYTES = 20 * 1024 * 1024


def validate_grid_layout(value):
    """Project only safe presentation fields; no addresses, commands or tokens."""
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Invalid grid metadata")
    cols, rows, tiles = (value.get(k) for k in ("grid_columns", "grid_rows", "tiles"))
    if type(cols) is not int or cols not in (2, 3, 4) or type(rows) is not int or rows != cols:
        raise ValueError("Invalid grid size")
    if not isinstance(tiles, list) or not 1 <= len(tiles) <= 16:
        raise ValueError("Invalid grid tiles")
    clean, seen = [], set()
    for tile in tiles:
        if not isinstance(tile, dict):
            raise ValueError("Invalid grid tile")
        index, name = tile.get("index"), tile.get("name")
        if (type(index) is not int or not 0 <= index < cols * rows or index in seen
                or not isinstance(name, str) or not name.strip() or len(name) > 160
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ValueError("Invalid grid tile")
        seen.add(index)
        clean.append({"index": index, "name": name})
    return {"version": 1, "grid_columns": cols, "grid_rows": rows, "tiles": clean}


class Go2RtcError(Exception):
    """Base error raised by the go2rtc client."""


class Go2RtcConnectionError(Go2RtcError):
    """Raised when the go2rtc endpoint cannot be reached."""


class Go2RtcPayloadError(Go2RtcError):
    """Raised when go2rtc returns an unexpected response."""


def validate_port(port: int) -> int:
    """Return a valid TCP port or raise ValueError."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return port


def validate_credentials(username: str, password: str) -> tuple[str, str]:
    """Return valid go2rtc credentials or raise ValueError."""
    if not isinstance(username, str) or not 1 <= len(username) <= 64:
        raise ValueError("username must be 1 to 64 characters")
    if not isinstance(password, str) or not 16 <= len(password) <= 256:
        raise ValueError("password must be 16 to 256 characters")
    if any(ord(character) < 32 or ord(character) == 127
           for character in username + password):
        raise ValueError("credentials cannot contain control characters")
    return username, password


def normalize_host(value: str) -> str:
    """Normalize an IP/hostname or a bare HTTP URL to a host value."""
    if not isinstance(value, str):
        raise ValueError("host must be a string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("host cannot contain control characters")
    raw = value.strip()
    if not raw:
        raise ValueError("host is required")

    if "://" in raw:
        parsed = urlsplit(raw)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "enter a host or a bare http://host URL without a port or path"
            )
        raw = parsed.hostname.replace("%25", "%", 1)
    elif raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    elif any(character in raw for character in "/?#@"):
        raise ValueError("host cannot contain a path, query, or credentials")
    elif raw.count(":") == 1:
        raise ValueError("enter the API port in the separate port field")

    if ":" in raw:
        try:
            address, separator, scope = raw.partition("%")
            if separator and (not scope or not re.fullmatch(r"[A-Za-z0-9_.-]+", scope)):
                raise ValueError("invalid IPv6 scope")
            raw = ipaddress.IPv6Address(address).compressed
            return raw + (separator + scope if separator else "")
        except ValueError as err:
            raise ValueError("invalid IPv6 address") from err
    else:
        try:
            raw = raw.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as err:
            raise ValueError("invalid host") from err
        if not raw or len(raw) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in raw.split(".")
        ):
            raise ValueError("invalid host")
    return raw


def host_from_internal_url(value: str | None) -> str | None:
    """Extract a normalized host from Home Assistant's configured internal URL."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    try:
        return normalize_host(parsed.hostname)
    except ValueError:
        return None


def _url_host(host: str) -> str:
    normalized = normalize_host(host)
    if ":" in normalized:
        normalized = normalized.replace("%", "%25", 1)
        return f"[{normalized}]"
    return normalized


def api_base_url(host: str, port: int) -> str:
    """Build the management URL, including brackets for IPv6 literals."""
    return f"http://{_url_host(host)}:{validate_port(port)}"


def api_url(host: str, port: int) -> str:
    """Build the go2rtc streams API URL."""
    return f"{api_base_url(host, port)}{API_STREAMS_PATH}"


def frame_url(host: str, port: int) -> str:
    """Build the go2rtc JPEG snapshot URL without query data or credentials."""
    return f"{api_base_url(host, port)}{API_FRAME_PATH}"


def rtsp_url(
    host: str, port: int, stream: str, username: str, password: str
) -> str:
    """Build a safe RTSP URL for a discovered stream name."""
    username, password = validate_credentials(username, password)
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return (
        f"rtsp://{userinfo}{_url_host(host)}:{validate_port(port)}/"
        f"{quote(stream, safe='_-')}"
    )


def _stream_map(payload: Any) -> dict[str, dict[str, Any]]:
    """Normalize either supported go2rtc response shape."""
    if isinstance(payload, dict) and "streams" in payload:
        payload = payload["streams"]
    if not isinstance(payload, dict):
        raise ValueError("go2rtc streams response is not an object")
    return {
        name: info if isinstance(info, dict) else {}
        for name, info in payload.items()
        if isinstance(name, str)
    }


def extract_streams(payload: Any) -> dict[str, dict[str, Any]]:
    """Return only Eufy streams from a go2rtc response."""
    return {
        name: info
        for name, info in _stream_map(payload).items()
        if name.startswith(STREAM_PREFIX)
    }


def stream_count(payload: Any) -> int:
    """Return the total number of named streams in a go2rtc response."""
    return len(_stream_map(payload))


def stream_summary(info: dict[str, Any]) -> dict[str, int | bool]:
    """Return non-sensitive producer/consumer counts for an API stream object."""
    producers = info.get("producers")
    consumers = info.get("consumers")
    producer_count = len(producers) if isinstance(producers, list) else 0
    consumer_count = len(consumers) if isinstance(consumers, list) else 0
    return {
        "producers": producer_count,
        "consumers": consumer_count,
        "streaming": consumer_count > 0,
    }


def summarize_streams(
    streams: dict[str, dict[str, Any]],
    *, anonymous: bool = False,
) -> dict[str, dict[str, int | bool]]:
    """Omit URLs; public diagnostics also replace private camera/serial names."""
    return {(f"stream_{index}" if anonymous else name): stream_summary(streams[name])
            for index, name in enumerate(sorted(streams), 1)}


class Go2RtcClient:
    """Fetch camera stream metadata from one local go2rtc instance."""

    def __init__(
        self, session: Any, host: str, api_port: int, timeout: int,
        username: str, password: str,
    ) -> None:
        from aiohttp import encode_basic_auth

        self.host = normalize_host(host)
        self.api_port = validate_port(api_port)
        username, password = validate_credentials(username, password)
        self.url = api_url(self.host, self.api_port)
        self.frame_url = frame_url(self.host, self.api_port)
        self.total_stream_count = 0
        self._session = session
        self._timeout = timeout
        self._headers = {"Authorization": encode_basic_auth(username, password)}

    async def async_get_streams(self) -> dict[str, dict[str, Any]]:
        """Fetch and parse the configured Eufy streams."""
        from aiohttp import ClientError, ClientResponseError

        try:
            async with self._session.get(
                self.url, timeout=self._timeout, headers=self._headers,
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except ClientResponseError as err:
            raise Go2RtcConnectionError(
                f"go2rtc returned HTTP {err.status} from the stream endpoint"
            ) from None
        except (ClientError, TimeoutError) as err:
            raise Go2RtcConnectionError(
                "cannot reach the configured go2rtc stream endpoint"
            ) from None
        except ValueError as err:
            raise Go2RtcPayloadError(
                "go2rtc returned invalid JSON from the stream endpoint"
            ) from None

        try:
            self.total_stream_count = stream_count(payload)
            return extract_streams(payload)
        except ValueError as err:
            raise Go2RtcPayloadError(
                "unexpected go2rtc stream response"
            ) from None

    async def async_get_grid_layout(self):
        """Optional metadata: old apps/invalid replies yield no automatic layout."""
        from aiohttp import ClientError
        url = api_base_url(self.host, self.api_port) + "/eufy-grid-layout.json"
        try:
            async with self._session.get(url, headers=self._headers, timeout=2,
                                         allow_redirects=False) as response:
                if response.status != 200:
                    return None
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) > 65536:
                        return None
                return validate_grid_layout(json.loads(body))
        except (ClientError, TimeoutError, ValueError, TypeError):
            return None

    async def async_get_frame(
        self, stream: str, *, timeout: float | None = None
    ) -> bytes:
        """Fetch a cached JPEG directly from go2rtc.

        Width and height are deliberately omitted: go2rtc 1.9.14 keys its JPEG
        cache by stream name, not rendition dimensions. One full frame per
        camera avoids wrong-sized cache hits and lets Home Assistant scale it.
        """
        from aiohttp import ClientError, ClientResponseError

        if not isinstance(stream, str) or not stream.startswith(STREAM_PREFIX):
            raise Go2RtcPayloadError("invalid Eufy stream name")
        request_timeout = min(self._timeout, 9) if timeout is None else timeout
        if type(request_timeout) not in (int, float) or not 0 < request_timeout <= 300:
            raise Go2RtcPayloadError("invalid snapshot timeout")
        try:
            async with self._session.get(
                self.frame_url,
                params={"src": stream, "cache": "30s"},
                timeout=request_timeout,
                headers=self._headers,
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                if content_type.lower() != "image/jpeg":
                    raise Go2RtcPayloadError("go2rtc snapshot was not JPEG")
                if response.content_length and response.content_length > MAX_FRAME_BYTES:
                    raise Go2RtcPayloadError("go2rtc snapshot exceeded the size limit")
                frame = await response.read()
        except Go2RtcPayloadError:
            raise
        except ClientResponseError as err:
            raise Go2RtcConnectionError(
                f"go2rtc returned HTTP {err.status} from the snapshot endpoint"
            ) from None
        except (ClientError, TimeoutError) as err:
            raise Go2RtcConnectionError("cannot fetch the go2rtc snapshot") from None
        if (
            not frame
            or len(frame) > MAX_FRAME_BYTES
            or not frame.startswith(b"\xff\xd8")
            or not frame.endswith(b"\xff\xd9")
        ):
            raise Go2RtcPayloadError("go2rtc returned an invalid JPEG snapshot")
        return frame
