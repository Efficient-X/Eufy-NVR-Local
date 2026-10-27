# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 mega-yfue
# Python adaptation by Efficient X (EFX), 2026.
"""Signed HTTP protocol adapted from eufy-sdk's crypto/passport/mega-client.

Upstream commit 5f92bea559fa1a5b619a84a9966ccc90a541735e, Apache-2.0.
Changes: Python async transport injection, no automatic retries/re-login, older
bridge envelope return contract, and errors that exclude response bodies/tokens.
This module does not open a network connection itself.
"""
from __future__ import annotations

import json
import time
from typing import Any

from eufy_crypto import aes_decrypt, aes_encrypt, derive_share_key, gen_keypair, sign
from os import urandom


class CloudProtocolError(Exception):
    """Protocol failure safe to show without echoing cloud response content."""


def request_headers(country: str = "") -> dict[str, str]:
    return {
        "App-Name": "eufy_mega", "Content-Type": "application/json",
        "X-Encryption-Info": "algo_ecdh", "X-Replay-Info": "replay",
        "X-Request-Ts": str(int(time.time())), "X-Request-Once": urandom(16).hex(),
        "Model_type": "WEB", "Model-Type": "WEB", "Web-Country": country,
    }


async def exchange(send, url: str, bootstrap: str, *, token: str, country: str, timeout: int):
    private, public = gen_keypair()
    encrypted_public = aes_encrypt(public, bootstrap)
    headers = request_headers(country)
    key_id = urandom(16).hex()
    headers["X-Key-Ident"] = key_id
    headers["X-Signature"] = sign(bootstrap, headers["X-Request-Ts"], headers["X-Request-Once"], encrypted_public)
    if token:
        headers["X-Auth-Token"] = token
    response = await send(url, json_body={"client_public_key": encrypted_public}, headers=headers, timeout=timeout)
    envelope = response.get("json")
    payload = envelope.get("data") if isinstance(envelope, dict) else None
    if response.get("status") != 200 or not isinstance(payload, dict) or not payload.get("server_public_key"):
        raise CloudProtocolError("Cloud key exchange was rejected")
    server_public = aes_decrypt(payload["server_public_key"], bootstrap)
    return key_id, private, server_public, derive_share_key(private, server_public)


async def signed_post(send, url: str, data: Any, *, key_id: str, shared_key: str,
                      token: str, gtoken: str, country: str, extra_headers,
                      return_envelope: bool, timeout: int):
    serialized = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    encrypted = aes_encrypt(serialized, shared_key) if serialized else ""
    headers = request_headers(country)
    headers.update({
        "X-Key-Ident": key_id, "X-Auth-Token": token or "", "GToken": gtoken or "",
        "Content-Type": "text/plain",
        "X-Signature": sign(shared_key, headers["X-Request-Ts"], headers["X-Request-Once"], encrypted),
    })
    if extra_headers:
        headers.update(extra_headers)
    response = await send(url, data=encrypted or None, headers=headers, timeout=timeout)
    if response.get("status") != 200:
        raise CloudProtocolError(f"Cloud request returned HTTP {response.get('status')}")
    envelope = response.get("json")
    if not isinstance(envelope, dict):
        raise CloudProtocolError("Cloud response was not an envelope")
    raw_payload = envelope.get("data")
    decoded = None
    if isinstance(raw_payload, str) and raw_payload:
        plain = aes_decrypt(raw_payload, shared_key)
        try:
            decoded = json.loads(plain)
        except json.JSONDecodeError:
            decoded = plain
    elif raw_payload:
        decoded = raw_payload
    if return_envelope:
        # The caller needs nonzero challenge codes and provisional tokens, too.
        return {"code": envelope.get("code"), "msg": envelope.get("msg"), "data": decoded, "raw": envelope}
    return decoded if raw_payload else envelope
