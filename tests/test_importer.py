import asyncio
import tempfile
from pathlib import Path

import pytest
import yaml

import config
import importer
from importer import build_follower_from_url, detect_platform, fallback_name
from models import Follower


def test_detect_platform_known_urls():
    assert detect_platform("https://fszb148.com/broadcast/details?room_id=740426526&sport_id=101") == (
        "fs1",
        "fs1",
        [],
    )
    assert detect_platform("https://www.fszb130.com/broadcast/details?room_id=740426526&sport_id=101") == (
        "fs1",
        "fs1",
        [],
    )
    assert detect_platform("https://www.huya.com/520819") == ("huya", "streamlink", ["streamget"])
    assert detect_platform("https://www.douyu.com/12020475") == ("douyu", "streamget", ["streamlink"])
    assert detect_platform("https://live.bilibili.com/7777") == ("bilibili", "streamlink", ["streamget"])
    assert detect_platform("https://live.douyin.com/123") == ("douyin", "streamlink", ["streamget"])
    assert detect_platform("https://www.xiaohongshu.com/user/profile/example") == ("rednote", "streamget", [])
    assert detect_platform("https://xhslink.com/a/example") == ("rednote", "streamget", [])
    assert detect_platform("https://www.twitch.tv/example") == ("twitch", "streamlink", ["streamget"])


def test_fallback_name_uses_last_path_segment():
    assert fallback_name("https://www.twitch.tv/example") == "example"
    assert fallback_name("https://www.huya.com/520819") == "520819"


def test_build_follower_from_url_without_scheme(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)

    follower = asyncio.run(build_follower_from_url("www.twitch.tv/example", "POE"))

    assert follower.name
    assert follower.plugin == "streamlink"
    assert follower.fallback_plugins == ["streamget"]
    assert follower.platform == "twitch"
    assert follower.url == "https://www.twitch.tv/example"
    assert follower.tags == ["POE"]
    assert follower.enabled is True


def test_build_follower_from_url_defaults_blank_tag(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)

    follower = asyncio.run(build_follower_from_url("www.twitch.tv/example", " "))

    assert follower.tags == ["未分类"]


def test_build_follower_from_fs_url(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)

    follower = asyncio.run(
        build_follower_from_url(
            "https://fszb148.com/broadcast/details?room_id=740426526&sport_id=101",
            "LOL",
        )
    )

    assert follower.name == "740426526"
    assert follower.plugin == "fs1"
    assert follower.fallback_plugins == []
    assert follower.platform == "fs1"
    assert follower.url == "740426526"
    assert follower.extra == {"sport_id": "101"}
    assert follower.tags == ["LOL"]


def test_append_follower_writes_followers_yaml():
    yaml_content = """
poll_interval: 30
followers: []
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name
    cm = config.ConfigManager(fpath)
    try:
        cm.load_config()
    except SystemExit:
        pass

    try:
        cm.append_follower(
            Follower(
                name="example",
                plugin="streamlink",
                platform="twitch",
                url="https://www.twitch.tv/example",
            )
        )
        data = yaml.safe_load(Path(fpath).read_text(encoding="utf-8"))
        assert data["followers"][0]["name"] == "example"
        assert data["followers"][0]["platform"] == "twitch"
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_append_follower_writes_followers_csv():
    csv_content = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(csv_content)
    fpath = f.name

    cm = config.ConfigManager(fpath)
    try:
        with pytest.raises(SystemExit):
            cm.load_config()
        cm.append_follower(
            Follower(
                enabled=True,
                name="fs",
                tags=["LOL"],
                plugin="fs1",
                fallback_plugins=[],
                platform="fs1",
                url="740426526",
                quality="best",
                extra={"sport_id": "101"},
            )
        )
        data = Path(fpath).read_text(encoding="utf-8-sig")
        assert "fs,LOL,fs1" in data
        assert "740426526" in data
        assert "101" in data
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_append_follower_rejects_duplicate_room():
    csv_content = (
        "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"
        "true,existing,LOL,streamlink,,twitch,https://www.twitch.tv/example,best,\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(csv_content)
    fpath = f.name

    try:
        cm = config.ConfigManager(fpath)
        with pytest.raises(ValueError, match="已关注"):
            cm.append_follower(
                Follower(
                    name="duplicate",
                    plugin="streamlink",
                    platform="twitch",
                    url="https://www.twitch.tv/example/",
                )
            )
        data = Path(fpath).read_text(encoding="utf-8")
        assert data.count("existing") == 1
        assert "duplicate" not in data
    finally:
        Path(fpath).unlink(missing_ok=True)
