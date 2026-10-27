"""Optional vendor Unix-seconds expiry; never guess milliseconds or a lifetime."""
from __future__ import annotations


def parse_expiry(value):
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) == 10:
        value = int(value)
    if type(value) is not int or not 1577836800 <= value < 4102444800:
        return None
    return value


def cache_expiry_fields(credentials):
    expiry = parse_expiry(credentials.get("token_expires_at"))
    return {"authTokenExpiresAt": expiry} if expiry is not None else {}


def next_check_delay(cache, now, interval, can_renew=False):
    expiry = parse_expiry((cache or {}).get("authTokenExpiresAt"))
    if expiry is None or expiry <= now:
        return min(interval, 900)  # Unknown/old expiry: bounded 15-minute checks.
    return min(interval, max(60, expiry - now - (300 if can_renew else 0)))
