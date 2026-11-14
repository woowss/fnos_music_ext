"""Unit tests for pure-Python NetEase WEAPI/EAPI client and crypto modules."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.parse import parse_qs

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient
import httpx
import pytest

_MUSICBOX_DIR = str(Path(__file__).resolve().parents[2] / "musicbox-service")
if _MUSICBOX_DIR not in sys.path:
    sys.path.insert(0, _MUSICBOX_DIR)

import app as musicbox_app
import netease_client
import netease_crypto
import netease_ext


def _decrypt_eapi_request_hex(hex_str: str) -> bytes:
    cipher = Cipher(algorithms.AES(netease_crypto.EAPI_KEY), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(bytes.fromhex(hex_str)) + decryptor.finalize()
    pad_len = padded[-1]
    return padded[:-pad_len]


def _encrypt_eapi_response_bytes(payload: dict) -> bytes:
    raw = json.dumps(payload).encode("utf-8")
    pad_len = 16 - (len(raw) % 16)
    padded = raw + bytes([pad_len] * pad_len)
    cipher = Cipher(algorithms.AES(netease_crypto.EAPI_KEY), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def test_weapi_encrypt_produces_valid_form_fields():
    encoded = netease_crypto.weapi_encrypt({"id": 123, "csrf_token": "abc"})
    assert set(encoded.keys()) == {"params", "encSecKey"}
    assert isinstance(encoded["params"], str) and len(encoded["params"]) > 16
    assert isinstance(encoded["encSecKey"], str) and len(encoded["encSecKey"]) == 256
    int(encoded["encSecKey"], 16)


def test_eapi_encrypt_and_response_decrypt_roundtrip():
    api_path = "/api/cloudsearch/get/web"
    encoded = netease_crypto.eapi_encrypt(api_path, {"s": "9-nine", "type": 1})
    assert "params" in encoded
    decrypted_req = _decrypt_eapi_request_hex(encoded["params"])
    parts = decrypted_req.decode("utf-8").split("-36cd479b6b5-")
    assert len(parts) == 3
    assert parts[0] == api_path
    assert json.loads(parts[1]) == {"s": "9-nine", "type": 1}

    sample_resp = {"code": 200, "result": {"songs": [{"id": 56}]}}
    cipher_bytes = _encrypt_eapi_response_bytes(sample_resp)
    assert netease_crypto.eapi_response_decrypt(cipher_bytes) == sample_resp
    assert netease_crypto.eapi_response_decrypt(json.dumps(sample_resp).encode("utf-8")) == sample_resp


def test_build_request_id_format():
    rid = netease_crypto.build_request_id()
    ts_part, rand_part = rid.split("_", 1)
    assert ts_part.isdigit() and len(ts_part) == 13
    assert rand_part.isdigit() and len(rand_part) == 3


def test_qr_login_persists_cookie_atomically_and_logout_clears(tmp_path, monkeypatch):
    cookie_path = tmp_path / "netease-musicbox" / "cookie.txt"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/weapi/login/qrcode/unikey":
            return httpx.Response(200, json={"code": 200, "unikey": "uk-123"})
        if request.url.path == "/weapi/login/qrcode/client/login":
            form = parse_qs(request.content.decode("utf-8"))
            assert "params" in form and "encSecKey" in form
            return httpx.Response(
                200,
                json={"code": 803, "nickname": "TestUser", "avatarUrl": "https://img.test/a.jpg"},
                headers=[
                    ("Set-Cookie", "MUSIC_U=secret_u; Domain=.music.163.com; Path=/"),
                    ("Set-Cookie", "__csrf=csrf_tok; Domain=.music.163.com; Path=/"),
                ],
            )
        if request.url.path == "/weapi/nuser/account/get":
            return httpx.Response(
                200,
                json={"code": 200, "profile": {"userId": 1001, "nickname": "TestUser", "vipType": 11}},
            )
        if request.url.path == "/weapi/logout":
            return httpx.Response(200, json={"code": 200})
        return httpx.Response(404)

    client = netease_client.NetEaseClient(
        cookie_path=cookie_path,
        transport=httpx.MockTransport(handler),
    )
    qr = client.qr_unikey()
    assert qr["ok"] is True
    assert qr["data"]["unikey"] == "uk-123"
    assert "codekey=uk-123" in qr["data"]["qr_url"]

    check = client.qr_check("uk-123")
    assert check["data"]["logged_in"] is True
    assert check["data"]["code"] == 803
    assert cookie_path.exists()
    content_before_err = cookie_path.read_text(encoding="utf-8")
    assert "secret_u" in content_before_err

    # Failed save_cookies must not clobber the existing cookie jar
    monkeypatch.setattr(
        client._jar,
        "save",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        client.save_cookies()
    assert cookie_path.read_text(encoding="utf-8") == content_before_err

    status = client.auth_status()
    assert status["data"]["logged_in"] is True
    assert status["data"]["user_id"] == 1001
    assert status["data"]["vip_type"] == 11

    out = client.logout()
    assert out == {"ok": True, "data": {"logged_in": False}}
    assert not client.has_auth_cookie()
    assert "secret_u" not in cookie_path.read_text(encoding="utf-8")


def test_cloudsearch_quality_modes_and_64bit_playlist_ids(tmp_path, monkeypatch):
    songs_fixture = {
        "code": 200,
        "result": {
            "songCount": 2,
            "songs": [
                {
                    "id": 101,
                    "name": "Master Track",
                    "dt": 210000,
                    "fee": 1,
                    "ar": [{"id": 1, "name": "Artist A"}],
                    "al": {"id": 2, "name": "Album X", "picUrl": "https://img.test/x.jpg"},
                    "sq": {"br": 999000},
                    "hr": {"br": 1800000},
                    "privilege": {
                        "id": 101,
                        "fee": 1,
                        "pl": 0,
                        "dl": 0,
                        "maxBrLevel": "jymaster",
                        "downloadMaxBrLevel": "jymaster",
                        "freeTrialPrivilege": {"resConsumable": False},
                    },
                },
                {
                    "id": 102,
                    "name": "Dolby Sky Track",
                    "dt": 185000,
                    "fee": 0,
                    "ar": [{"id": 3, "name": "Artist B"}],
                    "al": {"id": 4, "name": "Album Y"},
                    "sq": {"br": 880000},
                    "privilege": {
                        "id": 102,
                        "fee": 0,
                        "pl": 999000,
                        "dl": 999000,
                        "maxBrLevel": "sky",
                        "downloadMaxBrLevel": "sky",
                    },
                },
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/eapi/cloudsearch/get/web":
            return httpx.Response(200, json=songs_fixture)
        if request.url.path == "/eapi/song/enhance/player/url/v1":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [
                        {"id": 101, "code": 404, "url": None, "freeTrialInfo": {"start": 0, "end": 30}},
                        {"id": 102, "code": 200, "url": "https://cdn.test/102.flac", "level": "sky"},
                    ],
                },
            )
        if request.url.path == "/weapi/user/playlist":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "playlist": [
                        {
                            "id": 7368514547,
                            "name": "64-bit Playlist",
                            "trackCount": 14,
                            "creator": {"userId": 1001, "nickname": "Me"},
                        }
                    ],
                },
            )
        return httpx.Response(404)

    client = netease_client.NetEaseClient(
        cookie_path=tmp_path / "cookie.txt",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(netease_client, "get_client", lambda reset=False: client)

    songs = netease_ext.search_catalog(keyword="9-nine", stype="song", limit=20)
    assert len(songs) == 2
    assert songs[0]["max_quality"] == "jymaster"
    assert songs[0]["playable_status"] == "vip_required"
    assert songs[0]["fee"] == 1
    assert songs[0]["has_jm"] is True
    assert songs[0]["has_hr"] is True
    assert songs[0]["has_sq"] is True
    assert songs[1]["max_quality"] == "sky"
    assert songs[1]["has_sky"] is True
    assert songs[1]["playable_status"] == "ok"

    tc = TestClient(musicbox_app.app)
    resp_manual = tc.get("/api/v1/search?keyword=9-nine&mode=manual_save")
    assert resp_manual.status_code == 200
    assert [it["song_id"] for it in resp_manual.json()["data"]] == [101, 102]
    assert resp_manual.json()["data"][0]["max_quality"] == "jymaster"

    resp_playback = tc.get("/api/v1/search?keyword=9-nine&mode=playback")
    assert resp_playback.status_code == 200
    assert [it["song_id"] for it in resp_playback.json()["data"]] == [102]
    assert resp_playback.json()["data"][0]["max_quality"] == "sky"

    playlists_raw = client.user_playlists(1001)
    assert playlists_raw["playlist"][0]["id"] == 7368514547


def test_external_api_hook_optional_and_fault_tolerant(tmp_path, monkeypatch):
    monkeypatch.delenv(netease_client.EXTERNAL_API_URL_ENV, raising=False)
    client = netease_client.NetEaseClient(cookie_path=tmp_path / "cookie.txt")
    assert client.external_api_get("/song/url/v1", {"id": 56}) is None
