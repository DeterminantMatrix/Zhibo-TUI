import os
from pathlib import Path

import pytest

from zhibo import private_data
from zhibo.private_data import (
    configure_legacy_cookie_path,
    cookie_file_path,
    fs_config_path,
    legacy_credential_files,
    legacy_credentials_notice,
    log_file_path,
    migrate_legacy_credentials,
    user_data_dir,
)


def test_windows_default_prefers_localappdata(tmp_path: Path):
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "RoamingAppData"

    result = user_data_dir(
        {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)},
        platform_name="Windows",
        home=tmp_path / "home",
    )

    assert result == local / "Zhibo"


def test_private_data_dir_and_individual_overrides_are_resolved_without_writes(tmp_path: Path):
    data_dir = tmp_path / "private"
    env = {"ZHIBO_DATA_DIR": str(data_dir)}

    assert cookie_file_path(env) == data_dir / "cookies.txt"
    assert fs_config_path(env) == data_dir / "fs1" / "rooms.yaml"
    assert log_file_path(env) == data_dir / "logs" / "zhibo.log"
    assert not data_dir.exists()

    cookie = tmp_path / "custom" / "cookies.txt"
    rooms = tmp_path / "custom" / "rooms.yaml"
    log = tmp_path / "custom" / "zhibo.log"
    overridden = {
        "ZHIBO_DATA_DIR": str(data_dir),
        "ZHIBO_COOKIE_FILE": str(cookie),
        "ZHIBO_FS_CONFIG": str(rooms),
        "ZHIBO_LOG_FILE": str(log),
    }
    assert cookie_file_path(overridden) == cookie
    assert fs_config_path(overridden) == rooms
    assert log_file_path(overridden) == log


def test_legacy_discovery_is_explicit_and_never_exposes_contents(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    secret = "legacy-cookie-value-that-must-not-appear"
    (project / "cookies.txt").write_text(secret, encoding="utf-8")
    env = {"ZHIBO_DATA_DIR": str(tmp_path / "private")}

    items = legacy_credential_files(project, env)
    notice = legacy_credentials_notice(project, env)

    assert items[0].exists is True
    assert items[0].destination == tmp_path / "private" / "cookies.txt"
    assert "Cookie" in notice
    assert secret not in notice
    assert not items[0].destination.exists()


def test_explicit_migration_copies_without_moving_or_overwriting(tmp_path: Path):
    project = tmp_path / "project"
    source_fs = project / "sports" / "rooms.yaml"
    source_fs.parent.mkdir(parents=True)
    source_cookie = project / "cookies.txt"
    source_cookie.write_text("cookie-source", encoding="utf-8")
    source_fs.write_text("config:\n  token: fs-source\n", encoding="utf-8")
    private_dir = tmp_path / "private"
    env = {"ZHIBO_DATA_DIR": str(private_dir)}

    results = migrate_legacy_credentials(project, env)

    assert [result.status for result in results] == ["copied", "copied"]
    assert (private_dir / "cookies.txt").read_text(encoding="utf-8") == "cookie-source"
    assert (private_dir / "fs1" / "rooms.yaml").read_text(encoding="utf-8") == "config:\n  token: fs-source\n"
    assert source_cookie.read_text(encoding="utf-8") == "cookie-source"
    assert source_fs.read_text(encoding="utf-8") == "config:\n  token: fs-source\n"

    (private_dir / "cookies.txt").write_text("existing-private", encoding="utf-8")
    second_results = migrate_legacy_credentials(project, env)
    assert second_results[0].status == "destination_exists"
    assert (private_dir / "cookies.txt").read_text(encoding="utf-8") == "existing-private"


def test_legacy_cookie_compatibility_shim_sets_safe_path_without_creating_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ZHIBO_COOKIE_FILE", raising=False)
    monkeypatch.setenv("ZHIBO_DATA_DIR", str(tmp_path / "private"))

    result = configure_legacy_cookie_path()

    assert result == tmp_path / "private" / "cookies.txt"
    assert result == Path(os.environ["ZHIBO_COOKIE_FILE"])
    assert not result.exists()


def test_legacy_ytdlp_cookie_resolver_uses_the_safe_compatibility_path(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ZHIBO_COOKIE_FILE", raising=False)
    monkeypatch.setenv("ZHIBO_DATA_DIR", str(tmp_path / "private"))
    expected = cookie_file_path()

    from zhibo.plugins.yt_dlp_plugin import _cookie_file_path

    assert _cookie_file_path() == expected
    assert "ZHIBO_COOKIE_FILE" not in os.environ
    assert not expected.exists()


def test_explicit_migration_rejects_a_symlinked_legacy_parent(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "rooms.yaml").write_text("config: {}\n", encoding="utf-8")
    try:
        (project / "sports").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable for this test account")

    results = migrate_legacy_credentials(project, {"ZHIBO_DATA_DIR": str(tmp_path / "private")})

    assert results[1].status == "skipped_symlink"
    assert not (tmp_path / "private" / "fs1" / "rooms.yaml").exists()


def test_migration_does_not_publish_partial_file_when_final_link_fails(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "cookies.txt").write_text("credential", encoding="utf-8")
    private_dir = tmp_path / "private"
    monkeypatch.setattr(private_data.os, "link", lambda source, destination: (_ for _ in ()).throw(OSError()))

    results = migrate_legacy_credentials(project, {"ZHIBO_DATA_DIR": str(private_dir)})

    assert results[0].status == "copy_failed"
    assert not (private_dir / "cookies.txt").exists()
    assert not list(private_dir.glob(".cookies.txt.*.tmp"))
