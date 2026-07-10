"""测试配置加载"""
import tempfile
from pathlib import Path
import pytest
import config
from models import Follower, AppConfig

def test_load_config_valid():
    yaml_content = """
poll_interval: 30
followers:
  - name: "测试主播"
    plugin: streamget
    platform: douyin
    url: "https://live.douyin.com/123"
    quality: HD
    tags: ["游戏"]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

    try:
        cfg = config.ConfigManager(fpath).load_config()
        assert cfg.poll_interval == 30
        assert len(cfg.followers) == 1
        f1 = cfg.followers[0]
        assert f1.name == "测试主播"
        assert f1.plugin == "streamget"
        assert f1.platform == "douyin"
        assert f1.tags == ["游戏"]
        assert f1.quality == "HD"
        assert f1.extra == {}
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_default_values():
    yaml_content = """
followers:
  - name: "主播"
    plugin: streamget
    platform: douyin
    url: "https://live.douyin.com/123"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

    try:
        cfg = config.ConfigManager(fpath).load_config()
        f1 = cfg.followers[0]
        assert f1.quality == "best"
        assert f1.tags == ["未分类"]
        assert f1.extra == {}
        assert f1.enabled is True
        assert f1.fallback_plugins == []
        assert cfg.failure_backoff_after == 3
        assert cfg.failure_backoff_polls == 2
        assert cfg.notifications_enabled is True
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_load_config_normalizes_common_fields():
    yaml_content = """
poll_interval: "30"
max_concurrent_checks: "3"
notifications_enabled: "false"
followers:
  - name: " 主播 "
    plugin: " StreamGet "
    platform: " Twitch "
    url: " https://www.twitch.tv/example "
    quality: " 720p60 "
    tags: "POE"
    enabled: "false"
    fallback_plugins: [" StreamLink "]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

    try:
        cfg = config.ConfigManager(fpath).load_config()
        f1 = cfg.followers[0]
        assert cfg.poll_interval == 30
        assert cfg.max_concurrent_checks == 3
        assert cfg.notifications_enabled is False
        assert f1.name == "主播"
        assert f1.plugin == "streamget"
        assert f1.platform == "twitch"
        assert f1.url == "https://www.twitch.tv/example"
        assert f1.quality == "720p60"
        assert f1.tags == ["POE"]
        assert f1.enabled is False
        assert f1.fallback_plugins == ["streamlink"]
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_load_config_csv_table_format():
    csv_content = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\ntrue,主播,ASMR|POE,streamlink,streamget,fs1,740426526,best,101\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(csv_content)
    fpath = f.name
    settings_path = Path(fpath).with_name("settings.csv")
    settings_path.write_text(
        "key,value\npoll_interval,90\nnotifications_enabled,false\n",
        encoding="utf-8",
    )

    try:
        cfg = config.ConfigManager(fpath).load_config()
        f1 = cfg.followers[0]
        assert cfg.poll_interval == 90
        assert cfg.notifications_enabled is False
        assert f1.tags == ["ASMR", "POE"]
        assert f1.fallback_plugins == ["streamget"]
        assert f1.extra == {"sport_id": "101"}
    finally:
        Path(fpath).unlink(missing_ok=True)
        settings_path.unlink(missing_ok=True)


def test_load_config_csv_accepts_gb18030_encoding():
    csv_content = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\ntrue,驴哥说球,体育,fs1,,fs1,740426526,best,101\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="gb18030") as f:
        f.write(csv_content)
    fpath = f.name

    try:
        cfg = config.ConfigManager(fpath).load_config()
        f1 = cfg.followers[0]
        assert f1.name == "驴哥说球"
        assert f1.plugin == "fs1"
        assert f1.extra == {"sport_id": "101"}
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_missing_required_field_skips():
    yaml_content = """
followers:
  - name: "主播"
    plugin: streamget
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

    try:
        with pytest.raises(SystemExit):
            config.ConfigManager(fpath).load_config()
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_extract_tags():
    cfg = AppConfig(
        poll_interval=60,
        max_concurrent_checks=8,
        failure_backoff_after=3,
        failure_backoff_polls=2,
        notifications_enabled=True,
        followers=[
            Follower(name="1", plugin="fs1", url="1", tags=["游戏", "FPS"]),
            Follower(name="2", plugin="fs1", url="2", tags=["体育"]),
            Follower(name="3", plugin="fs1", url="3", tags=["游戏", "MOBA"]),
        ]
    )
    tags = config.extract_tags(cfg)
    assert "游戏" in tags
    assert "体育" in tags
    assert "FPS" in tags
    assert "MOBA" in tags
    assert len(tags) == 4
