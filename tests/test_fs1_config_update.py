from pathlib import Path

import yaml

from plugins.fs1_plugin import (
    Fs1Plugin,
    _as_bool,
    _configured_proxy,
    _extract_play_api_url,
    _extract_script_urls,
    parse_curl,
    update_rooms_config,
    update_stream_libraries,
)


def test_parse_curl_extracts_fs1_config_fields():
    raw = r"""curl 'https://apc.example.com/v1/room?room_id=380348943&sport_id=1' \
  -H 'Origin: https://fszb999.com' \
  -H 'Referer: https://fszb999.com/' \
  -H 'api-version: 9' \
  -H 'authorization: token-value' \
  -H 'imei: imei-value' \
  -H 'dun-imei: dun-value' \
  -H 'User-Agent: Test UA'"""

    assert parse_curl(raw) == {
        "api_url": "https://apc.example.com/v1/room",
        "site_url": "https://fszb999.com",
        "api_version": "9",
        "token": "token-value",
        "imei": "imei-value",
        "dun_imei": "dun-value",
        "user_agent": "Test UA",
    }


def test_update_rooms_config_preserves_rooms_and_applies_updates(tmp_path: Path):
    config_path = tmp_path / "rooms.yaml"
    config_path.write_text(
        """
config:
  site_url: https://old.example.com
  api_url: https://old-api.example.com/v1/room
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
            "api_url": "https://apc.example.com/v1/room",
            "token": "token-value",
        },
        discover_play_api=False,
    )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert applied == {
        "site_url": "https://fszb999.com",
        "api_url": "https://apc.example.com/v1/room",
        "token": "token-value",
    }
    assert data["config"]["site_url"] == "https://fszb999.com"
    assert data["config"]["api_url"] == "https://apc.example.com/v1/room"
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


def test_update_stream_libraries_runs_pip_upgrade(monkeypatch):
    captured = {}

    class Result:
        returncode = 0

    def fake_run(cmd):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr("plugins.fs1_plugin.subprocess.run", fake_run)
    monkeypatch.setattr("plugins.fs1_plugin.sys.executable", "python-test")

    assert update_stream_libraries() == 0
    assert captured["cmd"] == [
        "python-test",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "streamlink",
        "streamget",
    ]


def test_fs_proxy_is_always_direct(monkeypatch):
    monkeypatch.setenv("ZHIBO_PROXY", "http://127.0.0.1:7890")

    assert _configured_proxy({"proxy_url": "http://127.0.0.1:7890"}) is None
    assert _configured_proxy({"fs_proxy_url": "http://127.0.0.1:7891"}) is None


def test_fs1_enables_tls_verification_by_default(monkeypatch):
    monkeypatch.setattr("plugins.fs1_plugin._load_fs_config", lambda: {})

    assert Fs1Plugin()._verify_ssl is True
    assert _as_bool("false") is False
