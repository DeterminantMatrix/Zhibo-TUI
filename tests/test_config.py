"""CSV-only follower configuration tests."""
import tempfile
from pathlib import Path

import pytest

from zhibo import config
from zhibo.models import AppConfig, Follower


HEADER = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"


def write_followers_csv(rows: str, *, encoding: str = "utf-8") -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding=encoding, newline="") as f:
        f.write(HEADER + rows)
    return Path(f.name)


def test_load_config_valid():
    path = write_followers_csv(
        "true,测试主播,游戏,streamget,,douyin,https://live.douyin.com/123,HD,\n"
    )
    settings = path.with_name("settings.csv")
    settings.write_text("key,value\npoll_interval,30\n", encoding="utf-8")
    try:
        cfg = config.ConfigManager(path).load_config()
        f1 = cfg.followers[0]
        assert cfg.poll_interval == 30
        assert f1.name == "测试主播"
        assert f1.plugin == "streamget"
        assert f1.platform == "douyin"
        assert f1.tags == ["游戏"]
        assert f1.quality == "HD"
    finally:
        path.unlink(missing_ok=True)
        settings.unlink(missing_ok=True)


def test_default_values():
    path = write_followers_csv("true,主播,,streamget,,douyin,https://live.douyin.com/123,,\n")
    try:
        cfg = config.ConfigManager(path).load_config()
        f1 = cfg.followers[0]
        assert f1.quality == "best"
        assert f1.tags == ["未分类"]
        assert f1.enabled is True
        assert f1.fallback_plugins == []
        assert cfg.failure_backoff_after == 3
    finally:
        path.unlink(missing_ok=True)


def test_load_config_normalizes_common_fields():
    path = write_followers_csv(
        "false, 主播 ,POE, StreamGet , StreamLink , Twitch , https://www.twitch.tv/example , 720p60 ,\n"
    )
    settings = path.with_name("settings.csv")
    settings.write_text("key,value\npoll_interval,30\nmax_concurrent_checks,3\nnotifications_enabled,false\n", encoding="utf-8")
    try:
        cfg = config.ConfigManager(path).load_config()
        f1 = cfg.followers[0]
        assert (cfg.poll_interval, cfg.max_concurrent_checks, cfg.notifications_enabled) == (30, 3, False)
        assert (f1.name, f1.plugin, f1.platform, f1.url) == ("主播", "streamget", "twitch", "https://www.twitch.tv/example")
        assert f1.quality == "720p60"
        assert f1.fallback_plugins == ["streamlink"]
    finally:
        path.unlink(missing_ok=True)
        settings.unlink(missing_ok=True)


def test_load_config_bounds_hand_edited_unsafe_monitoring_values(tmp_path):
    path = tmp_path / "followers.csv"
    path.write_text(
        HEADER + "true,主播,,streamget,,douyin,https://live.douyin.com/123,,\n",
        encoding="utf-8",
    )
    (tmp_path / "settings.csv").write_text(
        "key,value\n"
        "poll_interval,1\n"
        "max_concurrent_checks,999999\n"
        "failure_backoff_after,999999\n"
        "failure_backoff_polls,0\n"
        "notifications_enabled,not-a-bool\n",
        encoding="utf-8",
    )

    manager = config.ConfigManager(path)
    cfg = manager.load_config()

    assert (
        cfg.poll_interval,
        cfg.max_concurrent_checks,
        cfg.failure_backoff_after,
        cfg.failure_backoff_polls,
        cfg.notifications_enabled,
    ) == (5, 16, 20, 1, True)
    # The settings screen must remain usable to repair the CSV permanently.
    assert manager.read_monitoring_settings()["max_concurrent_checks"] == 16


def test_load_config_csv_table_format_and_platform_proxies():
    path = write_followers_csv("true,主播,ASMR|POE,streamlink,streamget,fs1,740426526,best,101\n")
    settings = path.with_name("settings.csv")
    settings.write_text(
        "key,value\npoll_interval,90\nnotifications_enabled,false\nplatform_proxy.twitch,7897\nplatform_proxy.youtube,direct\n",
        encoding="utf-8",
    )
    try:
        cfg = config.ConfigManager(path).load_config()
        assert cfg.poll_interval == 90
        assert cfg.platform_proxies == {"twitch": "7897", "youtube": "direct"}
        assert cfg.followers[0].extra == {"sport_id": "101"}
    finally:
        path.unlink(missing_ok=True)
        settings.unlink(missing_ok=True)


def test_load_config_csv_accepts_gb18030_encoding():
    path = write_followers_csv("true,驴哥说球,体育,fs1,,fs1,740426526,best,101\n", encoding="gb18030")
    try:
        assert config.ConfigManager(path).load_config().followers[0].name == "驴哥说球"
    finally:
        path.unlink(missing_ok=True)


def test_rejects_non_csv_follower_config(tmp_path):
    path = tmp_path / "followers.yaml"
    path.write_text("followers: []\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="只支持"):
        config.ConfigManager(path).load_config()


def test_missing_required_field_skips():
    path = write_followers_csv("true,主播,,streamget,,,,,\n")
    try:
        with pytest.raises(SystemExit):
            config.ConfigManager(path).load_config()
    finally:
        path.unlink(missing_ok=True)


def test_extract_tags():
    cfg = AppConfig(followers=[
        Follower(name="1", plugin="fs1", url="1", tags=["游戏", "FPS"]),
        Follower(name="2", plugin="fs1", url="2", tags=["体育"]),
        Follower(name="3", plugin="fs1", url="3", tags=["游戏", "MOBA"]),
    ])
    assert set(config.extract_tags(cfg)) == {"游戏", "体育", "FPS", "MOBA"}


def test_csv_round_trips_plugin_extra(tmp_path):
    path = tmp_path / "followers.csv"
    follower = Follower(
        name="自定义插件主播",
        plugin="custom",
        url="https://example.com/live",
        extra={"sport_id": "101", "room_type": "vip", "nested": {"enabled": True}},
    )

    manager = config.ConfigManager(path)
    manager.write_followers_csv([follower])

    loaded = manager.load_config().followers[0]
    assert loaded.extra == follower.extra


def test_migrate_yaml_to_csv_preserves_followers_settings_and_extra(tmp_path):
    source = tmp_path / "followers.yaml"
    target = tmp_path / "followers.csv"
    source.write_text(
        """
poll_interval: 30
notifications_enabled: false
followers:
  - name: 旧配置主播
    plugin: custom
    platform: example
    url: https://example.com/live
    tags: [测试]
    extra:
      room_type: vip
      sport_id: "99"
""".strip(),
        encoding="utf-8",
    )

    assert config.migrate_yaml_to_csv(source, target) == target
    loaded = config.ConfigManager(target).load_config()

    assert loaded.poll_interval == 30
    assert loaded.notifications_enabled is False
    assert loaded.followers[0].extra == {"room_type": "vip", "sport_id": "99"}
    with pytest.raises(FileExistsError):
        config.migrate_yaml_to_csv(source, target)
