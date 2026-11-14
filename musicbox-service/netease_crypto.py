"""Pure-Python NetEase Cloud Music WEAPI and EAPI cryptography helpers.

Implements standard NetEase WEAPI (AES-128-CBC + textbook 1024-bit RSA) and
EAPI (AES-128-ECB + MD5) primitives using ``cryptography`` and Python stdlib
with zero side effects at import time.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any
from urllib.parse import urlencode

from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

WEAPI_MODULUS_HEX = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725"
    "152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312"
    "ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424"
    "d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
)
WEAPI_MODULUS = int(WEAPI_MODULUS_HEX, 16)
WEAPI_EXPONENT = 0x10001
WEAPI_NONCE = b"0CoJUm6Qyw8W8jud"
WEAPI_IV = b"0102030405060708"
EAPI_KEY = b"e82ckenh8dichen8"
EAPI_SEPARATOR = "-36cd479b6b5-"
_SECRET_ALPHABET = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _pkcs7_pad(data: bytes, block_bits: int = 128) -> bytes:
    padder = sym_padding.PKCS7(block_bits).padder()
    return padder.update(data) + padder.finalize()


def _pkcs7_unpad(data: bytes, block_bits: int = 128) -> bytes:
    unpadder = sym_padding.PKCS7(block_bits).unpadder()
    return unpadder.update(data) + unpadder.finalize()


def _aes_cbc_encrypt_b64(plaintext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(WEAPI_IV))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()
    return base64.b64encode(ciphertext)


def _create_secret_key(size: int = 16) -> bytes:
    raw = os.urandom(size)
    return bytes(_SECRET_ALPHABET[byte % len(_SECRET_ALPHABET)] for byte in raw)


def _rsa_encrypt(secret_key: bytes) -> str:
    value = int.from_bytes(secret_key[::-1], "big")
    encrypted = pow(value, WEAPI_EXPONENT, WEAPI_MODULUS)
    return f"{encrypted:0256x}"


def weapi_encrypt(payload: dict[str, Any]) -> dict[str, str]:
    """Encrypt a NetEase WEAPI request payload into ``params`` and ``encSecKey``."""
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    secret_key = _create_secret_key(16)
    first_pass = _aes_cbc_encrypt_b64(text, WEAPI_NONCE)
    second_pass = _aes_cbc_encrypt_b64(first_pass, secret_key).decode("ascii")
    return {
        "params": second_pass,
        "encSecKey": _rsa_encrypt(secret_key),
    }


def eapi_encrypt(api_path: str, payload: dict[str, Any]) -> dict[str, str]:
    """Encrypt an EAPI request for ``api_path`` and return form dict ``{"params": HEX}``."""
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    message = f"nobody{api_path}use{text}md5forencrypt"
    digest = hashlib.md5(message.encode("utf-8")).hexdigest()
    raw = f"{api_path}{EAPI_SEPARATOR}{text}{EAPI_SEPARATOR}{digest}".encode("utf-8")
    cipher = Cipher(algorithms.AES(EAPI_KEY), modes.ECB())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(_pkcs7_pad(raw)) + encryptor.finalize()
    return {"params": encrypted.hex().upper()}


def eapi_response_decrypt(raw: bytes | str) -> Any:
    """Decrypt an EAPI binary or hex-encoded response body into parsed JSON."""
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return json.loads(stripped)
        cipher_bytes = bytes.fromhex(stripped)
    else:
        stripped_bytes = raw.strip()
        if stripped_bytes.startswith((b"{", b"[")):
            return json.loads(stripped_bytes.decode("utf-8"))
        # Responses from interface.music.163.com when e_r=True are raw AES-ECB bytes
        # unless a hex-encoded fixture string is passed directly.
        try:
            text_candidate = stripped_bytes.decode("ascii")
        except UnicodeDecodeError:
            cipher_bytes = raw
        else:
            if len(text_candidate) % 2 == 0 and all(ch in "0123456789abcdefABCDEF" for ch in text_candidate):
                cipher_bytes = bytes.fromhex(text_candidate)
            else:
                cipher_bytes = raw
    cipher = Cipher(algorithms.AES(EAPI_KEY), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(cipher_bytes) + decryptor.finalize()
    plaintext = _pkcs7_unpad(padded).decode("utf-8")
    return json.loads(plaintext)


def eapi_form_urlencoded(api_path: str, payload: dict[str, Any]) -> str:
    return urlencode(eapi_encrypt(api_path, payload))


def build_request_id() -> str:
    entropy = int.from_bytes(os.urandom(4), "big") % 100_000_000
    return f"{int(time.time() * 1000)}_{entropy:08d}"[:17]
