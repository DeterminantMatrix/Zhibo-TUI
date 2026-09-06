import asyncio
import json
import ssl
from pathlib import Path

import pytest
import yaml

from zhibo.plugins import fs1_plugin as fs1_plugin
from zhibo.plugins.fs1_plugin import (
    Fs1Plugin,
    _apply_curl_update,
    _as_bool,
    _build_headers,
    _configured_proxy,
    _extract_play_api_url,
    _extract_script_urls,
    _extract_fszb_base_im_api,
    _is_trusted_fs_asset_url,
    _play_api_headers,
    discover_play_api_url,
    fetch_play_url,
    is_trusted_api_url,
    is_trusted_play_api_url,
    parse_curl,
    parse_fs1_export,
    update_from_text,
    update_rooms_config,
    update_stream_libraries,
)


API_URL = fs1_plugin.DEFAULT_API_URL


def test_parse_curl_extracts_fs1_config_fields():
    raw = rf"""curl '{API_URL}?room_id=380348943&sport_id=1' \
  -H 'Origin: https://fszb999.com' \
  -H 'Referer: https://fszb999.com/' \
  -H 'api-version: 9' \
  -H 'authorization: token-value' \
  -H 'imei: imei-value' \
  -H 'dun-imei: dun-value' \
  -H 'User-Agent: Test UA'"""

    assert parse_curl(raw) == {
        "api_url": API_URL,
        "site_url": "https://fszb999.com",
        "api_version": "9",
        "token": "token-value",
        "imei": "imei-value",
        "dun_imei": "dun-value",
        "user_agent": "Test UA",
    }


def test_parse_userscript_export_supports_rotating_fs_domain():
    raw = json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": 1,
            "site_url": "https://www.fszb321.com/broadcast/details?ignored=1",
            "request_url": f"{API_URL}?room_id=847907972&sport_id=1",
            "api_version": "8",
            "authorization": "token-value",
            "cookie": "session=demo",
            "imei": "imei-value",
            "dun_imei": "dun-value",
            "user_agent": "Test UA",
            "client_version": "1.8.4",
        }
    )

    assert parse_fs1_export(raw) == {
        "api_url": API_URL,
        "site_url": "https://www.fszb321.com",
        "api_version": "8",
        "token": "token-value",
        "cookie": "session=demo",
        "imei": "imei-value",
        "dun_imei": "dun-value",
        "user_agent": "Test UA",
        "version": "1.8.4",
    }


def test_userscript_export_rejects_untrusted_fs_domain():
    raw = json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": 1,
            "site_url": "https://fszb148.com.attacker.invalid",
            "api_url": API_URL,
            "authorization": "token-value",
        }
    )

    with pytest.raises(ValueError, match="站点 URL 不受支持"):
        parse_fs1_export(raw)


def test_userscript_v2_export_falls_back_to_nested_snapshot_and_query():
    raw = "说明文字：\n```json\n" + json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": "2",
            "site_url": "",
            "headers": {
                "Authorization": "nested-token",
                "API_VERSION": 8,
                "User_Agent": "Nested UA",
                "Dun_Imei": "nested-dun",
                "Client_Version": "1.9.7",
            },
            "request_snapshot": {
                "room": {
                    "method": "GET",
                    "request_url": f"{API_URL}?room_id=641731356&sport_id=1&match_id=4558502",
                    "headers": {"IMEI": "nested-imei", "Origin": "https://www.fszb148.com"},
                },
                "play": {
                    "play_api_url": "https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url"
                },
            },
        },
        ensure_ascii=False,
    ) + "\n```"

    assert parse_fs1_export(raw) == {
        "api_url": API_URL,
        "site_url": "https://www.fszb148.com",
        "api_version": "8",
        "token": "nested-token",
        "imei": "nested-imei",
        "dun_imei": "nested-dun",
        "user_agent": "Nested UA",
        "version": "1.9.7",
        "play_api_url": "https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url",
    }


def test_userscript_v2_export_accepts_captured_request_list_and_cookie_alias():
    raw = json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": 2,
            "capturedRequests": [
                {
                    "url": "https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url",
                    "headers": {"Authorization": "list-token"},
                },
                {
                    "url": f"{API_URL}?room_id=111&sport_id=1",
                    "headers": {"COOKIES": "sid=demo"},
                }
            ],
        }
    )

    assert parse_fs1_export(raw)["token"] == "list-token"
    assert parse_fs1_export(raw)["cookie"] == "sid=demo"


def test_userscript_export_requires_authorization_or_cookie():
    raw = json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": 1,
            "site_url": "https://fszb321.com",
            "api_url": API_URL,
        }
    )

    with pytest.raises(ValueError, match="没有 authorization 或 Cookie"):
        parse_fs1_export(raw)


def test_update_from_text_imports_json_and_preserves_rooms(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "rooms.yaml"
    config_path.write_text(
        "config: {}\nrooms:\n- name: existing\n  room_id: '123'\n  sport_id: '1'\n",
        encoding="utf-8",
    )
    raw = json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": 1,
            "site_url": "https://www.fszb987.com",
            "api_url": API_URL,
            "authorization": "token-value",
            "cookie": "session=demo",
        }
    )

    monkeypatch.setattr(fs1_plugin, "discover_play_api_url", lambda *args, **kwargs: "")
    applied = update_from_text(raw, config_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert applied["token"] == "token-value"
    assert applied["cookie"] == "session=demo"
    assert applied["play_api_url"] == fs1_plugin.DEFAULT_PLAY_API_URL
    assert data["config"]["site_url"] == "https://www.fszb987.com"
    assert data["rooms"] == [{"name": "existing", "room_id": "123", "sport_id": "1"}]


def test_json_import_refreshes_play_api_from_public_site(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "rooms.yaml"
    raw = json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": 1,
            "site_url": "https://www.fszb148.com",
            "api_url": API_URL,
            "authorization": "token-value",
        }
    )
    play_api_url = "https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url"
    calls = []

    def discover(site_url, *args, **kwargs):
        calls.append(site_url)
        return play_api_url

    monkeypatch.setattr(fs1_plugin, "discover_play_api_url", discover)

    applied = update_from_text(raw, config_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert calls == ["https://www.fszb148.com"]
    assert applied["play_api_url"] == play_api_url
    assert data["config"]["play_api_url"] == play_api_url


def test_json_import_keeps_explicit_play_api_without_discovery(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "rooms.yaml"
    play_api_url = "https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url"
    raw = json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": 1,
            "site_url": "https://www.fszb148.com",
            "api_url": API_URL,
            "play_api_url": play_api_url,
            "authorization": "token-value",
        }
    )

    def unexpected_discovery(*args, **kwargs):
        pytest.fail("explicit play_api_url must not trigger discovery")

    monkeypatch.setattr(fs1_plugin, "discover_play_api_url", unexpected_discovery)

    assert update_from_text(raw, config_path)["play_api_url"] == play_api_url


def test_json_import_replaces_stale_play_api_when_discovery_is_unavailable(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "rooms.yaml"
    config_path.write_text(
        "config:\n"
        "  play_api_url: https://openim-php-api.qaek4a2wjx6bt.cc/v230/play/url\n"
        "rooms: []\n",
        encoding="utf-8",
    )
    raw = json.dumps(
        {
            "format": "zhibo.fs1-auth",
            "version": 1,
            "site_url": "https://www.fszb148.com",
            "api_url": API_URL,
            "authorization": "token-value",
        }
    )
    monkeypatch.setattr(fs1_plugin, "discover_play_api_url", lambda *args, **kwargs: "")

    applied = update_from_text(raw, config_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert applied["play_api_url"] == fs1_plugin.DEFAULT_PLAY_API_URL
    assert data["config"]["play_api_url"] == fs1_plugin.DEFAULT_PLAY_API_URL


def test_update_rooms_config_preserves_rooms_and_applies_updates(tmp_path: Path):
    config_path = tmp_path / "rooms.yaml"
    config_path.write_text(
        f"""
config:
  site_url: https://old.example.com
  api_url: {API_URL}
rooms:
- name: test
  room_id: "380348943"
  sport_id: "1"
""",
        encoding="utf-8",
    )

    applied = update_rooms_config(
        config_path,
        {
            "site_url": "https://fszb999.com",
            "api_url": API_URL,
            "token": "token-value",
        },
        discover_play_api=False,
    )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert applied == {
        "site_url": "https://fszb999.com",
        "api_url": API_URL,
        "token": "token-value",
    }
    assert data["config"]["site_url"] == "https://fszb999.com"
    assert data["config"]["api_url"] == API_URL
    assert data["config"]["token"] == "token-value"
    assert data["rooms"][0]["room_id"] == "380348943"


def test_extract_script_urls_resolves_relative_paths():
    html = """<script src="static/js/app.js"></script><script src="/assets/vendor.js"></script>"""

    assert _extract_script_urls(html, "https://www.fszb130.com") == [
        "https://www.fszb130.com/static/js/app.js",
        "https://www.fszb130.com/assets/vendor.js",
    ]


def test_extract_play_api_url_from_openim_base():
    js = 'n="https://openim-php-api.x3t9p9f5h0l3.cc/";getRoomPlayUrl:e=>i["c"]("/v230/play/url",e,"baseImApi")'

    assert _extract_play_api_url(js) == "https://openim-php-api.x3t9p9f5h0l3.cc/v230/play/url"


def test_extract_fszb_base_im_api_ignores_other_brand_mappings():
    js = (
        '"rrty-theme"==i?1==e?(a="https://apc.other.cc/",'
        'h="https://openim-php-api.qaek4a2wjx6bt.cc/"):"fszb-theme"==i?'
        '1==e?(a="https://apc.xzood6veuybwkr.com",'
        'h="https://openim-php-api.q2n1w3g2y5v0w4l1.cc",'
        's="wss://example.invalid",c="https://example.invalid")'
    )

    assert _extract_fszb_base_im_api(js) == (
        "https://openim-php-api.q2n1w3g2y5v0w4l1.cc"
    )


def test_fs_public_asset_allowlist_accepts_fixed_cdn_only():
    page_url = "https://www.fszb148.com/index.html"

    assert _is_trusted_fs_asset_url(
        "https://rsfs.ypzzib.com/static-html/pc/static39/js/app.js", page_url
    )
    assert not _is_trusted_fs_asset_url("https://cdn.example.invalid/app.js", page_url)


def test_update_stream_libraries_runs_pip_upgrade(monkeypatch):
    captured = {}

    class Result:
        returncode = 0

    def fake_run(cmd):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr("zhibo.plugins.fs1_plugin.subprocess.run", fake_run)
    monkeypatch.setattr("zhibo.plugins.fs1_plugin.sys.executable", "python-test")

    assert update_stream_libraries() == 0
    assert captured["cmd"] == [
        "python-test",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--constraint",
        str(fs1_plugin.SCRIPT_DIR / "requirements.txt"),
        "streamlink",
        "streamget",
    ]


def test_fs_proxy_is_always_direct(monkeypatch):
    monkeypatch.setenv("ZHIBO_PROXY", "http://127.0.0.1:7890")

    assert _configured_proxy({"proxy_url": "http://127.0.0.1:7890"}) is None
    assert _configured_proxy({"fs_proxy_url": "http://127.0.0.1:7891"}) is None


def test_fs1_enables_tls_verification_by_default(monkeypatch):
    monkeypatch.setattr("zhibo.plugins.fs1_plugin._load_fs_config", lambda: {})

    verify = Fs1Plugin()._verify_ssl
    assert verify is not False
    if fs1_plugin.truststore is not None:
        assert isinstance(verify, ssl.SSLContext)
    assert _as_bool("false") is False


def test_fs1_api_endpoint_is_fixed_and_query_free():
    assert is_trusted_api_url(API_URL)
    assert not is_trusted_api_url(API_URL + "?token=leak")
    assert not is_trusted_api_url("https://attacker.invalid/v1/room")
    assert not is_trusted_api_url(API_URL.replace("/v1/room", "/v2/room"))


def test_fs1_runtime_ignores_untrusted_api_and_tls_downgrade(monkeypatch):
    monkeypatch.setattr(
        "zhibo.plugins.fs1_plugin._load_fs_config",
        lambda: {"api_url": "https://attacker.invalid/v1/room", "verify_ssl": False},
    )

    plugin = Fs1Plugin()

    assert plugin._api_url == fs1_plugin.DEFAULT_API_URL
    assert plugin._verify_ssl is not False
    if fs1_plugin.truststore is not None:
        assert isinstance(plugin._verify_ssl, ssl.SSLContext)


def test_play_api_endpoint_requires_the_trusted_https_namespace():
    trusted = "https://openim-php-api.x3t9p9f5h0l3.cc/v230/play/url"

    assert is_trusted_play_api_url(trusted)
    assert not is_trusted_play_api_url("http://openim-php-api.x3t9p9f5h0l3.cc/v230/play/url")
    assert not is_trusted_play_api_url("https://attacker.invalid/v230/play/url")
    assert not is_trusted_play_api_url(trusted + "?token=leak")
    assert _extract_play_api_url("const api='https://attacker.invalid/v230/play/url'") == ""


def test_play_api_headers_drop_room_cookie_and_keep_frontend_common_headers():
    headers = {
        "authorization": "token-value",
        "api-version": "8",
        "cookie": "tracking=demo",
        "device": "3",
        "device2": "3",
        "version": "1.9.7",
        "platform": "fszb",
    }

    assert _play_api_headers(headers) == {
        "authorization": "token-value",
        "api-version": "8",
        "device": "3",
        "device2": "3",
        "platform": "fszb",
        "version": "1.9.7",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    }


def test_room_headers_match_base_api_and_do_not_send_page_cookie():
    headers = _build_headers(
        {
            "site_url": "https://www.fszb148.com",
            "token": "token-value",
            "cookie": "sid=page-cookie",
            "api_version": "8",
            "version": "1.9.7",
            "imei": "imei-value",
            "dun_imei": "dun-value",
        }
    )

    assert headers["Accept"] == "application/json, text/plain, */*"
    assert headers["device"] == "3"
    assert headers["version"] == "1.9.7"
    assert headers["authorization"] == "token-value"
    assert "cookie" not in {key.casefold() for key in headers}
    assert "platform" not in headers
    assert "device2" not in headers


def test_fetch_play_url_rejects_untrusted_endpoint_before_request():
    class NoRequestClient:
        async def post(self, *args, **kwargs):
            pytest.fail("an untrusted endpoint must not receive an authorized request")

    with pytest.raises(ValueError, match="untrusted"):
        asyncio.run(
            fetch_play_url(
                NoRequestClient(),
                "https://attacker.invalid/v230/play/url",
                "room",
                "quality",
            )
        )


def test_discovery_uses_verified_tls_and_same_origin_scripts_only(monkeypatch):
    page_url = "https://www.fszb130.com/"
    script_url = "https://www.fszb130.com/assets/app.js"
    endpoint = "https://openim-php-api.x3t9p9f5h0l3.cc/v230/play/url"
    requested: list[str] = []
    init_kwargs: dict = {}

    class Response:
        def __init__(self, url: str, text: str):
            self.url = url
            self.text = text

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            init_kwargs.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url: str):
            requested.append(url)
            if url == page_url:
                return Response(
                    page_url,
                    '<script src="https://attacker.invalid/payload.js"></script>'
                    '<script src="/assets/app.js"></script>',
                )
            if url == script_url:
                return Response(script_url, f'const api = "{endpoint}";')
            pytest.fail(f"unexpected request: {url}")

    monkeypatch.setattr(fs1_plugin.httpx, "Client", Client)

    assert discover_play_api_url(page_url) == endpoint
    assert requested == [page_url, script_url]
    assert init_kwargs.get("verify", True) is not False
    if fs1_plugin.truststore is not None:
        assert isinstance(init_kwargs["verify"], ssl.SSLContext)


def test_discovery_rejects_same_origin_script_redirected_elsewhere(monkeypatch):
    page_url = "https://www.fszb130.com/"
    script_url = "https://www.fszb130.com/assets/app.js"

    class Response:
        def __init__(self, url: str, text: str):
            self.url = url
            self.text = text

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url: str):
            if url == page_url:
                return Response(page_url, '<script src="/assets/app.js"></script>')
            if url == script_url:
                return Response(
                    "https://attacker.invalid/payload.js",
                    'const api = "https://openim-php-api.x3t9p9f5h0l3.cc/v230/play/url";',
                )
            pytest.fail(f"unexpected request: {url}")

    monkeypatch.setattr(fs1_plugin.httpx, "Client", Client)

    assert discover_play_api_url(page_url) == ""


def test_config_update_does_not_persist_automatically_discovered_endpoint(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "rooms.yaml"
    config_path.write_text("config: {}\nrooms: []\n", encoding="utf-8")

    def unexpected_discovery(*args, **kwargs):
        pytest.fail("automatic discovery must not run while writing credentials")

    monkeypatch.setattr(fs1_plugin, "discover_play_api_url", unexpected_discovery)

    update_rooms_config(
        config_path,
        {
            "site_url": "https://www.fszb130.com",
            "api_url": API_URL,
            "token": "token-value",
        },
    )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "play_api_url" not in data["config"]


def test_config_update_keeps_original_file_if_atomic_replace_fails(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "rooms.yaml"
    original = "config:\n  site_url: https://www.fszb130.com\nrooms: []\n"
    config_path.write_text(original, encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(fs1_plugin.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        update_rooms_config(
            config_path,
            {"api_url": API_URL},
            discover_play_api=False,
        )

    assert config_path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".rooms.yaml.*.tmp"))


def test_config_update_initializes_a_missing_private_destination(tmp_path: Path):
    config_path = tmp_path / "private" / "fs1" / "rooms.yaml"

    applied = update_rooms_config(
        config_path,
        {"api_url": API_URL},
        discover_play_api=False,
    )

    assert applied["api_url"] == API_URL
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["config"]["api_url"] == API_URL


def test_cli_config_update_redacts_entire_token_value(monkeypatch, capsys):
    secret = "token-value-that-must-not-appear-in-output"
    monkeypatch.setattr(
        fs1_plugin,
        "update_from_text",
        lambda raw, path: {"api_url": API_URL, "token": secret},
    )

    assert _apply_curl_update("curl ignored") == 0
    output = capsys.readouterr().out
    assert secret not in output
    assert secret[:8] not in output
    assert "token: ***" in output
