"""Safety and persistence tests for the follower editing data layer."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from zhibo import config
from zhibo.config import (
    ConfigLockTimeoutError,
    ConfigManager,
    FollowerValidationError,
    MonitoringSettingsValidationError,
    follower_key,
    follower_to_edit_payload,
    preview_follower_edit,
    preview_monitoring_settings,
    validate_follower_edit,
    validate_monitoring_settings,
)
from zhibo.models import Follower


def _follower(name: str = "主播", url: str = "https://example.com/live", **kwargs) -> Follower:
    return Follower(name=name, plugin="streamget", platform="example", url=url, **kwargs)


def test_validate_follower_edit_normalizes_all_edit_fields():
    follower = validate_follower_edit(
        {
            "enabled": "false",
            "name": "  测试主播  ",
            "tags": " 游戏，FPS|游戏 ",
            "plugin": " StreamLink ",
            "fallback_plugins": " StreamGet | streamlink ",
            "platform": " Twitch ",
            "url": "twitch.tv/example",
            "quality": " 720p60 ",
            "sport_id": 101,
            "extra": json.dumps({"room_type": "vip", "nested": {"enabled": True}}),
        }
    )

    assert follower.enabled is False
    assert follower.name == "测试主播"
    assert follower.tags == ["游戏", "FPS"]
    assert follower.plugin == "streamlink"
    assert follower.fallback_plugins == ["streamget"]
    assert follower.platform == "twitch"
    assert follower.url == "https://twitch.tv/example"
    assert follower.quality == "720p60"
    assert follower.extra == {
        "room_type": "vip",
        "nested": {"enabled": True},
        "sport_id": "101",
    }


@pytest.mark.parametrize(
    "values",
    [
        {"token": "must-not-be-written"},
        {"auth": "must-not-be-written"},
        {"sign": "must-not-be-written"},
        {"headers": {"X-Unlabelled": "Bearer must-not-be-written"}},
        {"headers": {"Authorization": "must-not-be-written"}},
        {"room_type": "https://example.com/live?signature=must-not-be-written"},
    ],
)
def test_validate_follower_edit_rejects_sensitive_extra_without_echoing_value(values):
    secret = "must-not-be-written"

    with pytest.raises(FollowerValidationError) as exc_info:
        validate_follower_edit(
            {
                "name": "主播",
                "plugin": "streamget",
                "platform": "example",
                "url": "https://example.com/live",
                "extra": values,
            }
        )

    assert secret not in str(exc_info.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/live?token=must-not-be-written",
        "https://example.com/live?auth=must-not-be-written",
        "https://example.com/live?sign=must-not-be-written",
        "https://user:password@example.com/live",
        "ftp://example.com/live",
    ],
)
def test_validate_follower_edit_rejects_credential_or_unsafe_urls(url):
    with pytest.raises(FollowerValidationError):
        validate_follower_edit(
            {
                "name": "主播",
                "plugin": "streamget",
                "platform": "example",
                "url": url,
            }
        )


def test_follower_to_edit_payload_masks_legacy_sensitive_extra_and_url():
    secret = "must-not-be-shown"
    payload = follower_to_edit_payload(
        _follower(
            url=f"https://example.com/live?token={secret}",
            extra={"token": secret, "nested": {"Cookie": secret}, "room_type": "vip"},
        )
    )

    assert secret not in repr(payload)
    assert payload["url"].endswith("token=***")
    assert payload["extra"]["token"] == "***"
    assert payload["extra"]["nested"]["Cookie"] == "***"


def test_legacy_append_api_cannot_bypass_safe_follower_validation(tmp_path):
    manager = ConfigManager(tmp_path / "followers.csv")

    with pytest.raises(FollowerValidationError):
        manager.append_follower(
            _follower(
                url="https://example.com/live?auth=must-not-be-written",
                extra={"sign": "must-not-be-written"},
            )
        )

    assert not manager.config_path.exists()


def test_public_table_writer_cannot_bypass_safe_follower_validation(tmp_path):
    manager = ConfigManager(tmp_path / "followers.csv")

    with pytest.raises(FollowerValidationError):
        manager.write_followers_csv(
            [_follower(url="https://example.com/live?sign=must-not-be-written")]
        )

    assert not manager.config_path.exists()


def test_preview_follower_edit_returns_redacted_field_diff():
    current = _follower(tags=["旧标签"], quality="best", extra={"sport_id": "1", "room_type": "normal"})

    preview = preview_follower_edit(
        current,
        {"quality": "720p", "tags": ["新标签"], "sport_id": "2"},
        existing_followers=[current],
        editing_index=0,
    )

    assert preview.has_changes is True
    assert preview.follower.quality == "720p"
    assert preview.follower.extra == {"room_type": "normal", "sport_id": "2"}
    assert preview.changes["quality"] == ("best", "720p")
    assert preview.changes["tags"] == (["旧标签"], ["新标签"])
    assert preview.changes["sport_id"] == ("1", "2")


def test_follower_edit_duplicate_check_normalizes_url_identity_and_tracking_parameters():
    existing = _follower(
        "existing",
        "HTTPS://Example.COM.:443/live/room/?v=42&utm_source=newsletter",
    )

    with pytest.raises(FollowerValidationError, match="同一直播间"):
        validate_follower_edit(
            {
                "name": "candidate",
                "plugin": "streamget",
                "platform": "example",
                "url": "https://example.com/live/room?v=42&fbclid=click#section",
            },
            existing_followers=[existing],
        )


def test_follower_key_keeps_fs1_sport_context_for_the_same_opaque_room_id():
    first = Follower(
        name="sport one",
        plugin="fs1",
        platform="fs1",
        url="740426526",
        extra={"sport_id": "101"},
    )
    second = Follower(
        name="sport two",
        plugin="fs1",
        platform="fs1",
        url="740426526",
        extra={"sport_id": "202"},
    )

    assert follower_key(first)[:2] == follower_key(second)[:2]
    assert follower_key(first) != follower_key(second)


def test_config_manager_read_followers_is_nonfatal_for_missing_or_empty_files(tmp_path):
    manager = ConfigManager(tmp_path / "followers.csv")
    assert manager.read_followers() == []

    manager.config_path.write_text(
        "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id,extra\n",
        encoding="utf-8",
    )
    assert manager.read_followers() == []


def test_config_manager_read_followers_reports_invalid_rows_without_system_exit(tmp_path):
    path = tmp_path / "followers.csv"
    path.write_text(
        "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id,extra\n"
        "true,主播,,streamget,,example,https://example.com/live,best,,not-json\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="第 2 行"):
        ConfigManager(path).read_followers()


def test_write_lock_times_out_while_another_process_holds_the_same_config(tmp_path):
    path = tmp_path / "followers.csv"
    holder = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from zhibo.config import _config_write_lock\n"
        "with _config_write_lock(Path(sys.argv[1])):\n"
        "    print('locked', flush=True)\n"
        "    time.sleep(0.5)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", holder, str(path)],
        cwd=Path(__file__).parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(ConfigLockTimeoutError):
            with config._config_write_lock(path, timeout=0.05):
                pytest.fail("second process unexpectedly acquired the config lock")
    finally:
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 0, f"lock holder failed: {stdout}\n{stderr}"


def test_concurrent_validated_appends_preserve_both_rows(tmp_path):
    path = tmp_path / "followers.csv"
    worker = (
        "import sys\n"
        "from pathlib import Path\n"
        "from zhibo.config import ConfigManager\n"
        "from zhibo.models import Follower\n"
        "path = Path(sys.argv[1])\n"
        "name = sys.argv[2]\n"
        "ConfigManager(path).append_validated_follower(Follower(\n"
        "    name=name, plugin='streamget', platform='example',\n"
        "    url='https://example.test/' + name, tags=['并发']))\n"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, str(path), name],
            cwd=Path(__file__).parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in ("first", "second")
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, f"append worker failed: {stdout}\n{stderr}"

    assert {follower.name for follower in ConfigManager(path).read_followers()} == {"first", "second"}


def test_update_follower_validates_duplicates_then_writes_atomically(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [
            _follower("甲", "https://example.com/a", tags=["A"]),
            _follower("乙", "https://example.com/b", tags=["B"]),
        ]
    )

    preview = manager.update_follower(0, {"quality": "720p", "tags": "游戏|FPS"})
    saved = manager.read_followers()
    assert preview.changes["quality"] == ("best", "720p")
    assert saved[0].quality == "720p"
    assert saved[0].tags == ["游戏", "FPS"]
    original_bytes = path.read_bytes()

    with pytest.raises(FollowerValidationError, match="同一直播间"):
        manager.update_follower(1, {"url": "https://example.com/a"})

    assert path.read_bytes() == original_bytes


def test_update_follower_keeps_original_csv_when_atomic_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv([_follower()])
    original = path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(config.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        manager.update_follower(0, {"quality": "720p"})

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".followers.csv.*.tmp"))


def test_atomic_config_write_fsyncs_before_replacement(tmp_path, monkeypatch):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    fsync_calls = []

    monkeypatch.setattr(config.os, "fsync", lambda descriptor: fsync_calls.append(descriptor))

    manager.write_followers_csv([_follower()])

    assert fsync_calls
    assert path.exists()


def test_update_follower_rejects_a_target_changed_after_preview(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    original = _follower("甲", "https://example.com/a")
    manager.write_followers_csv([original])

    # Simulate a manual external replacement while the confirmation dialog is
    # open.  The same row index must not be silently overwritten.
    manager.write_followers_csv([_follower("乙", "https://example.com/b")])

    with pytest.raises(ValueError, match="预览后变化"):
        manager.update_follower(0, {"quality": "720p"}, expected_key=follower_key(original))

    assert manager.read_followers()[0].name == "乙"


def test_update_follower_rejects_same_room_content_changed_after_preview(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    original = _follower("甲", "https://example.com/a", quality="best")
    manager.write_followers_csv([original])

    # The room identity is unchanged, but an external editor changed another
    # field after the UI rendered its confirmation diff.
    manager.write_followers_csv([_follower("甲", "https://example.com/a", quality="1080p")])

    with pytest.raises(ValueError, match="内容已在预览后变化"):
        manager.update_follower(
            0,
            {"tags": "游戏"},
            expected_key=follower_key(original),
            expected_follower=original,
        )

    assert manager.read_followers()[0].quality == "1080p"


def test_remove_follower_verifies_target_and_writes_remaining_rows(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    first = _follower("甲", "https://example.com/a")
    second = _follower("乙", "https://example.com/b")
    manager.write_followers_csv([first, second])

    removed = manager.remove_follower(
        0,
        expected_key=follower_key(first),
        expected_follower=first,
    )

    assert removed == first
    assert manager.read_followers() == [second]


def test_remove_follower_refuses_last_row_and_stale_confirmation(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    first = _follower("甲", "https://example.com/a")
    second = _follower("乙", "https://example.com/b")
    manager.write_followers_csv([first, second])

    with pytest.raises(ValueError, match="确认后变化"):
        manager.remove_follower(0, expected_follower=second)
    assert manager.read_followers() == [first, second]

    manager.write_followers_csv([first])
    with pytest.raises(ValueError, match="至少需要保留一个"):
        manager.remove_follower(0, expected_follower=first)
    assert manager.read_followers() == [first]


def test_validate_monitoring_settings_normalizes_complete_and_partial_edits():
    settings = validate_monitoring_settings(
        {
            "poll_interval": "90",
            "max_concurrent_checks": "12",
            "failure_backoff_after": "4",
            "failure_backoff_polls": "5",
            "notifications_enabled": "false",
        }
    )

    assert settings == {
        "poll_interval": 90,
        "max_concurrent_checks": 12,
        "failure_backoff_after": 4,
        "failure_backoff_polls": 5,
        "notifications_enabled": False,
    }
    assert validate_monitoring_settings(
        {"poll_interval": "120"}, current=settings
    ) == {**settings, "poll_interval": 120}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("poll_interval", "4"),
        ("poll_interval", "3601"),
        ("max_concurrent_checks", "0"),
        ("max_concurrent_checks", "17"),
        ("failure_backoff_after", "0"),
        ("failure_backoff_polls", "61"),
        ("notifications_enabled", "perhaps"),
    ],
)
def test_validate_monitoring_settings_rejects_unsafe_ranges(field, value):
    with pytest.raises(MonitoringSettingsValidationError):
        validate_monitoring_settings({field: value})


def test_preview_monitoring_settings_uses_an_allowlisted_safe_diff():
    preview = preview_monitoring_settings(
        {
            "poll_interval": 60,
            "max_concurrent_checks": 8,
            "failure_backoff_after": 3,
            "failure_backoff_polls": 2,
            "notifications_enabled": True,
            "platform_proxies": {"twitch": "7897"},
        },
        {"poll_interval": "90", "notifications_enabled": False},
    )

    assert preview.has_changes is True
    assert preview.before == {
        "poll_interval": 60,
        "max_concurrent_checks": 8,
        "failure_backoff_after": 3,
        "failure_backoff_polls": 2,
        "notifications_enabled": True,
    }
    assert preview.changes == {
        "poll_interval": (60, 90),
        "notifications_enabled": (True, False),
    }
    assert "platform_proxies" not in preview.after


def test_config_manager_updates_monitoring_settings_without_followers_or_proxy_loss(tmp_path):
    manager = ConfigManager(tmp_path / "followers.csv")
    manager.save_config(
        config.AppConfig(
            poll_interval=60,
            max_concurrent_checks=8,
            failure_backoff_after=3,
            failure_backoff_polls=2,
            notifications_enabled=True,
            platform_proxies={"twitch": "7897"},
        )
    )
    expected = manager.read_monitoring_settings()

    preview = manager.update_monitoring_settings(
        {"poll_interval": "90", "notifications_enabled": "false"},
        expected_settings=expected,
    )

    assert preview.after["poll_interval"] == 90
    assert manager.read_monitoring_settings()["notifications_enabled"] is False
    settings_text = (tmp_path / "settings.csv").read_text(encoding="utf-8-sig")
    assert "platform_proxy.twitch,7897" in settings_text


def test_save_config_cannot_persist_unsafe_monitoring_limits(tmp_path):
    manager = ConfigManager(tmp_path / "followers.csv")
    cfg = config.AppConfig(poll_interval=1, max_concurrent_checks=999999)

    manager.save_config(cfg)

    assert cfg.poll_interval == 5
    assert cfg.max_concurrent_checks == 16
    assert manager.read_monitoring_settings()["poll_interval"] == 5


def test_config_manager_rejects_stale_monitoring_settings_preview(tmp_path):
    manager = ConfigManager(tmp_path / "followers.csv")
    manager.save_config(config.AppConfig())
    expected = manager.read_monitoring_settings()
    manager.update_monitoring_settings({"poll_interval": 90})
    saved = (tmp_path / "settings.csv").read_bytes()

    with pytest.raises(ValueError, match="预览后变更"):
        manager.update_monitoring_settings(
            {"notifications_enabled": False}, expected_settings=expected
        )

    assert (tmp_path / "settings.csv").read_bytes() == saved


def test_config_manager_keeps_settings_when_atomic_monitoring_save_fails(tmp_path, monkeypatch):
    manager = ConfigManager(tmp_path / "followers.csv")
    manager.save_config(config.AppConfig())
    settings_path = tmp_path / "settings.csv"
    original = settings_path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated settings replace failure")

    monkeypatch.setattr(config.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated settings replace failure"):
        manager.update_monitoring_settings({"poll_interval": 90})

    assert settings_path.read_bytes() == original
    assert not list(tmp_path.glob(".settings.csv.*.tmp"))
