"""Tests for the non-mutating follower import preview workflow."""
import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from zhibo import config
from zhibo import importer
from zhibo.config import ConfigManager
from zhibo.import_preview import (
    ImportConfirmationRequired,
    ImportConflictError,
    ImportDuplicateError,
    ImportValidationFailed,
    ImportPreviewService,
    build_import_preview,
    confirm_import_preview,
)
from zhibo.models import Follower


@dataclass
class RecordingConfigManager:
    followers: list[Follower] = field(default_factory=list)
    appended: list[Follower] = field(default_factory=list)

    def read_followers(self) -> list[Follower]:
        return list(self.followers)

    def append_follower(self, follower: Follower) -> None:
        self.appended.append(follower)
        self.followers.append(follower)

    def append_validated_follower(self, follower: Follower):
        self.append_follower(follower)
        return SimpleNamespace(follower=follower)


def _follower(
    *,
    name: str = "主播",
    platform: str = "twitch",
    url: str = "https://www.twitch.tv/example",
    sport_id: str = "",
) -> Follower:
    extra = {"sport_id": sport_id} if sport_id else {}
    return Follower(
        name=name,
        plugin="streamlink",
        platform=platform,
        url=url,
        tags=["测试"],
        extra=extra,
    )


def test_preview_builds_candidate_without_writing(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    existing = [_follower(name="已有主播", url="https://www.twitch.tv/other")]

    preview = asyncio.run(
        build_import_preview(
            "www.twitch.tv/example",
            "POE",
            existing_followers=existing,
        )
    )

    assert preview.is_valid is True
    assert preview.can_confirm is True
    assert preview.follower is not None
    assert preview.follower.url == "https://www.twitch.tv/example"
    assert preview.follower.tags == ["POE"]
    assert preview.duplicate is None
    assert preview.conflicts == ()


def test_preview_returns_structured_url_validation_error():
    preview = asyncio.run(build_import_preview("file:///C:/Windows/System32/calc.exe", "测试"))

    assert preview.is_valid is False
    assert preview.follower is None
    assert preview.validation_errors[0].code == "unsupported_scheme"
    assert preview.messages == (preview.validation_errors[0].message,)


def test_sensitive_import_is_rejected_before_preview_or_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    preview = asyncio.run(
        build_import_preview(
            "https://www.twitch.tv/example?token=must-not-be-written",
            "测试",
        )
    )
    manager = ConfigManager(tmp_path / "followers.csv")

    assert preview.is_valid is False
    with pytest.raises(ImportValidationFailed):
        confirm_import_preview(preview, manager, confirmed=True)

    assert not manager.config_path.exists()


def test_preview_identifies_exact_duplicate(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    existing = [_follower(name="已关注", url="https://www.twitch.tv/example/")]

    preview = asyncio.run(
        build_import_preview(
            "https://www.twitch.tv/example",
            "测试",
            existing_followers=existing,
        )
    )

    assert preview.duplicate is not None
    assert preview.duplicate.kind == "duplicate"
    assert preview.can_confirm is False
    assert "已关注" in preview.duplicate.message


def test_preview_identifies_same_name_different_room(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    existing = [_follower(name="example", url="https://www.twitch.tv/old-room")]

    preview = asyncio.run(
        build_import_preview(
            "https://www.twitch.tv/example",
            "测试",
            existing_followers=existing,
        )
    )

    assert preview.duplicate is None
    assert [conflict.kind for conflict in preview.conflicts] == ["same_name_different_room"]
    assert preview.requires_conflict_override is True


def test_preview_identifies_same_fs_room_with_different_context(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    existing = [
        Follower(
            name="足球",
            plugin="fs1",
            platform="fs1",
            url="740426526",
            tags=["体育"],
            extra={"sport_id": "202"},
        )
    ]

    preview = asyncio.run(
        build_import_preview(
            "https://fszb148.com/broadcast/details?room_id=740426526&sport_id=101",
            "体育",
            existing_followers=existing,
        )
    )

    assert preview.duplicate is None
    assert [conflict.kind for conflict in preview.conflicts] == ["same_room_different_context"]


def test_confirmation_is_required_before_any_write(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    manager = RecordingConfigManager()
    preview = asyncio.run(build_import_preview("https://www.twitch.tv/example", "测试"))

    with pytest.raises(ImportConfirmationRequired):
        confirm_import_preview(preview, manager)

    assert manager.appended == []


def test_confirmation_rechecks_latest_duplicate_before_writing(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    preview = asyncio.run(build_import_preview("https://www.twitch.tv/example", "测试"))
    manager = RecordingConfigManager(followers=[_follower(name="另一位")])

    with pytest.raises(ImportDuplicateError):
        confirm_import_preview(preview, manager, confirmed=True)

    assert manager.appended == []


def test_conflict_needs_separate_override_and_can_then_write(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    manager = RecordingConfigManager(
        followers=[_follower(name="example", url="https://www.twitch.tv/old-room")]
    )
    preview = asyncio.run(
        build_import_preview(
            "https://www.twitch.tv/example",
            "测试",
            existing_followers=manager.read_followers(),
        )
    )

    with pytest.raises(ImportConflictError):
        confirm_import_preview(preview, manager, confirmed=True)
    assert manager.appended == []

    with pytest.raises(ImportConflictError):
        confirm_import_preview(
            preview,
            manager,
            confirmed=True,
            allow_conflicts="yes",  # type: ignore[arg-type]
        )
    assert manager.appended == []

    saved = confirm_import_preview(
        preview,
        manager,
        confirmed=True,
        allow_conflicts=True,
    )

    assert saved.url == "https://www.twitch.tv/example"
    assert manager.appended == [saved]


def test_conflict_override_is_bound_to_the_previewed_conflict_set(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    first = _follower(name="example", url="https://www.twitch.tv/old-room")
    manager = RecordingConfigManager(followers=[first])
    preview = asyncio.run(
        build_import_preview(
            "https://www.twitch.tv/example",
            "测试",
            existing_followers=manager.read_followers(),
        )
    )
    # A second same-name row appears after the user has seen the first preview.
    manager.followers.append(_follower(name="example", url="https://www.twitch.tv/another-room"))

    with pytest.raises(ImportConflictError, match="预览后变化"):
        confirm_import_preview(
            preview,
            manager,
            confirmed=True,
            allow_conflicts=True,
        )

    assert manager.appended == []


def test_service_reads_current_followers_for_preview_and_confirm(monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    manager = RecordingConfigManager()
    service = ImportPreviewService(manager)

    preview = asyncio.run(service.preview("https://www.twitch.tv/example", "测试"))
    saved = service.confirm(preview, confirmed=True)

    assert saved.name == "example"
    assert manager.appended == [saved]


def test_service_persists_only_after_confirmation_with_real_config_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    manager = config.ConfigManager(tmp_path / "followers.csv")
    service = ImportPreviewService(manager)

    preview = asyncio.run(service.preview("https://www.twitch.tv/example", "测试"))
    saved = service.confirm(preview, confirmed=True)

    assert manager.config_path.exists()
    assert manager.read_followers() == [saved]


def test_confirmation_refuses_to_rewrite_a_malformed_current_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "get_plugin", lambda name: None)
    path = tmp_path / "followers.csv"
    path.write_text(
        "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id,extra\n"
        "true,损坏项,测试,,,,https://www.twitch.tv/broken,best,,\n",
        encoding="utf-8",
    )
    manager = config.ConfigManager(path)
    preview = asyncio.run(build_import_preview("https://www.twitch.tv/example", "测试"))
    before = path.read_bytes()

    with pytest.raises(ValueError):
        confirm_import_preview(preview, manager, confirmed=True)

    assert path.read_bytes() == before


@pytest.mark.parametrize("query_key", ["token", "auth", "sign"])
def test_preview_rejects_sensitive_url_before_plugin_lookup(monkeypatch, query_key):
    monkeypatch.setattr(importer, "get_plugin", lambda name: pytest.fail("plugin lookup should not occur"))

    preview = asyncio.run(
        build_import_preview(
            f"https://www.twitch.tv/example?{query_key}=must-not-reach-plugin",
            "测试",
        )
    )

    assert preview.is_valid is False
    assert preview.follower is None
