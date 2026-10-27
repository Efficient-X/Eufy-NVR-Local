# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 mega-yfue
# Python adaptation by Efficient X (EFX), 2026.
"""Mega cloud primitives adapted from eufy-sdk's Apache-2.0 core/crypto.ts.

Upstream: 5f92bea559fa1a5b619a84a9966ccc90a541735e.
Changes: Python cryptography API, legacy bridge function signatures, input
validation. See the packaged Apache licence and eufy-sdk-PROVENANCE.md.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

EUFY_MEGA_LOCAL_KEY_HEX = "2500a7d5617812f9d52515b2c8f20a3d"
EUFYLIFE_LOCAL_KEY_HEX = "118c12c81e211149304bd70a0c071d01"
SERVER_STATIC_PUBLIC_KEY_HEX = (
    "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a3868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"
)


def _public_key_from_hex(public_hex: str) -> ec.EllipticCurvePublicKey:
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(public_hex))


def _login_keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    encoded = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return private, encoded.hex()


def gen_keypair() -> tuple[str, str]:
    private, public_hex = _login_keypair()
    return f"{private.private_numbers().private_value:064x}", public_hex


def derive_share_key(private_hex: str, server_public_hex: str) -> str:
    private = ec.derive_private_key(int(private_hex, 16), ec.SECP256R1())
    shared = private.exchange(ec.ECDH(), _public_key_from_hex(server_public_hex))
    # The SDK takes the first sixteen bytes, including any leading zero byte.
    return shared.hex().zfill(64)[:32]


def sign(key_hex: str, ts: int | str, once: str, body: str | None = None) -> str:
    parts = [str(ts), once]
    if body:
        parts.append(body)
    return hmac.new(key_hex[:32].encode("utf-8"), "+".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def _aes_key(key_hex: str) -> bytes:
    raw = bytes.fromhex(key_hex)
    if len(raw) < 16:
        raise ValueError("AES key must contain at least sixteen bytes")
    return raw[:16]


def _encrypt(plain: bytes, key: bytes, iv: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return cipher.update(padded) + cipher.finalize()


def aes_encrypt(plaintext: str, key_hex: str) -> str:
    iv = os.urandom(16)
    encrypted = _encrypt(plaintext.encode("utf-8"), _aes_key(key_hex), iv)
    return base64.b64encode(iv + encrypted).decode("ascii")


def aes_decrypt(payload: str, key_hex: str) -> str:
    raw = base64.b64decode(payload, validate=True)
    if len(raw) < 32 or len(raw) % 16:
        raise ValueError("Invalid encrypted body length")
    cipher = Cipher(algorithms.AES(_aes_key(key_hex)), modes.CBC(raw[:16])).decryptor()
    padded = cipher.update(raw[16:]) + cipher.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def encrypt_password(password: str, server_public_hex: str, private: ec.EllipticCurvePrivateKey) -> str:
    shared = private.exchange(ec.ECDH(), _public_key_from_hex(server_public_hex))
    # Password encryption uses all 32 shared-secret bytes; body encryption uses 16.
    encrypted = _encrypt(password.encode("utf-8"), shared, shared[:16])
    return base64.b64encode(encrypted).decode("ascii")
