"""Headless cloud account adapter for Eufy NVR Local.

The encrypted protocol implementation is adapted from Apache-2.0 eufy-sdk:
eufy_crypto.py and cloud_transport.py. See the packaged licence and provenance
record. This file preserves the bridge's discovery, challenge and login API.

Earlier revisions included vendor-transcribed crypto/request wrappers. Those
were replaced on 27 October 2026, not merely renamed. This is an attributed
community-SDK adaptation, not a claim of an EFX clean-room implementation.
No network calls occur during import.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---- crypto: 'cryptography' is present in this env; pycryptodome is an acceptable
# ---- alternative per the task. We use 'cryptography' for ECDH + AES.
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.backends import default_backend

# aiohttp is imported lazily inside the async functions so that this module stays
# importable / py_compile-valid even where aiohttp is not installed (it is the only
# network dependency and is never touched unless you actually make a request).


# ====================================================================================
# Only the production ("-pr") entries are needed for real use; QA kept for reference.
# fa(service, region) -> ha[service][region] or fallback ha[service]["us-pr"].
# ====================================================================================
DOMAINS: Dict[str, Dict[str, str]] = {
    "mega": {
        "us-pr": "https://mega-us-pr.eufy.com",
        "eu-pr": "https://mega-eu-pr.eufy.com",
        "ie-pr": "https://mega-ie-pr.eufy.com",
    },
    "passport": {
        "us-pr": "https://app-passport-us-pr.eufy.com",
        "eu-pr": "https://app-passport-eu-pr.eufy.com",
        "ie-pr": "https://app-passport-ie-pr.eufy.com",
    },
    "openapi": {
        "us-pr": "https://app-openapi-us-pr.eufy.com",
        "eu-pr": "https://app-openapi-eu-pr.eufy.com",
        "ie-pr": "https://app-openapi-ie-pr.eufy.com",
    },
    "push": {
        "us-pr": "https://app-push-us-pr.eufy.com",
        "eu-pr": "https://app-push-eu-pr.eufy.com",
        "ie-pr": "https://app-push-ie-pr.eufy.com",
    },
    # `security` is the service `wn` (the v3 request wrapper) and the v3 key/exchange
    "security": {
        "us-pr": "https://security-app.eufylife.com",
        "eu-pr": "https://security-app-eu.eufylife.com",
        "ie-pr": "https://security-app-ie.eufylife.com",
    },
    "smart": {
        "us-pr": "https://security-smart.eufylife.com",
        "eu-pr": "https://security-smart-eu.eufylife.com",
        "ie-pr": "https://security-smart-ie.eufylife.com",
    },
}

APP_NAME = "eufy_mega"

# Static bootstrap key used to encrypt the client public key in the v3 key/exchange
# This 32-hex string is the AES key for the exchange call (same Hex.parse semantics:
# 16 raw bytes), and is ALSO the HMAC key (as a string) for X-Signature on exchange.
EXCHANGE_BOOTSTRAP_KEY = "118c12c81e211149304bd70a0c071d01"
EXCHANGE_BOOTSTRAP_KEY_QA = "118c02b71e211049304bd70a0c971d44"

# ("2500a7d5617812f9d52515b2c8f20a3d") against fa("openapi")+"/openapi/oauth/key/
# exchange". We default to the v3 "security" flow that the station_list wrapper uses.
EXCHANGE_BOOTSTRAP_KEY_OPENAPI = "2500a7d5617812f9d52515b2c8f20a3d"
EXCHANGE_BOOTSTRAP_KEY_OPENAPI_QA = "208c02b71e211049304bd70a0c971d44"

KEY_EXCHANGE_PATH_V3 = "/v3/openapi/oauth/key/exchange"
KEY_EXCHANGE_PATH_V1 = "/openapi/oauth/key/exchange"

DEFAULT_TIMEOUT = 30

_BACKEND = default_backend()


def normalize_region(region: str) -> str:
    """Normalize add-on values (``US``/``EU``/``IE``) to cloud table keys."""
    normalized = (region or "us-pr").strip().lower()
    if not normalized.endswith("-pr"):
        normalized += "-pr"
    return normalized


def resolve_region(service: str, region: str) -> str:
    """Return the region key actually selected for a cloud service."""
    table = DOMAINS.get(service, {})
    normalized = normalize_region(region)
    if normalized in table:
        return normalized
    if "us-pr" in table:
        return "us-pr"
    return normalized


def signaling_region(region: str) -> str:
    """Return the service region sent in the WebSocket subprotocol.

    The official client sends the selected smart-service region
    (``US``/``EU``/``IE``), not the account's web-country code. Supplying a
    country such as ``AU`` can join the signaling room but leaves the NVR call
    pending at status 100.
    """
    return resolve_region("smart", region).split("-", 1)[0].upper()


def base_url(service: str, region: str) -> str:
    """fa(service, region) -- region->domain lookup with us-pr fallback."""
    table = DOMAINS.get(service, {})
    return table.get(resolve_region(service, region)) or ""


def smart_urls(station_sn: str, region: str) -> Tuple[str, str]:
    """Return the region-correct WebSocket and sign endpoints for an NVR."""
    smart_base = base_url("smart", region).rstrip("/")
    websocket_base = smart_base.replace("https://", "wss://", 1)
    return (
        f"{websocket_base}/v1/rtc/ws/join?reqtype=nvr",
        # Captured official portal request (2026-10-13) has no station query.
        # The station is selected by sn in the authenticated WebSocket envelope.
        f"{smart_base}/v1/smart/nvr/ws/sign",
    )


# ====================================================================================
# Low-level primitives  (Ya / Xa / $a / tn / an)
# ====================================================================================
def _uuid_hex() -> str:
    """nn() = uuid v4 with dashes stripped. Used for keyindent/once/unisign."""
    return uuid.uuid4().hex


# Apache-2.0 SDK adaptations; keep the older adapter's import surface stable.
from eufy_crypto import (
    gen_keypair, _public_key_from_hex, derive_share_key, sign,
    aes_encrypt, aes_decrypt, _login_keypair, encrypt_password,
)


@dataclass
class KeyExchange:
    """
    Xa instance -- the result of a handshake. Bundle stores keyIndent +
    clientPrivatKeyHex + serverPublicKeyHex + shareKey.
    """
    key_indent: str
    client_priv_hex: str
    server_pub_hex: str
    share_key: str  # 32-char hex string (used as both AES key and HMAC key)

    @classmethod
    def create(cls, key_indent: str, client_priv_hex: str, server_pub_hex: str) -> "KeyExchange":
        return cls(
            key_indent=key_indent,
            client_priv_hex=client_priv_hex,
            server_pub_hex=server_pub_hex,
            share_key=derive_share_key(client_priv_hex, server_pub_hex),
        )


@dataclass
class RandomField:
    """sn().randomField -- per-request nonce bundle."""
    unisign: str = field(default_factory=_uuid_hex)
    keyindent: str = field(default_factory=_uuid_hex)
    once: str = field(default_factory=_uuid_hex)
    ts: int = field(default_factory=lambda: int(round(time.time())))


def _base_headers(rf: RandomField, web_country: str) -> Dict[str, str]:
    """The constant part of sn().headers."""
    return {
        "Content-Type": "application/json",
        "X-Replay-Info": "replay",
        "Model_type": "WEB",
        "Model-type": "WEB",
        "X-Request-Ts": str(rf.ts),
        "X-Request-Once": rf.once,
        "Web-Country": web_country or "",
    }


# ====================================================================================
# HTTP plumbing (lazy aiohttp)
# ====================================================================================
async def _http_post(url: str, *, data: Any = None, json_body: Any = None,
                     headers: Dict[str, str], timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Thin POST helper. Returns {"status": int, "json": <parsed or None>, "text": str}.
    aiohttp is imported here so the module imports without it installed.
    """
    import aiohttp  # local import on purpose

    to = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=to) as session:
        kwargs: Dict[str, Any] = {"headers": headers}
        if json_body is not None:
            kwargs["json"] = json_body
        elif data is not None:
            kwargs["data"] = data
        async with session.post(url, **kwargs) as resp:
            text = await resp.text()
            parsed: Optional[Any]
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            return {"status": resp.status, "json": parsed, "text": text}


# ====================================================================================
# ====================================================================================
async def ecdh_handshake(token: str, *, region: str = "us-pr",
                         web_country: str = "", qa: bool = False,
                         exchange: str = "security", timeout: int = DEFAULT_TIMEOUT) -> KeyExchange:
    from cloud_transport import CloudProtocolError, exchange as exchange_key
    openapi = exchange == "openapi"
    bootstrap = ((EXCHANGE_BOOTSTRAP_KEY_OPENAPI_QA if qa else EXCHANGE_BOOTSTRAP_KEY_OPENAPI)
                 if openapi else (EXCHANGE_BOOTSTRAP_KEY_QA if qa else EXCHANGE_BOOTSTRAP_KEY))
    url = base_url("openapi" if openapi else "security", region) + (KEY_EXCHANGE_PATH_V1 if openapi else KEY_EXCHANGE_PATH_V3)
    try:
        key_id, private, public, shared = await exchange_key(
            _http_post, url, bootstrap, token=token, country=web_country, timeout=timeout)
    except CloudProtocolError as error:
        raise EufyCloudError(str(error)) from None
    return KeyExchange(key_id, private, public, shared)


async def encrypted_post(path: str, data: Any, token: str, *,
                         key_obj: Optional[KeyExchange] = None, gtoken: str = "",
                         region: str = "us-pr", web_country: str = "", service: str = "security",
                         extra_headers: Optional[Dict[str, str]] = None,
                         return_envelope: bool = False, timeout: int = DEFAULT_TIMEOUT,
                         verify_signature: bool = True) -> Any:
    from cloud_transport import CloudProtocolError, signed_post
    # SDK transport authenticates HTTPS and decrypts the envelope. The retained
    # compatibility argument did not enforce response signatures in older builds.
    if key_obj is None:
        key_obj = await ecdh_handshake(token, region=region, web_country=web_country, timeout=timeout)
    try:
        return await signed_post(_http_post, base_url(service, region) + path, data,
            key_id=key_obj.key_indent, shared_key=key_obj.share_key, token=token,
            gtoken=gtoken, country=web_country, extra_headers=extra_headers,
            return_envelope=return_envelope, timeout=timeout)
    except CloudProtocolError as error:
        raise EufyCloudError(str(error)) from None


async def station_list(token: str, *, key_obj: Optional[KeyExchange] = None,
                       gtoken: str = "", region: str = "us-pr",
                       web_country: str = "", timeout: int = DEFAULT_TIMEOUT) -> Any:
    """
    jn() -- POST /v3/house/station_list. Verbatim body:
        {page:0, num:100, orderby:"", station_sn:"", device_sn:""}
    Returns the decrypted payload (typically {code, msg, data:[<stations>...]}).
    """
    return await encrypted_post(
        "/v3/house/station_list",
        {"page": 0, "num": 100, "orderby": "", "station_sn": "", "device_sn": ""},
        token, key_obj=key_obj, gtoken=gtoken, region=region,
        web_country=web_country, timeout=timeout,
    )


async def house_list(token: str, *, key_obj: Optional[KeyExchange] = None,
                     gtoken: str = "", region: str = "us-pr",
                     web_country: str = "", timeout: int = DEFAULT_TIMEOUT) -> Any:
    """Sn() -- POST /v3/house/list. Same body shape as station_list."""
    return await encrypted_post(
        "/v3/house/list",
        {"page": 0, "num": 100, "orderby": "", "station_sn": "", "device_sn": ""},
        token, key_obj=key_obj, gtoken=gtoken, region=region,
        web_country=web_country, timeout=timeout,
    )


async def device_list(token: str, station_sn: str = "", *,
                      key_obj: Optional[KeyExchange] = None, gtoken: str = "",
                      region: str = "us-pr", web_country: str = "",
                      timeout: int = DEFAULT_TIMEOUT) -> Any:
    """
    Fetch the device list. The web app obtains the per-channel camera list from the
    station_list response (each station embeds its devices). This convenience wrapper
    re-uses station_list and optionally filters to one station_sn. Kept as a separate
    entry point so callers have a device-centric API.

    NOTE (INFERRED): the bundle does NOT expose a standalone "/v3/.../device_list"
    endpoint for the NVR camera array -- the camera channels arrive inside
    station_list (fields ch/name/sn/ip/status/mac/dev_type/link_type ...). If a future
    firmware exposes a dedicated device_list path, swap the URL here.
    """
    raw = await station_list(token, key_obj=key_obj, gtoken=gtoken, region=region,
                             web_country=web_country, timeout=timeout)
    nvrs = parse_stations(raw)
    if station_sn:
        nvrs = [n for n in nvrs if n.get("station_sn") == station_sn]
    return nvrs


# ====================================================================================
# Response decoding -> NVRs + cameras
# ====================================================================================
#   ["FPS","bind_status","ch","create_times","dev_type","dev_work_status",
#    "dev_power_switch","ip","isUps","link_type","mac","main_soft_ver","name",
#    "record_mode","resolution","sensor_num","sn","status","update_status"]
# Cameras are the per-channel sub-devices (dev_type 301 for the S4 PoE cams).
def parse_stations(raw: Any) -> List[Dict[str, Any]]:
    """
    Normalize a station_list / house_list response into:
      [{ station_sn, name, ip, online, cameras: [
            { ch, name, sn, local_ip, online }, ... ] }, ...]

    Accepts either the decrypted dict {code,msg,data:[...]} or a bare list. Is defensive
    about the exact nesting because the precise station JSON could only be confirmed by
    a live call -- the camera-array field names (ch/name/sn/ip/status) are CONFIRMED
    from the bundle, the station-wrapper field names (station_sn/station_name/devices/
    device_list) are the eufy-security-client conventions and are marked INFERRED.
    """
    if isinstance(raw, dict):
        stations = raw.get("data")
        if isinstance(stations, dict):
            # Some responses wrap as data:{list:[...]} or data:{stations:[...]}
            stations = (stations.get("list") or stations.get("stations")
                        or stations.get("data") or [])
    else:
        stations = raw
    if not isinstance(stations, list):
        return []

    out: List[Dict[str, Any]] = []
    for st in stations:
        if not isinstance(st, dict):
            continue
        station_sn = (st.get("station_sn") or st.get("sn") or st.get("device_sn") or "")
        # camera/sub-device array: eufy uses "devices" / "device_list" (INFERRED key)
        devs = (st.get("devices") or st.get("device_list") or st.get("device")
                or st.get("channels") or [])
        cameras: List[Dict[str, Any]] = []
        if isinstance(devs, list):
            for d in devs:
                if not isinstance(d, dict):
                    continue
                cameras.append({
                    "ch": d.get("ch"),
                    "name": d.get("name") or d.get("device_name") or "",
                    "sn": d.get("sn") or d.get("device_sn") or "",
                    "local_ip": d.get("ip") or d.get("local_ip") or "",
                    "online": _online(d.get("status")),
                    "dev_type": d.get("dev_type"),
                })
        out.append({
            "station_sn": station_sn,
            "name": st.get("station_name") or st.get("name") or "",
            "ip": st.get("ip") or st.get("local_ip") or st.get("station_ip") or "",
            "online": _online(st.get("status")),
            "cameras": cameras,
        })
    return out


def _online(status: Any) -> bool:
    """eufy 'status' is 1=online / 0=offline for these devices (CONFIRMED in bundle)."""
    try:
        return int(status) == 1
    except (TypeError, ValueError):
        return bool(status)


# ====================================================================================
# login(email, password, region)  --  CONFIRMED against the live login page bundle
# captures/login_js/index-bnAfdtPI.js (de-minified: ...beauty.js). No longer inferred.
# ====================================================================================
# the password is ECDH(P-256)+AES-256-CBC encrypted, not RSA. Key facts:
#   * Host/path:  fa("passport", region) = https://app-passport-<r>-pr.eufy.com, then
#                 "/passport/login"   (NOT "/v1/passport/login").
#   * Password encryption  (bundle te() + se()):
#       1. server EC pubkey = S()?.server_secret_info?.public_key, else the hardcoded
#          fallback uncompressed P-256 point LOGIN_SERVER_PUBKEY_FALLBACK below. A fresh
#          headless login has nothing cached -> the fallback is the value to use.
#       2. generate an ephemeral client P-256 keypair (ee()).
#       3. WebCrypto deriveKey(ECDH(clientPriv, serverPub) -> AES-CBC length:256) ==
#          the raw 32-byte ECDH shared secret (the P-256 X coordinate) used directly as
#          the AES-256 key (WebCrypto takes the first `length` bits, no extra KDF/hash).
#       4. iv = FIRST 16 BYTES of that 32-byte key  (se(): exportKey(key).slice(0,16)).
#       5. ciphertext = AES-256-CBC + PKCS7 over the UTF-8 password bytes.
#       6. password field = base64(ciphertext)  -- NO IV prepended (server re-derives).
#      Verified WITHOUT a live login by a differential test against Node's WebCrypto
#      reference: scripts/login_ref.js + scripts/login_verify.py (byte-identical output).
#   * Login body (bundle ca()):
#       { email, password:<enc>, enc:0, ab:<country>, login_id:"",
#         client_secret_info:{ public_key:<client pub hex "04"||X||Y> },
#         captcha_id:"", answer:"" }
#   * Headers = Ba()/K() base set + { "X-Auth-Token":"", Openudid:SHA256(ua+"_"+email) }
#     + App-Name:"eufy_mega" (the request wrapper injects App-Name/Model-Type).
#   * Response: code 0 -> data{ auth_token, user_id, domain, server_secret_info?, ... }.
#     code 10019 / 100056 -> graphic captcha required (login() auto-fetches it via
#     get_captcha() and tells you to re-run with captcha_id+answer); 26052 fa_info.step
#     -> 2FA; 100028 / 100041 -> login-limit reached (retry after ~24h).

# from the login bundle te(). The passport server holds the matching private key.
LOGIN_SERVER_PUBKEY_FALLBACK = "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a3868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"

# Openudid = SHA256(userAgent + "_" + email).hex (bundle: N.SHA256(`${ua}_${email}`)). The
# web app uses navigator.userAgent; any stable UA works for a fresh login.
WEB_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

PASSPORT_REGION_MAP = {  # high-level region -> passport subdomain region
    "us-pr": "us", "eu-pr": "eu", "ie-pr": "ie",
    "us": "us", "eu": "eu", "ie": "ie",
}


def _passport_base(region: str) -> str:
    r = PASSPORT_REGION_MAP.get(region, "us")
    return f"https://app-passport-{r}-pr.eufy.com"


async def login(email: str, password: str, region: str = "us-pr", *,
                country: str = "US", language: str = "en-US",
                captcha_id: str = "", answer: str = "",
                verification_code: str = "",
                save_captcha: bool = True,
                server_pub_hex: Optional[str] = None,
                user_agent: Optional[str] = None,
                key_obj: Optional[KeyExchange] = None,
                timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Headless passport login, reversed from captures/login_js/index-bnAfdtPI.js. Returns:
        { auth_token, gtoken, user_id, region_domain }

    TRANSPORT: /passport/login is NOT a plaintext POST. In the bundle P()=vn() is the
    encrypted v3 wrapper -- the JSON body (which already contains the ECDH-encrypted
    password) is AES-CBC encrypted under the session share key and HMAC-signed, sent as
    text/plain to the passport host. So login() (1) does the ECDH key/exchange to get the
    share key, then (2) rides encrypted_post(service="passport"). This is the SAME wrapper
    station_list uses (verified live); only the password's own ECDH layer + the body are
    login-specific.

    The password is ECDH(P-256)+AES-256-CBC encrypted (see encrypt_password). Pass
    ``captcha_id``+``answer`` satisfy a graphic captcha.  When Eufy requires mailbox
    verification (``fa_info.step == 26052``), the first call requests an email code and
    raises :class:`EufyVerificationRequired`; repeat the call with the six-digit
    ``verification_code`` to finish the same encrypted login flow.
    """
    ua = user_agent or WEB_USER_AGENT
    srv_pub = server_pub_hex or LOGIN_SERVER_PUBKEY_FALLBACK

    # 1) Establish the global ECDH share key (the web app does this at bootstrap via the
    #    security key/exchange; /passport/login then rides the same encrypted wrapper).
    if key_obj is None:
        key_obj = await ecdh_handshake("", region=region, web_country=country,
                                       exchange="openapi", timeout=timeout)

    # 2) ECDH-encrypt the password against the login server pubkey (bundle te()+se()).
    client_priv, client_pub_hex = _login_keypair()
    enc_password = encrypt_password(password, srv_pub, client_priv)

    # 3) POST the login body (bundle ca()) THROUGH the encrypted wrapper (domainKey passport).
    login_body = {
        "email": email,
        "password": enc_password,
        "enc": 0,
        "ab": country,
        "login_id": "",
        "client_secret_info": {"public_key": client_pub_hex},
        "captcha_id": captcha_id,
        "answer": answer,
    }
    env = await encrypted_post(
        "/passport/login", login_body, "", key_obj=key_obj, gtoken="",
        region=region, web_country=country, service="passport",
        extra_headers=_passport_headers(email, country, language, ua),
        return_envelope=True, verify_signature=False, timeout=timeout)
    code = env.get("code")
    data = env.get("data") if isinstance(env.get("data"), dict) else {}

    # ---- graphic captcha required (10019 = need captcha, 100056 = wrong/again) --------
    if code in (10019, 100056) or (bool(data.get("captcha_id")) and not answer):
        hint = ""
        cap = {}
        try:
            cap = await get_captcha(region=region, language=language,
                                    user_agent=ua, key_obj=key_obj, timeout=timeout)
            if save_captcha:
                saved = _save_captcha_image(cap.get("item", ""))
                hint = (f" Fetched captcha_id={cap.get('captcha_id')!r}"
                        + (f", image saved to {saved}" if saved else "")
                        + "; solve it then call login(..., captcha_id=<id>, answer=<text>).")
        except Exception:  # noqa: BLE001 - never echo vendor bodies/URLs
            hint = " (could not fetch CAPTCHA; retry later)"
        raise EufyCaptchaRequired(
            f"login(): CAPTCHA required.{hint}",
            captcha_id=cap.get("captcha_id", ""), image=cap.get("item", ""))

    # ---- mailbox/device verification (official web /2fa flow) ------------------------
    if (data.get("fa_info") or {}).get("step") == 26052:
        challenge_token = data.get("auth_token") or data.get("token") or ""
        server_secret = data.get("server_secret_info") or {}
        server_public_key = (server_secret.get("public_key", "")
                             if isinstance(server_secret, dict) else "")
        if not challenge_token:
            raise EufyCloudError(
                "login(): verification challenge did not include a provisional token")

        if not verification_code:
            sent = await _send_login_verification_code(
                challenge_token, server_public_key, email=email, country=country,
                language=language, user_agent=ua, region=region, key_obj=key_obj,
                timeout=timeout)
            detail = "code requested by email" if sent else "code request was not confirmed"
            raise EufyVerificationRequired(
                "login(): email verification required (fa_info.step=26052); "
                f"{detail}. Set the six-digit verification code and retry.")

        if not re.fullmatch(r"\d{6}", verification_code):
            raise EufyCloudError("login(): verification code must be exactly six digits")

        verified_body = {**login_body, "verify_code": verification_code}
        env = await encrypted_post(
            "/passport/login", verified_body, challenge_token,
            key_obj=key_obj, gtoken="", region=region, web_country=country,
            service="passport",
            extra_headers=_passport_headers(
                email, country, language, ua, auth_token=challenge_token),
            return_envelope=True, verify_signature=False, timeout=timeout)
        code = env.get("code")
        data = env.get("data") if isinstance(env.get("data"), dict) else {}
        if (data.get("fa_info") or {}).get("step") == 26052:
            raise EufyCloudError("login(): verification code was rejected or expired")
    if code in (100028, 100041):
        raise EufyLoginRateLimited(
            f"login(): login limit reached (code={code}) -- retry after ~24h.")

    auth_token = data.get("auth_token") or data.get("token")
    if code not in (0, None) or not auth_token:
        # Vendor messages may contain echoed credentials or request details.
        raise EufyLoginRejected("login(): credentials or verification were rejected")

    user_id = data.get("user_id") or data.get("userId") or ""
    gtoken = data.get("gtoken") or data.get("g_token") or ""
    if not gtoken and user_id:
        # GToken is NOT returned by login -- the web app computes it client-side as
        # MD5(user_id) hex and stores it (bundle: setItem(ja, C.MD5(user_id).toString())).
        gtoken = hashlib.md5(user_id.encode("utf-8")).hexdigest()
    from token_expiry import parse_expiry
    expiry = parse_expiry(data.get("token_expires_at"))
    return {
        **({"token_expires_at": expiry} if expiry is not None else {}),
        "auth_token": auth_token,
        "gtoken": gtoken,
        "user_id": user_id,
        # `domain` is the regional API host to use afterwards (the region_domain).
        "region_domain": data.get("domain") or data.get("region_domain") or "",
    }



def _openudid(email: str, user_agent: str) -> str:
    """Openudid header = SHA256(userAgent + "_" + email) hex (bundle: N.SHA256(...))."""
    return hashlib.sha256(f"{user_agent}_{email}".encode("utf-8")).hexdigest()


def _passport_headers(email: str, country: str, language: str,
                      user_agent: str, auth_token: str = "") -> Dict[str, str]:
    """
    Ba()/K() base headers + the login overrides (X-Auth-Token:"", Openudid). App-Name and
    Model-Type are injected by the web app's request wrapper; we set them explicitly.
    """
    return {
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "App-Name": APP_NAME,
        "Os_type": "web", "Os-type": "web",
        "Os_version": user_agent, "Os-version": user_agent,
        "Phone_model": "Win32",
        "Model-type": "WEB", "Model_type": "WEB", "Model-Type": "WEB",
        "Country": country, "Language": language, "Timezone": "",
        "X-Auth-Token": auth_token,
        "Openudid": _openudid(email, user_agent),
    }


async def _send_login_verification_code(
        auth_token: str, server_public_key: str, *, email: str, country: str,
        language: str, user_agent: str, region: str, key_obj: KeyExchange,
        timeout: int) -> bool:
    """Request the mailbox code used by Eufy's current web ``/2fa`` screen.

    The official client posts to the regional push service with ``biz_type=1004``
    and ``message_type=2`` while authenticating with the provisional login token.
    """
    if not server_public_key:
        return False
    env = await encrypted_post(
        "/app/sendmsg/verify_code",
        {
            "biz_type": 1004,
            "message_type": 2,
            "client_secret_info": {"public_key": server_public_key},
        },
        auth_token,
        key_obj=key_obj, gtoken="", region=region, web_country=country,
        service="push",
        extra_headers=_passport_headers(
            email, country, language, user_agent, auth_token=auth_token),
        return_envelope=True, verify_signature=False, timeout=timeout)
    return env.get("code") == 0


async def get_captcha(*, region: str = "us-pr", language: str = "en-US",
                      user_agent: Optional[str] = None,
                      key_obj: Optional[KeyExchange] = None,
                      timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    getCaptcha() -- POST /passport/generate/captcha through the encrypted v3 wrapper
    (P()=vn, domainKey "passport"; the browser sends no request body). Returns the
    response's ``data`` dict, typically { captcha_id, item } where ``item`` is the captcha
    image (data-URL/base64). Needs no credentials, so it doubles as a transport smoke-test
    for the exact handshake + passport-encrypted-POST path login() relies on.
    """
    ua = user_agent or WEB_USER_AGENT
    if key_obj is None:
        key_obj = await ecdh_handshake("", region=region, web_country="",
                                       exchange="openapi", timeout=timeout)
    env = await encrypted_post(
        "/passport/generate/captcha", "", "", key_obj=key_obj,
        region=region, web_country="", service="passport",
        extra_headers=_passport_headers("", "", language, ua),
        return_envelope=True, verify_signature=False, timeout=timeout)
    data = env.get("data")
    return data if isinstance(data, dict) else {}


def _save_captcha_image(item: str) -> str:
    """Best-effort: decode a captcha ``item`` (data-URL or bare base64) to a PNG. Returns
    the written path, or "" on failure. Never raises."""
    if not item:
        return ""
    try:
        b64 = item.split(",", 1)[1] if item.startswith("data:") else item
        raw = base64.b64decode(b64)
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "captures", "login_captcha.png")
        with open(out, "wb") as fh:
            fh.write(raw)
        return out
    except Exception:  # noqa: BLE001
        return ""


# ====================================================================================
class EufyCloudError(RuntimeError):
    """Raised on handshake / request / decode / login failures."""


class EufyVerificationRequired(EufyCloudError):
    """Raised after requesting the email code required to finish login."""


class EufyCaptchaRequired(EufyCloudError):
    """Structured human challenge; callers must not log the attributes."""

    def __init__(self, message, *, captcha_id="", image=""):
        super().__init__(message)
        self.captcha_id = captcha_id
        self.image = image


class EufyLoginRateLimited(EufyCloudError):
    """Stop automatic login attempts when the provider rate-limits them."""


class EufyLoginRejected(EufyCloudError):
    """A login was not accepted; no vendor response is exposed."""


__all__ = [
    "DOMAINS", "APP_NAME", "EXCHANGE_BOOTSTRAP_KEY",
    "normalize_region", "resolve_region", "signaling_region", "base_url", "smart_urls",
    "gen_keypair", "derive_share_key", "sign",
    "aes_encrypt", "aes_decrypt", "KeyExchange", "RandomField",
    "ecdh_handshake", "encrypted_post",
    "station_list", "house_list", "device_list", "parse_stations",
    "login", "encrypt_password", "get_captcha", "LOGIN_SERVER_PUBKEY_FALLBACK",
    "EufyCloudError", "EufyVerificationRequired", "EufyCaptchaRequired",
    "EufyLoginRateLimited", "EufyLoginRejected",
]


# Manual / offline self-test of the pure-crypto path (NO network). Run:
#   python scripts/eufy_cloud.py --selftest
if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        # Round-trip ECDH: two parties derive the SAME share key.
        a_priv, a_pub = gen_keypair()
        b_priv, b_pub = gen_keypair()
        ka = derive_share_key(a_priv, b_pub)
        kb = derive_share_key(b_priv, a_pub)
        assert ka == kb, f"ECDH mismatch: {ka} != {kb}"
        assert len(ka) == 32, f"share key not 16 bytes: {ka!r}"
        # Round-trip AES-CBC under the share key.
        msg = json.dumps({"hello": "eufy", "n": 123})
        ct = aes_encrypt(msg, ka)
        assert aes_decrypt(ct, ka) == msg, "AES round-trip failed"
        # Signature determinism.
        sig = sign(ka, 1700000000, "abc123", ct)
        assert len(sig) == 64, "HMAC-SHA256 hex must be 64 chars"
        print("selftest OK:")
        print("  curve            = secp256r1")
        print("  share_key (hex)  =", ka, "(16 bytes / AES-128)")
        print("  aes              = AES-128-CBC + PKCS7, wire = b64(iv||ct)")
        print("  signature sample =", sig)
    else:
        print(__doc__.strip().splitlines()[1])
        print("Run with --selftest for an offline crypto round-trip check.")
