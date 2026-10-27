#!/usr/bin/env python3
"""Headless auth bootstrap: email/password -> auth.json (for the engine / add-on).

Credentials are read from the ENVIRONMENT (the add-on run.sh exports them); they are
never passed on argv and never printed. Logs in via the reversed eufy passport flow
(eufy_cloud.login), then fetches the station list to learn the NVR ``station_sn`` the
engine needs, and writes auth.json in the shape eufy_stream.py expects.

Env in:
  EUFY_EMAIL, EUFY_PASSWORD              required
  EUFY_REGION                            server region: US | EU | IE (default US)
  EUFY_COUNTRY                           account country code (e.g. US, EU, IE, AU);
                                         defaults to EUFY_REGION when unset
  EUFY_STATION_SN                        optional override / fallback
  EUFY_CAPTCHA_ID, EUFY_CAPTCHA_ANSWER   optional, if a prior run reported a captcha
  EUFY_VERIFICATION_CODE                optional six-digit mailbox verification code
  EUFY_AUTH                              output path (default <bridge>/auth.json)
Exit: 0 ok | 2 missing creds | 3 login failed | 4 no station_sn.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eufy_cloud as ec  # noqa: E402

REGION_MAP = {"US": "us-pr", "EU": "eu-pr", "IE": "ie-pr"}
_SERIAL_RE = re.compile(r"[A-Z0-9]{12,20}")
_CACHE_VERSION = 2


def _normalized_key(value: object) -> str:
    """Normalize JSON field names without retaining or logging their values."""
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _find_admin_user_id(raw, station_sn: str) -> str:
    """Find the selected NVR's administrator id in station metadata.

    Eufy's current web client signs action-3 messages with the value stored as
    ``admin_user_id``.  The exact response nesting varies by region/firmware, so
    search the selected station object defensively and only fall back to a
    globally unique administrator id.  The value is never printed.
    """
    admin_keys = {"adminuserid"}
    station_keys = {"stationsn", "devicesn", "sn"}
    matched_station_objects: list[dict] = []
    global_admin_ids: list[str] = []

    def collect_admin_ids(value, output: list[str]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if (_normalized_key(key) in admin_keys
                        and isinstance(child, (str, int))
                        and str(child).strip()):
                    output.append(str(child).strip())
                collect_admin_ids(child, output)
        elif isinstance(value, list):
            for child in value:
                collect_admin_ids(child, output)

    def locate(value) -> None:
        if isinstance(value, dict):
            if any(
                _normalized_key(key) in station_keys
                and isinstance(child, (str, int))
                and str(child).strip() == station_sn
                for key, child in value.items()
            ):
                matched_station_objects.append(value)
            for child in value.values():
                locate(child)
        elif isinstance(value, list):
            for child in value:
                locate(child)

    if station_sn:
        locate(raw)
        for station in matched_station_objects:
            selected: list[str] = []
            collect_admin_ids(station, selected)
            if selected:
                return selected[0]

    collect_admin_ids(raw, global_admin_ids)
    unique = list(dict.fromkeys(global_admin_ids))
    return unique[0] if len(unique) == 1 else ""


def signaling_identities_match(passport_user_id: str, admin_user_id: str) -> bool:
    """Compare identities through fixed-length digests; never expose either id."""
    if not passport_user_id or not admin_user_id:
        return False
    passport_digest = hashlib.sha256(passport_user_id.encode("utf-8")).digest()
    admin_digest = hashlib.sha256(admin_user_id.encode("utf-8")).digest()
    return hmac.compare_digest(passport_digest, admin_digest)


def account_fingerprint(email: str, region: str, country: str) -> str:
    """Return a stable, non-plaintext binding for an auth cache."""
    identity = "\0".join((email.strip().casefold(), region, country.upper()))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _read_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    return cached if isinstance(cached, dict) else {}


def _cache_account_matches(
    cached: dict, email: str, region: str, country: str
) -> bool:
    """Accept a v1/v2 cache only when it is bound to this configured account."""
    actual = cached.get("accountFingerprint", "")
    return bool(
        cached.get("cacheVersion") in {1, _CACHE_VERSION}
        and cached.get("authToken")
        and cached.get("stationSn")
        and isinstance(actual, str)
        and hmac.compare_digest(
            actual, account_fingerprint(email, region, country))
    )


def cache_matches(path: str, email: str, region: str, country: str) -> bool:
    """Accept only a v2 cache carrying the NVR's signaling identity."""
    cached = _read_cache(path)
    return bool(
        _cache_account_matches(cached, email, region, country)
        and cached.get("cacheVersion") == _CACHE_VERSION
        and cached.get("signalingUserId")
        and cached.get("signalingIdentitySource") == "nvr_admin"
    )


def _write_private_json(out_path: str, value: dict) -> None:
    """Atomically write sensitive runtime state with owner-only permissions."""
    old = os.umask(0o077)
    temp_path = None
    try:
        out_dir = os.path.dirname(os.path.abspath(out_path))
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=out_dir,
                prefix=".auth-", suffix=".tmp", delete=False) as handle:
            temp_path = handle.name
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, out_path)
        temp_path = None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        os.umask(old)


def sign_response_is_live(status: int, body) -> bool:
    """Return whether a ws/sign response proves the cached session is usable."""
    return bool(
        status == 200
        and isinstance(body, dict)
        and body.get("code") == 0
        and isinstance(body.get("data"), str)
        and body["data"]
    )


async def cache_is_live(path: str, email: str, region: str, country: str) -> bool:
    """Validate and, where possible, enrich a cache without a fresh login."""
    cached = _read_cache(path)
    if not _cache_account_matches(cached, email, region, country):
        return False
    try:
        _ws_url, sign_url = ec.smart_urls(cached["stationSn"], region)
        headers = {
            "x-auth-token": cached["authToken"],
            "gtoken": cached.get("gtoken", ""),
            "app-name": cached.get("appName", "eufy_mega"),
            "model-type": "WEB",
            "web-country": country,
            "origin": "https://security.eufy.com",
            "referer": "https://security.eufy.com/",
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(sign_url, headers=headers) as response:
                try:
                    body = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, json.JSONDecodeError):
                    return False
                if not sign_response_is_live(response.status, body):
                    return False
    except (OSError, ValueError, TypeError, KeyError, aiohttp.ClientError, asyncio.TimeoutError):
        return False

    if cache_matches(path, email, region, country):
        return True

    # Migrate a live v1 cache without spending another passport login. If
    # metadata is temporarily unavailable, keep the live cache untouched; the
    # bridge can use its legacy fallback and the next periodic check retries.
    try:
        station_raw = await ec.station_list(
            cached["authToken"], gtoken=cached.get("gtoken", ""),
            region=region, web_country=country,
        )
        admin_user_id = _find_admin_user_id(station_raw, cached["stationSn"])
        if not admin_user_id:
            print("auth_login: live cache has no NVR administrator identity yet; "
                  "keeping the current live session", file=sys.stderr)
            return True
        passport_user_id = cached.get("userId", "")
        cached.update({
            "cacheVersion": _CACHE_VERSION,
            "signalingUserId": admin_user_id,
            "signalingIdentitySource": "nvr_admin",
        })
        _write_private_json(path, cached)
        print("auth_login: live cache enriched with NVR signaling identity")
        print("auth_login: passport and signaling identities "
              + ("match" if signaling_identities_match(
                  passport_user_id, admin_user_id) else "differ"))
        return True
    except Exception as exc:  # noqa: BLE001 - a live token must not trigger a fresh login
        print("auth_login: live cache could not be enriched: "
              f"{type(exc).__name__}; keeping the current live session",
              file=sys.stderr)
        return True


def _find_station_sn(raw) -> str:
    """Pull an NVR station serial out of a station_list response. Prefer the structured
    parse; fall back to a recursive scan for a *station*sn key holding a serial."""
    try:
        for n in ec.parse_stations(raw):
            if n.get("station_sn"):
                return n["station_sn"]
    except Exception:  # noqa: BLE001
        pass
    found: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                kl = k.lower()
                if (isinstance(v, str) and "sn" in kl and "station" in kl
                        and _SERIAL_RE.fullmatch(v)):
                    found.append(v)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(raw)
    return found[0] if found else ""


async def main() -> int:
    email = os.environ.get("EUFY_EMAIL", "").strip()
    region_opt = (os.environ.get("EUFY_REGION") or "US").strip().upper()
    region = REGION_MAP.get(region_opt, "us-pr")
    # Account country (the login `ab` field / Web-Country header). Defaults to the region, but
    # can be set independently (EUFY_COUNTRY) so accounts registered outside US/EU/IE — e.g. AU —
    # authenticate against the nearest server region while still identifying their real country.
    country = (os.environ.get("EUFY_COUNTRY") or region_opt).strip().upper()

    if len(sys.argv) == 3 and sys.argv[1] == "--check-cache":
        if not email:
            return 1
        return 0 if cache_matches(sys.argv[2], email, region, country) else 1

    if len(sys.argv) == 3 and sys.argv[1] == "--check-cache-live":
        if not email:
            return 1
        live = await cache_is_live(sys.argv[2], email, region, country)
        if live:
            print("auth_login: cached auth session is live; login not required")
        return 0 if live else 1

    password = os.environ.get("EUFY_PASSWORD", "")
    if not email or not password:
        print("auth_login: EUFY_EMAIL / EUFY_PASSWORD not set", file=sys.stderr)
        return 2

    try:
        creds = await ec.login(
            email, password, region=region, country=country,
            captcha_id=os.environ.get("EUFY_CAPTCHA_ID", ""),
            answer=os.environ.get("EUFY_CAPTCHA_ANSWER", ""),
            verification_code=os.environ.get("EUFY_VERIFICATION_CODE", ""))
    except ec.EufyCloudError as exc:
        print(f"auth_login: login failed: {exc}", file=sys.stderr)
        return 3

    auth_token = creds["auth_token"]
    gtoken = creds.get("gtoken", "")
    user_id = creds.get("user_id", "")
    print("auth_login: login OK")

    # Always fetch station metadata: besides discovering station_sn, the current
    # official web client obtains the NVR administrator id used for action-3
    # signaling from this metadata. An explicit station_sn only selects the NVR.
    station_sn = os.environ.get("EUFY_STATION_SN", "").strip()
    station_raw = None
    try:
        station_raw = await ec.station_list(
            auth_token, gtoken=gtoken, region=region, web_country=country)
        if not station_sn:
            station_sn = _find_station_sn(station_raw)
    except Exception as exc:  # noqa: BLE001
        print(f"auth_login: station metadata lookup failed: {type(exc).__name__}",
              file=sys.stderr)
    if not station_sn:
        print("auth_login: could not determine station_sn "
              "(set EUFY_STATION_SN to override)", file=sys.stderr)
        return 4
    print("auth_login: station selected")

    admin_user_id = _find_admin_user_id(station_raw, station_sn)
    signaling_user_id = admin_user_id or user_id
    if admin_user_id:
        print("auth_login: NVR administrator signaling identity found")
        match = signaling_identities_match(user_id, admin_user_id)
        print("auth_login: passport and signaling identities "
              + ("match" if match else "differ"))
        signaling_source = "nvr_admin"
    else:
        print("auth_login: NVR administrator signaling identity unavailable; "
              "using passport identity", file=sys.stderr)
        signaling_source = "passport"
    if not signaling_user_id:
        print("auth_login: no usable signaling identity returned", file=sys.stderr)
        return 5

    out_path = os.environ.get(
        "EUFY_AUTH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.json"))
    auth = {
        "authToken": auth_token,
        "gtoken": gtoken,
        "userId": user_id,
        "signalingUserId": signaling_user_id,
        "signalingIdentitySource": signaling_source,
        "stationSn": station_sn,
        "region": region,
        "webCountry": country,
        "appName": "eufy_mega",
        "cacheVersion": _CACHE_VERSION,
        "accountFingerprint": account_fingerprint(email, region, country),
    }
    _write_private_json(out_path, auth)
    print("auth_login: refreshed auth state (secrets not logged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
