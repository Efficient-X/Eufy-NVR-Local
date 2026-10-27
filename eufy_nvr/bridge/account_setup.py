"""Transport-independent account setup; not an HTTP server or an enabled app UI.

One instance belongs to one authenticated operator session. Never serialize its
__dict__: only public_status() is suitable for a UI. The eventual ingress layer
must authenticate requests and enforce CSRF/origin/body-size limits separately.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import re
import secrets
import time

import eufy_cloud as ec
from auth_login import (
    REGION_MAP, _CACHE_VERSION, _write_private_json,
    account_fingerprint,
)


def captcha_image(value):
    """Return only a bounded raster data URI, never a remote URL or SVG."""
    if not isinstance(value, str) or len(value) > 350_000:
        return None
    if value.startswith("data:"):
        prefix, separator, value = value.partition(",")
        if not separator or prefix not in ("data:image/png;base64", "data:image/jpeg;base64"):
            return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    mime = ("image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else
            "image/jpeg" if raw.startswith(b"\xff\xd8\xff") else None)
    if not mime or len(raw) > 256_000:
        return None
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")




def selected_admin_id(raw, serial):
    """Require identity metadata on this NVR, not a different account device."""
    found = set()
    station_keys = {"stationsn", "devicesn", "sn"}

    def normalized(key):
        return re.sub(r"[^a-z0-9]", "", str(key).casefold())

    def collect(value):
        if isinstance(value, dict):
            if any(normalized(key) in station_keys and child != serial
                   for key, child in value.items()):
                return
            for key, child in value.items():
                if (normalized(key) == "adminuserid" and type(child) in (str, int)
                        and str(child).strip()):
                    found.add(str(child).strip())
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    def walk(value):
        if isinstance(value, dict):
            if any(normalized(key) in station_keys and child == serial
                   for key, child in value.items()):
                collect(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw)
    return next(iter(found)) if len(found) == 1 else ""


class AccountSetup:
    """Explicit, bounded login steps with cancellation and private persistence."""

    def __init__(self, *, clock=time.monotonic, ttl=600, max_attempts=5, budget=None):
        self._clock = clock
        self._ttl = ttl
        self._max_attempts = max_attempts
        self._budget = budget
        self._lock = asyncio.Lock()
        self._generation = 0
        self._blocked_until = 0.0
        self._recent_attempts = []
        self._clear()
        self.state = "credentials"

    def _clear(self):
        self._email = self._password = self._region = self._country = ""
        self._remember = False
        self._captcha_id = ""
        self._image = None
        self._creds = {}
        self._stations = {}
        self._station_raw = None
        self._deadline = 0.0
        self._attempts = 0
        self.error = None

    def cancel(self):
        self._generation += 1
        self._clear()
        self.state = "cancelled"

    def _expire(self):
        if self._deadline and self._clock() >= self._deadline:
            self.cancel()
            self.state = "expired"

    def public_status(self):
        self._expire()
        result = {"state": self.state}
        if self.error:
            result["error"] = self.error
        if self.state == "captcha" and self._image:
            result["captcha_image"] = self._image
        if self.state == "select_nvr":
            result["stations"] = [
                {"id": key, "name": value["name"]}
                for key, value in self._stations.items()
            ]
        if self._blocked_until > self._clock():
            result["retry_after_seconds"] = int(self._blocked_until - self._clock()) + 1
        return result

    async def start(self, email, password, *, region="US", country="", remember=False):
        if self._lock.locked():
            return {"state": "busy"}
        if self._blocked_until > self._clock():
            return self.public_status()
        if (type(remember) is not bool
                or not isinstance(email, str) or not 3 <= len(email.strip()) <= 320
                or "@" not in email or not isinstance(password, str)
                or not 1 <= len(password) <= 1024 or not isinstance(region, str)
                or region not in REGION_MAP
                or not isinstance(country, str)
                or (country and not re.fullmatch(r"[A-Za-z]{2}", country))):
            return {"state": "credentials", "error": "invalid_input"}
        self.cancel()
        self._email, self._password = email.strip(), password
        self._remember = remember
        self._region, self._country = REGION_MAP[region], (country or region).upper()
        self._deadline = self._clock() + self._ttl
        self.state = "credentials"
        return await self._attempt()

    async def answer_captcha(self, answer):
        self._expire()
        if self.state != "captcha":
            return self.public_status()
        if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 128:
            return {"state": self.state, "error": "invalid_input"}
        return await self._attempt(answer=answer.strip())

    async def verify_email(self, code):
        self._expire()
        if self.state != "email_code":
            return self.public_status()
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            return {"state": self.state, "error": "invalid_code"}
        return await self._attempt(verification_code=code)

    async def _attempt(self, *, answer="", verification_code=""):
        if self._lock.locked():
            return {"state": "busy"}
        self._expire()
        if self.state in ("expired", "cancelled"):
            return self.public_status()
        self._recent_attempts = [at for at in self._recent_attempts
                                 if self._clock() - at < 60]
        if (self._attempts >= self._max_attempts
                or len(self._recent_attempts) >= self._max_attempts):
            self._blocked_until = self._clock() + 60
            self.cancel()
            self.state = "rate_limited"
            return self.public_status()
        async with self._lock:
            if self._budget is not None:
                try:
                    wait = self._budget.reserve()
                except OSError:
                    self.cancel()
                    self.state, self.error = "credentials", "protection_unavailable"
                    return self.public_status()
                if wait:
                    self._blocked_until = self._clock() + wait
                    self.cancel()
                    self.state = "rate_limited"
                    return self.public_status()
            generation = self._generation
            previous = self.state
            self._attempts += 1
            self._recent_attempts.append(self._clock())
            self.state, self.error = "connecting", None
            try:
                async with asyncio.timeout(45):
                    creds = await ec.login(
                        self._email, self._password, region=self._region,
                        country=self._country, captcha_id=self._captcha_id,
                        answer=answer, verification_code=verification_code,
                        save_captcha=False, timeout=15,
                    )
                    if generation != self._generation:
                        return self.public_status()
                    self._expire()
                    if generation != self._generation:
                        return self.public_status()
                    raw = await ec.station_list(
                        creds["auth_token"], gtoken=creds.get("gtoken", ""),
                        region=self._region, web_country=self._country, timeout=15,
                    )
                if generation != self._generation:
                    return self.public_status()
                self._expire()
                if generation != self._generation:
                    return self.public_status()
                stations = {}
                for station in ec.parse_stations(raw):
                    serial = station.get("station_sn")
                    # This app supports T8N00, not all Eufy HomeBase products.
                    if not isinstance(serial, str) or not re.fullmatch(r"T8N00[A-Z0-9]{7,15}", serial):
                        continue
                    if any(item["station_sn"] == serial for item in stations.values()):
                        continue
                    name = station.get("name")
                    stations[secrets.token_urlsafe(18)] = {
                        "station_sn": serial,
                        "name": name[:120] if isinstance(name, str) and name else "Eufy PoE NVR",
                    }
                if not stations:
                    self.cancel()
                    self.state, self.error = "credentials", "no_supported_nvr"
                else:
                    if not self._remember:
                        self._password = ""
                    self._captcha_id = ""
                    self._image = None
                    self._creds, self._station_raw, self._stations = creds, raw, stations
                    self.state = "select_nvr"
            except ec.EufyCaptchaRequired as exc:
                if generation == self._generation:
                    self._captcha_id = exc.captcha_id if isinstance(exc.captcha_id, str) else ""
                    self._image = captcha_image(exc.image)
                    if self._captcha_id and self._image:
                        self.state = "captcha"
                    else:
                        self.cancel()
                        self.state, self.error = "credentials", "captcha_unavailable"
            except ec.EufyVerificationRequired:
                if generation == self._generation:
                    self._captcha_id, self._image = "", None
                    self.state = "email_code"
            except ec.EufyLoginRateLimited:
                # A late vendor refusal still counts after the operator cancels.
                # Otherwise closing a tab could bypass the persistent cooldown.
                self._blocked_until = self._clock() + 86400
                protection_failed = False
                if self._budget is not None:
                    try:
                        self._budget.block(86400)
                    except OSError:
                        protection_failed = True
                if generation == self._generation:
                    # Conservative local lockout, not a claim of provider retry time.
                    self.cancel()
                    if protection_failed:
                        self.state, self.error = "credentials", "protection_unavailable"
                    else:
                        self.state = "rate_limited"
            except ec.EufyCloudError:
                if generation == self._generation:
                    self.state = previous
                    self.error = "login_not_accepted"
            except asyncio.CancelledError:
                if generation == self._generation:
                    self.cancel()
                raise
            except Exception:  # Never return raw vendor exceptions or HTTP URLs.
                if generation == self._generation:
                    self.state = previous
                    self.error = "connection_failed"
            return self.public_status()

    def select_nvr(self, selection, auth_path):
        """auth_path must come from trusted server configuration, never the UI."""
        self._expire()
        if self.state != "select_nvr":
            return self.public_status()
        if not isinstance(selection, str) or selection not in self._stations:
            return {"state": "select_nvr", "error": "invalid_selection"}
        serial = self._stations[selection]["station_sn"]
        admin_id = selected_admin_id(self._station_raw, serial)
        if not admin_id:
            self.error = "nvr_owner_identity_unavailable"
            return self.public_status()
        from token_expiry import cache_expiry_fields
        auth = {
            **cache_expiry_fields(self._creds),
            "authToken": self._creds["auth_token"],
            "gtoken": self._creds.get("gtoken", ""),
            "userId": self._creds.get("user_id", ""),
            "signalingUserId": admin_id,
            "signalingIdentitySource": "nvr_admin",
            "stationSn": serial, "region": self._region,
            "webCountry": self._country, "appName": "eufy_mega",
            "cacheVersion": _CACHE_VERSION,
            "accountFingerprint": account_fingerprint(
                self._email, self._region, self._country),
        }
        if self._remember:
            # Consent and connection are one atomic write, never two mismatched files.
            auth["rememberedLogin"] = {
                "version": 1, "email": self._email, "password": self._password,
                "paused": False, "reason": None,
            }
        try:
            _write_private_json(str(auth_path), auth)
        except OSError:
            self.error = "storage_failed"
            return self.public_status()
        self.cancel()
        self.state = "complete"
        return self.public_status()
