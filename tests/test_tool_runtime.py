import zipfile

import pytest

from zhibo import tool_runtime
def _asset(name="tool.zip", size=1024):
    return {
        "name": name,
        "browser_download_url": f"https://example.test/{name}",
        "size": size,
        "digest": "sha256:" + "a" * 64,
    }


def test_ffmpeg_uses_small_essentials_7z_release():
    definition = tool_runtime.TOOL_RELEASES["ffmpeg"]

    assert definition["asset_name"] == "ffmpeg-release-essentials.7z"
    assert definition["asset_url"].endswith("ffmpeg-release-essentials.7z")
    assert definition["archive"] == "7z"
    assert definition["max_size"] == 64 * 1024 * 1024
    assert "BtbN" not in definition["asset_url"]


def test_ffmpeg_check_marks_missing_tool_for_install(monkeypatch):
    monkeypatch.setattr(tool_runtime, "tool_version", lambda _name: "未安装")
    monkeypatch.setattr(
        tool_runtime,
        "_read_text",
        lambda url: "8.1.2" if url.endswith(".ver") else "a" * 64,
    )
    monkeypatch.setattr(tool_runtime, "_remote_size", lambda _url: 32 * 1024 * 1024)

    plan = tool_runtime.check_tool_update("ffmpeg")

    assert plan.status == "install"
    assert plan.remote_version == "8.1.2"
    assert plan.download_size == 32 * 1024 * 1024
    assert plan.asset["name"] == "ffmpeg-release-essentials.7z"


@pytest.mark.parametrize(
    ("installed", "status"),
    [
        ("ffmpeg version 8.0", "update"),
        ("ffmpeg version 8.1.2", "current"),
        ("ffmpeg version 9.0", "current"),
        ("ffmpeg version N-123456-gabcdef", "migrate"),
        ("ffmpeg.exe", "repair"),
    ],
)
def test_ffmpeg_check_compares_versions_and_never_guesses(installed, status, monkeypatch):
    monkeypatch.setattr(tool_runtime, "tool_version", lambda _name: installed)
    monkeypatch.setattr(
        tool_runtime,
        "_read_text",
        lambda url: "8.1.2" if url.endswith(".ver") else "b" * 64,
    )
    monkeypatch.setattr(tool_runtime, "_remote_size", lambda _url: 32 * 1024 * 1024)

    plan = tool_runtime.check_tool_update("ffmpeg")

    assert plan.status == status
    assert plan.action_allowed is (status in {"update", "migrate", "repair"})


def test_ffmpeg_legacy_snapshot_is_presented_as_explicit_small_build_migration(monkeypatch):
    monkeypatch.setattr(
        tool_runtime,
        "tool_version",
        lambda _name: "ffmpeg version N-125649-g8d394252d8-20260717",
    )
    monkeypatch.setattr(
        tool_runtime,
        "_read_text",
        lambda url: "8.1.2" if url.endswith(".ver") else "d" * 64,
    )
    monkeypatch.setattr(tool_runtime, "_remote_size", lambda _url: 32 * 1024 * 1024)

    plan = tool_runtime.check_tool_update("ffmpeg")
    fields = tool_runtime.tool_plan_ui(plan)

    assert plan.status == "migrate"
    assert fields["actionLabel"] == "换用精简版"
    assert fields["actionEnabled"] is True
    assert "开发快照" in fields["updateHint"]


def test_ffmpeg_check_rejects_suspiciously_large_package(monkeypatch):
    monkeypatch.setattr(tool_runtime, "tool_version", lambda _name: "未安装")
    monkeypatch.setattr(
        tool_runtime,
        "_read_text",
        lambda url: "8.1.2" if url.endswith(".ver") else "c" * 64,
    )
    monkeypatch.setattr(tool_runtime, "_remote_size", lambda _url: 101 * 1024 * 1024)

    plan = tool_runtime.check_tool_update("ffmpeg")

    assert plan.status == "unknown"
    assert plan.action_allowed is False
    assert "体积异常" in plan.reason


@pytest.mark.parametrize(
    ("comparison", "status"),
    [("ahead", "update"), ("identical", "current"), ("behind", "current"), ("diverged", "unknown")],
)
def test_mpv_check_uses_git_ancestry(comparison, status, monkeypatch):
    asset = _asset("mpv-x86_64-20260718-git-bbbbbbbbbbbbbbbb.7z", 33 * 1024 * 1024)
    release = {
        "body": "MPV Git commit: https://github.com/mpv-player/mpv/commit/bbbbbbbbbbbbbbbb",
        "assets": [
            _asset("mpv-x86_64-v3-20260718-git-bbbbbbbb.7z", 34 * 1024 * 1024),
            asset,
        ],
    }
    monkeypatch.setattr(tool_runtime, "tool_version", lambda _name: "v0.41.0-515-gaaaaaaaaaaaaaaaa")
    monkeypatch.setattr(
        tool_runtime,
        "_read_json",
        lambda url: release if "/releases/latest" in url else {"status": comparison},
    )

    plan = tool_runtime.check_tool_update("mpv")

    assert plan.status == status
    assert plan.asset == (asset if status != "unknown" else None)
    assert plan.action_allowed is (status == "update")


def test_portable_ffmpeg_install_is_atomic_and_discoverable(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_runtime, "managed_tools_dir", lambda: tmp_path / "tools")
    definition = dict(tool_runtime.TOOL_RELEASES["ffmpeg"], archive="zip")
    monkeypatch.setitem(tool_runtime.TOOL_RELEASES, "ffmpeg", definition)
    asset = _asset("ffmpeg.zip")
    plan = tool_runtime.ToolUpdatePlan("ffmpeg", "未安装", "8.1.2", "install", asset)

    def fake_download(_asset, destination, progress, *, max_size):
        assert max_size == 64 * 1024 * 1024
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("ffmpeg-build/bin/ffmpeg.exe", b"binary")
        progress(70, "downloaded")

    monkeypatch.setattr(tool_runtime, "_download", fake_download)
    monkeypatch.setattr(tool_runtime, "tool_version", lambda _name: "ffmpeg version test")
    messages = []

    version = tool_runtime.install_portable_tool(
        "ffmpeg", lambda value, message: messages.append((value, message)), plan=plan
    )

    assert version == "ffmpeg version test"
    assert tool_runtime.managed_executable("ffmpeg").read_bytes() == b"binary"
    assert messages[-1][0] == 100


def test_portable_install_refuses_current_or_unknown_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_runtime, "managed_tools_dir", lambda: tmp_path / "tools")
    monkeypatch.setattr(
        tool_runtime,
        "_download",
        lambda *_args, **_kwargs: pytest.fail("must not download without an actionable plan"),
    )
    plan = tool_runtime.ToolUpdatePlan("ffmpeg", "ffmpeg version 8.1.2", "8.1.2", "current", _asset())

    with pytest.raises(RuntimeError, match="已是最新"):
        tool_runtime.install_portable_tool("ffmpeg", plan=plan)

    assert not (tmp_path / "tools").exists()


def test_zip_extraction_rejects_parent_traversal(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../outside.exe", b"unsafe")

    with pytest.raises(RuntimeError, match="不安全路径"):
        tool_runtime._safe_extract_zip(archive, tmp_path / "content")

    assert not (tmp_path / "outside.exe").exists()


def test_download_requires_sha256_before_network_access(tmp_path):
    asset = _asset(size=1)
    asset.pop("digest")

    with pytest.raises(RuntimeError, match="SHA-256"):
        tool_runtime._download(asset, tmp_path / "package.bin", lambda *_args: None, max_size=1024)

    assert not (tmp_path / "package.bin").exists()


def test_7z_extraction_rejects_parent_traversal_before_extract(tmp_path, monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = "Path = C:\\temp\\unsafe.7z\n\nPath = ..\\outside.exe\nSize = 1\n"
        stderr = ""

    monkeypatch.setattr(tool_runtime.shutil, "which", lambda name: "7z.exe" if name == "7z" else None)

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(tool_runtime.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="不安全路径"):
        tool_runtime._extract_7z(tmp_path / "unsafe.7z", tmp_path / "content")

    assert calls == [["7z.exe", "l", "-slt", str(tmp_path / "unsafe.7z")]]


def test_mpv_check_rejects_release_without_digest(monkeypatch):
    asset = _asset("mpv-x86_64-20260718-git-bbbbbbbbbbbbbbbb.7z", 33 * 1024 * 1024)
    asset.pop("digest")
    release = {
        "body": "MPV Git commit: https://github.com/mpv-player/mpv/commit/bbbbbbbbbbbbbbbb",
        "assets": [asset],
    }
    monkeypatch.setattr(tool_runtime, "tool_version", lambda _name: "未安装")
    monkeypatch.setattr(tool_runtime, "_read_json", lambda _url: release)

    plan = tool_runtime.check_tool_update("mpv")

    assert plan.status == "unknown"
    assert plan.action_allowed is False
    assert "SHA-256" in plan.reason


def test_sweep_install_leftovers_restores_backup_and_removes_temporaries(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_runtime, "managed_tools_dir", lambda: tmp_path)
    (tmp_path / "mpv").mkdir()
    (tmp_path / "mpv" / "mpv.exe").write_bytes(b"x")
    (tmp_path / ".mpv-install-abcd").mkdir()
    (tmp_path / ".mpv-previous").mkdir()
    (tmp_path / ".ffmpeg-install-1234").mkdir()

    cleaned = tool_runtime.sweep_install_leftovers()

    assert (tmp_path / "mpv" / "mpv.exe").exists()
    assert not (tmp_path / ".mpv-install-abcd").exists()
    assert not (tmp_path / ".mpv-previous").exists()
    assert not (tmp_path / ".ffmpeg-install-1234").exists()
    assert len(cleaned) == 3


def test_sweep_install_leftovers_recovers_missing_target_from_backup(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_runtime, "managed_tools_dir", lambda: tmp_path)
    backup = tmp_path / ".mpv-previous"
    backup.mkdir()
    (backup / "mpv.exe").write_bytes(b"old")

    cleaned = tool_runtime.sweep_install_leftovers()

    assert (tmp_path / "mpv" / "mpv.exe").read_bytes() == b"old"
    assert not backup.exists()
    assert cleaned
