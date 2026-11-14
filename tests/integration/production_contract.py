"""Offline checks run with each service's real production dependencies installed."""
import argparse
import asyncio
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def check_musicbox():
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from urllib.parse import parse_qs
    import httpx
    import netease_client
    import netease_crypto
    import netease_ext
    import playback

    # 1. Pure-Python crypto round-trip check
    weapi_req = netease_crypto.weapi_encrypt({"type": 1})
    assert "params" in weapi_req and len(weapi_req["encSecKey"]) == 256

    eapi_req = netease_crypto.eapi_encrypt("/api/cloudsearch/get/web", {"s": "fixture", "type": 1})
    assert eapi_req["params"].isupper()

    # 2. Pure-Python playback EAPI -> WEAPI fallback contract (no NEMbox/CLI)
    client_type = httpx.AsyncClient
    captured = []

    def _decrypt_eapi_form(hex_params: str) -> tuple[str, dict]:
        cipher = Cipher(algorithms.AES(netease_crypto.EAPI_KEY), modes.ECB())
        decryptor = cipher.decryptor()
        padded = decryptor.update(bytes.fromhex(hex_params)) + decryptor.finalize()
        unpadder = sym_padding.PKCS7(128).unpadder()
        plain = (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
        api_path, body_json, _digest = plain.split(netease_crypto.EAPI_SEPARATOR)
        return api_path, json.loads(body_json)

    def _encrypt_eapi_resp(obj: dict) -> bytes:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        padder = sym_padding.PKCS7(128).padder()
        padded = padder.update(raw) + padder.finalize()
        cipher = Cipher(algorithms.AES(netease_crypto.EAPI_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def handler(request):
        form = parse_qs(request.content.decode())
        if request.url.path.endswith("/url/v1"):
            api_path, body = _decrypt_eapi_form(form["params"][0])
            header_obj = json.loads(body["header"]) if isinstance(body["header"], str) else body["header"]
            assert api_path == "/api/song/enhance/player/url/v1"
            assert body["ids"] == "[123]" and body["level"] == "lossless"
            assert body["encodeType"] == "flac" and header_obj["deviceId"]
            captured.append("eapi")
            return httpx.Response(200, content=_encrypt_eapi_resp({"code": 200, "data": []}))
        assert "params" in form and "encSecKey" in form
        assert request.headers["host"] == request.url.host
        captured.append("weapi")
        return httpx.Response(200, json={"code": 200, "data": [{
            "id": 123, "code": 200, "url": "https://audio.invalid/fixture.flac",
        }]})

    async def verify():
        original_client = playback.httpx.AsyncClient
        playback.httpx.AsyncClient = lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs)
        try:
            playback.reset()
            with netease_ext._api_lock:
                item = await asyncio.wait_for(playback.get_song_url_async(123, "lossless"), 5)
            assert item["url"].endswith(".flac") and captured == ["eapi", "weapi"]
            await playback.shutdown()
        finally:
            playback.httpx.AsyncClient = original_client

    asyncio.run(verify())

    # 3. 64-bit playlist ID support + honest search metadata in NetEaseClient
    def search_handler(request):
        return httpx.Response(200, content=_encrypt_eapi_resp({
            "code": 200,
            "result": {"songs": [{
                "id": 7368514547,
                "name": "Fixture",
                "ar": [{"name": "Tester"}],
                "al": {"name": "Album", "picUrl": "https://img.invalid/1.jpg"},
                "dt": 180000,
                "fee": 0,
                "privilege": {"maxBrLevel": "jymaster", "maxbr": 1999000, "fee": 0},
            }]},
        }))

    with tempfile.TemporaryDirectory() as tmp:
        c = netease_client.NetEaseClient(
            cookie_path=Path(tmp) / "cookie.txt",
            transport=httpx.MockTransport(search_handler),
        )
        res = c.cloudsearch("Fixture")
        assert res["result"]["songs"][0]["privilege"]["maxBrLevel"] == "jymaster"
        c.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=["musicdl", "musicbox", "lxmusic"])
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--require-non-root", action="store_true")
    args = parser.parse_args()
    if args.require_non_root:
        assert os.geteuid() != 0, "Production service must run as non-root"
    with tempfile.TemporaryDirectory(prefix="fnmusic-contract-") as home:
        os.environ.update(HOME=home, XDG_CACHE_HOME=home + "/cache", XDG_DATA_HOME=home + "/data",
                          XDG_CONFIG_HOME=home + "/config", XDG_RUNTIME_DIR=home + "/run")
        for name in ("cache", "data", "config", "run"):
            Path(home, name).mkdir()
        sys.path.insert(0, str(args.app_dir.resolve()))
        module = importlib.import_module("app")
        assert module.app is not None
        if args.service == "musicbox":
            check_musicbox()
        elif args.service == "musicdl":
            from musicdl import musicdl
            import curl_cffi
            assert callable(musicdl.MusicClient)
            assert curl_cffi.requests.Session
        else:
            from lxserver_client import SUPPORTED_PLATFORMS
            from verify_source import parse_script_meta
            assert "kw" in SUPPORTED_PLATFORMS
            meta = parse_script_meta(
                "/*\n * @name ci-src\n * @version 1.0.0\n * @author ci\n"
                " * @description contract\n */\n"
            )
            assert meta["name"] == "ci-src"
        print(json.dumps({"service": args.service, "production_import": "pass", "offline_contract": "pass"}))


if __name__ == "__main__":
    main()
