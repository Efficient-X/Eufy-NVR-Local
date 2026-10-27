"""Guided startup, bounded opt-in renewal, and one owned video worker."""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from pathlib import Path

import aiohttp
import eufy_cloud as ec
from auth_login import sign_response_is_live
from token_expiry import next_check_delay


def read_guided_cache(path):
    """Private server-owned cache; validate before using fields in requests."""
    try:
        path = Path(path)
        if path.stat().st_size > 65536:
            return None
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            return None
        if (type(cached.get("cacheVersion")) is not int or cached["cacheVersion"] != 2
                or cached.get("signalingIdentitySource") != "nvr_admin"
                or cached.get("region") not in {"us-pr", "eu-pr", "ie-pr"}
                or not isinstance(cached.get("webCountry"), str)
                or not re.fullmatch(r"[A-Z]{2}", cached["webCountry"])
                or not isinstance(cached.get("stationSn"), str)
                or not re.fullmatch(r"T8N00[A-Z0-9]{7,15}", cached["stationSn"])
                or not isinstance(cached.get("accountFingerprint"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", cached["accountFingerprint"])):
            return None
        for key in ("authToken", "gtoken", "userId", "signalingUserId"):
            value = cached.get(key)
            if (not isinstance(value, str) or not 1 <= len(value) <= 8192
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                return None
        # Media/account checks never receive the remembered account password.
        return {key: value for key, value in cached.items() if key != "rememberedLogin"}
    except (OSError, ValueError, TypeError):
        return None


def classify_sign_response(status, body):
    if sign_response_is_live(status, body):
        return "valid"
    if status in {401, 403} or (isinstance(body, dict) and body.get("code") == 26084):
        return "rejected"
    # Rate limits, server failures and unexpected replies are not bad passwords.
    return "unavailable"


async def check_saved_account(path):
    cached = read_guided_cache(path)
    if cached is None:
        return "missing"
    _, url = ec.smart_urls(cached["stationSn"], cached["region"])
    headers = {
        "x-auth-token": cached["authToken"], "gtoken": cached["gtoken"],
        "app-name": "eufy_mega", "model-type": "WEB",
        "web-country": cached["webCountry"], "origin": "https://security.eufy.com",
        "referer": "https://security.eufy.com/",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(url, headers=headers, allow_redirects=False) as response:
                if response.status in {401, 403}:
                    return "rejected"
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        return "unavailable"
                return classify_sign_response(response.status, json.loads(raw))
    except (OSError, ValueError, TypeError, aiohttp.ClientError, TimeoutError):
        return "unavailable"


class StartupController:
    """One coordinator owns the child process and all cache-check scheduling."""

    def __init__(self, auth_path, worker, *, checker=check_saved_account,
                 clock=time.monotonic, check_interval=21600, retry_interval=60,
                 local_credentials_check=None, renewal=None, wall_clock=time.time):
        self.auth_path, self.worker, self.checker = Path(auth_path), worker, checker
        self.clock = clock
        self.wall_clock = wall_clock
        self._reload_after_renewal = False
        self.check_interval, self.retry_interval = check_interval, retry_interval
        self.state, self.warning = "checking_account", None
        self._next_check = 0
        self._lock = asyncio.Lock()
        self._task = None
        self._closed = False
        self.local_credentials_check = local_credentials_check
        self.renewal = renewal

    def can_setup(self):
        return (not self._closed and not self._lock.locked() and not self.worker.running()
                and self.state in {"setup_required", "sign_in_required"})

    def public_status(self):
        return {"state": self.state, "warning": self.warning,
                **(self.renewal.public_status() if self.renewal else {}),
                "can_setup": self.can_setup(),
                "can_retry": not self._closed and not self._lock.locked()
                    and self.state in {"connection_unavailable", "service_error"}}

    async def reconcile(self):
        if self._closed or self._lock.locked():
            return
        async with self._lock:
            running = self.worker.running()
            self.state, self.warning = "checking_account", None
            if not running and self.local_credentials_check is not None:
                try:
                    ready = self.local_credentials_check()
                except Exception:
                    ready = False
                if not ready:
                    self.state = "local_video_setup_required"
                    return
            try:
                async with asyncio.timeout(20):
                    outcome = await self.checker(self.auth_path)
            except asyncio.CancelledError:
                raise
            except Exception:
                outcome = "unavailable"
            proactive = bool(outcome == "valid" and self.renewal and self.renewal.due())
            if (outcome == "rejected" or proactive) and self.renewal is not None:
                try:
                    if not proactive:
                        await self.worker.stop()
                        running = False
                    self.state = "renewing_sign_in"
                    renewed = await self.renewal.renew()
                    if renewed == "renewed":
                        self._reload_after_renewal = True
                        async with asyncio.timeout(20):
                            outcome = await self.checker(self.auth_path)
                        if outcome == "rejected":
                            self.renewal.pause()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    outcome = "unavailable" if proactive else "rejected"
            if outcome in {"missing", "rejected"}:
                try:
                    await self.worker.stop()
                except Exception:
                    self.state = "service_error"
                    return
                self.state = "setup_required" if outcome == "missing" else "sign_in_required"
            elif outcome == "valid":
                if self._reload_after_renewal:
                    try:
                        await self.worker.stop()
                        running = False
                        self._reload_after_renewal = False
                    except Exception:
                        self.state = "service_error"
                        return
                if not running:
                    self.state = "starting_video"
                    try:
                        async with asyncio.timeout(120):
                            await self.worker.start()
                    except asyncio.CancelledError:
                        await self.worker.stop()
                        raise
                    except Exception:
                        await self.worker.stop()
                        self.state = "service_error"
                        return
                self.state = "video_service_running"
                delay = next_check_delay(read_guided_cache(self.auth_path),
                    self.wall_clock(), self.check_interval,
                    bool(self.renewal and self.renewal.can_renew()))
                self._next_check = self.clock() + delay
            else:
                # Keep a running producer on a temporary cloud/network failure.
                self.state = "video_service_running" if running else "connection_unavailable"
                self.warning = "account_check_unavailable"
                self._next_check = self.clock() + self.retry_interval

    def saved(self):
        """Call synchronously immediately after the wizard atomically saves."""
        if not self.can_setup():
            raise RuntimeError("Setup is not available")
        self.state = "checking_account"  # blocks another write before the task runs
        self._task = asyncio.create_task(self.reconcile())

    def retry(self):
        if self.public_status()["can_retry"]:
            self.state = "checking_account"
            self._task = asyncio.create_task(self.reconcile())

    def forget(self):
        if self.renewal is None:
            raise ValueError("Remembered sign-in is not available")
        self.renewal.forget()

    async def tick(self):
        if self._closed or self._lock.locked():
            return
        if self.state == "video_service_running" and not self.worker.running():
            self.state, self.warning = "service_error", None
            return  # No unlimited restart/discovery storm.
        if (self.state in {"video_service_running", "connection_unavailable"}
                and self.clock() >= self._next_check):
            await self.reconcile()

    async def close(self):
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.worker.stop()
        self.state = "stopped"

