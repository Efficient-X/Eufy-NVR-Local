"""Opt-in renewal with private persistence, no unattended challenges or retries."""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

import eufy_cloud as ec
from account_setup import selected_admin_id
from auth_login import _write_private_json, account_fingerprint
from startup_controller import read_guided_cache
from token_expiry import parse_expiry, cache_expiry_fields


class RememberedLogin:
    """One launcher owns this file and serializes renewal with account setup."""
    def __init__(self, path, budget, *, wall_clock=time.time):
        self.path, self.budget = Path(path), budget
        self.wall_clock = wall_clock
        self._lock = asyncio.Lock()

    def can_renew(self):
        try:
            saved = self._read()
            login = self._credentials(saved)
            last = saved.get("lastRenewalAttemptAt", 0)
            return bool(login and not login["paused"] and
                        type(last) in (int, float) and math.isfinite(last) and last >= 0 and
                        self.wall_clock() >= last + 3600)
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def due(self):
        try:
            expiry = parse_expiry(self._read().get("authTokenExpiresAt"))
            return bool(self.can_renew() and expiry is not None and
                        0 < expiry - self.wall_clock() <= 300)
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def _read(self):
        if self.path.stat().st_size > 65536:
            raise ValueError("Invalid private account state")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or read_guided_cache(self.path) is None:
            raise ValueError("Invalid private account state")
        return saved

    def _credentials(self, saved):
        login = saved.get("rememberedLogin")
        if (not isinstance(login, dict) or type(login.get("version")) is not int
                or login["version"] != 1 or type(login.get("paused")) is not bool
                or not isinstance(login.get("email"), str) or "@" not in login["email"]
                or not 3 <= len(login["email"]) <= 320
                or not isinstance(login.get("password"), str)
                or not 1 <= len(login["password"]) <= 1024
                or account_fingerprint(login["email"], saved["region"], saved["webCountry"])
                    != saved["accountFingerprint"]):
            return None
        return login

    def public_status(self):
        try:
            saved = self._read()
            login = self._credentials(saved)
            return {"remembered": "rememberedLogin" in saved,
                    "renewal_paused": bool(login is None or login["paused"])
                        if "rememberedLogin" in saved else False}
        except (OSError, ValueError, TypeError, KeyError):
            return {"remembered": False, "renewal_paused": False}

    def forget(self):
        saved = self._read()
        saved.pop("rememberedLogin", None)
        _write_private_json(str(self.path), saved)

    def pause(self):
        saved = self._read()
        if "rememberedLogin" in saved:
            saved["rememberedLogin"] = {**saved["rememberedLogin"],
                                       "paused": True, "reason": "sign_in_required"}
            _write_private_json(str(self.path), saved)

    def _replace_if_current(self, before, after):
        # Synchronous comparison/write: no interleaving with another request in
        # this process. External editing of the private state is unsupported.
        if self._read() != before:
            return False
        _write_private_json(str(self.path), after)
        return True

    async def renew(self):
        """One opted-in attempt near known expiry or after rejection, never an outage."""
        if self._lock.locked():
            return "unavailable"
        async with self._lock:
            try:
                saved = self._read()
                login = self._credentials(saved)
                if login is None or login["paused"]:
                    return "manual"
                if not self.can_renew():
                    self.pause()
                    return "manual"
                # Mark paused BEFORE any password request. A crash/restart cannot
                # replay an unattended login, even if the response was lost.
                pending = {**saved, "lastRenewalAttemptAt": self.wall_clock(), "rememberedLogin": {
                    **login, "paused": True, "reason": "attempt_in_progress"}}
                if not self._replace_if_current(saved, pending):
                    return "manual"
                if self.budget.reserve():
                    return "manual"
            except (OSError, ValueError, TypeError, KeyError):
                return "manual"
            reason = "sign_in_required"
            try:
                async with asyncio.timeout(45):
                    creds = await ec.login(login["email"], login["password"],
                        region=saved["region"], country=saved["webCountry"],
                        save_captcha=False, timeout=15)
                    # The saved user and selected recorder must still be the same.
                    if creds.get("user_id") != saved["userId"]:
                        raise ValueError("Account identity changed")
                    raw = await ec.station_list(creds["auth_token"],
                        gtoken=creds.get("gtoken", ""), region=saved["region"],
                        web_country=saved["webCountry"], timeout=15)
                admin = selected_admin_id(raw, saved["stationSn"])
                if not admin or admin != saved["signalingUserId"]:
                    raise ValueError("Recorder ownership changed")
                for key in ("auth_token", "gtoken", "user_id"):
                    value = creds.get(key)
                    if (not isinstance(value, str) or not 1 <= len(value) <= 8192
                            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                        raise ValueError("Invalid account reply")
                updated = {**{k: v for k, v in pending.items() if k != "authTokenExpiresAt"},
                           **cache_expiry_fields(creds), "authToken": creds["auth_token"],
                           "gtoken": creds["gtoken"], "userId": creds["user_id"],
                           "rememberedLogin": {**login, "paused": False, "reason": None}}
                return "renewed" if self._replace_if_current(pending, updated) else "manual"
            except ec.EufyLoginRateLimited:
                reason = "rate_limited"
                try:
                    self.budget.block(86400)
                except OSError:
                    reason = "protection_unavailable"
            except (ec.EufyCaptchaRequired, ec.EufyVerificationRequired):
                reason = "human_check_required"
            except asyncio.CancelledError:
                # Persisted pause already protects against a restart replay.
                raise
            except Exception:
                # Even an uncertain network reply stops automatic password retries.
                reason = "sign_in_required"
            try:
                self._replace_if_current(pending, {**pending, "rememberedLogin": {
                    **pending["rememberedLogin"], "reason": reason}})
            except (OSError, ValueError, TypeError, KeyError):
                pass
            return "manual"

