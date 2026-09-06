"""Install and activate the optional uosc interface for Zhibo's MPV player."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.error
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from packaging.version import InvalidVersion, Version

from zhibo.private_data import user_data_dir
from zhibo.tool_runtime import MIB, _download, _read_json, _safe_extract_zip


ProgressCallback = Callable[[float, str], None]
UOSC_REPO = "tomasklaen/uosc"
UOSC_MAX_ARCHIVE_SIZE = 20 * MIB
UOSC_MAX_CONFIG_SIZE = 256 * 1024
MARKER_NAME = ".zhibo-uosc.json"


@dataclass(frozen=True)
class UoscUpdatePlan:
    installed_version: str
    remote_version: str
    status: str
    assets: tuple[dict, ...] = ()
    reason: str = ""

    @property
    def action_allowed(self) -> bool:
        return self.status in {"install", "update", "repair"} and len(self.assets) == 2

    @property
    def download_size(self) -> int:
        return sum(int(asset.get("size") or 0) for asset in self.assets)


def uosc_config_dir() -> Path:
    return user_data_dir() / "mpv-ui" / "uosc"


def _marker_path(root: Path | None = None) -> Path:
    return (root or uosc_config_dir()) / MARKER_NAME


def _required_files(root: Path) -> tuple[Path, ...]:
    return (
        root / "scripts" / "uosc" / "main.lua",
        root / "scripts" / "uosc" / "bin" / "ziggy-windows.exe",
        root / "fonts" / "uosc_icons.otf",
        root / "fonts" / "uosc_textures.ttf",
        root / "script-opts" / "uosc.conf",
        root / "mpv.conf",
    )


def uosc_installed_version() -> str:
    root = uosc_config_dir()
    marker = _marker_path(root)
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        version = str(payload.get("version") or "").strip()
    except (OSError, ValueError, TypeError):
        version = ""
    if version and all(path.is_file() for path in _required_files(root)):
        return version
    if root.exists():
        return "安装不完整"
    return "未安装"


def active_uosc_config_dir() -> Path | None:
    return uosc_config_dir() if uosc_installed_version() not in {"未安装", "安装不完整"} else None


def _release_assets(release: dict) -> tuple[dict, dict]:
    assets = {str(asset.get("name") or ""): asset for asset in release.get("assets") or []}
    archive = assets.get("uosc.zip")
    config = assets.get("uosc.conf")
    if not isinstance(archive, dict) or not isinstance(config, dict):
        raise RuntimeError("uosc 发布页缺少 uosc.zip 或 uosc.conf")
    for asset, limit in ((archive, UOSC_MAX_ARCHIVE_SIZE), (config, UOSC_MAX_CONFIG_SIZE)):
        size = int(asset.get("size") or 0)
        digest = str(asset.get("digest") or "")
        if size <= 0 or size > limit:
            raise RuntimeError(f"uosc 资产 {asset.get('name')} 体积异常")
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise RuntimeError(f"uosc 资产 {asset.get('name')} 缺少 SHA-256")
    return archive, config


def check_uosc_update() -> UoscUpdatePlan:
    installed = uosc_installed_version()
    try:
        release = _read_json(f"https://api.github.com/repos/{UOSC_REPO}/releases/latest")
        remote = str(release.get("tag_name") or "").lstrip("v").strip()
        if not remote:
            raise RuntimeError("uosc 发布页没有版本号")
        archive, config = _release_assets(release)
        assets = (dict(config), dict(archive))
        if installed == "未安装":
            status = "install"
        elif installed == "安装不完整":
            status = "repair"
        else:
            status = "update" if Version(installed) < Version(remote) else "current"
        return UoscUpdatePlan(installed, remote, status, assets)
    except (OSError, ValueError, TypeError, InvalidVersion, RuntimeError, urllib.error.URLError) as exc:
        return UoscUpdatePlan(installed, "无法确认", "unknown", reason=f"检查 uosc 更新失败：{exc}")


def uosc_plan_ui(plan: UoscUpdatePlan) -> dict:
    labels = {
        "install": "安装",
        "update": "更新",
        "repair": "修复安装",
        "current": "已是最新",
        "unknown": "重新检查",
    }
    hints = {
        "install": f"可安装 uosc {plan.remote_version}",
        "update": f"发现 uosc {plan.remote_version}",
        "repair": f"当前安装不完整，可修复为 {plan.remote_version}",
        "current": f"uosc {plan.remote_version}，无需更新",
        "unknown": plan.reason or "暂时无法读取 uosc 版本",
    }
    size = f"{plan.download_size / MIB:.1f} MB" if plan.download_size else ""
    return {
        "version": plan.installed_version,
        "installed": plan.installed_version not in {"未安装", "安装不完整"},
        "remoteVersion": plan.remote_version,
        "downloadSize": size,
        "updateStatus": plan.status,
        "updateHint": hints[plan.status],
        "actionLabel": labels[plan.status],
        "actionEnabled": plan.action_allowed or plan.status == "unknown",
        "actionKind": "recheck" if plan.status == "unknown" else "execute",
    }


def _write_managed_config(root: Path, version: str) -> None:
    (root / "script-opts").mkdir(parents=True, exist_ok=True)
    (root / "mpv.conf").write_text(
        "# Managed by Zhibo for the uosc interface.\n"
        "osc=no\n"
        "osd-bar=no\n"
        "border=no\n",
        encoding="utf-8",
    )
    (root / "input.conf").write_text(
        "# Open uosc's searchable menu with the right mouse button.\n"
        "MBTN_RIGHT script-binding uosc/menu\n",
        encoding="utf-8",
    )
    _marker_path(root).write_text(
        json.dumps(
            {
                "component": "uosc",
                "version": version,
                "installed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def install_uosc(
    progress: ProgressCallback | None = None,
    *,
    plan: UoscUpdatePlan | None = None,
) -> str:
    """Download, verify, and atomically replace Zhibo's complete uosc config."""
    checked = plan or check_uosc_update()
    if not checked.action_allowed:
        detail = checked.reason or ("当前已是最新版本" if checked.status == "current" else "更新状态无效")
        raise RuntimeError(f"未通过 uosc 下载前检查：{detail}")
    emit = progress or (lambda _value, _message: None)
    parent = uosc_config_dir().parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".uosc-install-", dir=parent))
    content = temporary / "content"
    content.mkdir()
    target = uosc_config_dir()
    backup = parent / ".uosc-previous"
    try:
        config_asset, archive_asset = checked.assets
        config_file = temporary / "uosc.conf"
        archive_file = temporary / "uosc.zip"
        emit(8, f"已确认 uosc {checked.remote_version}，共 {checked.download_size / MIB:.1f} MB")
        _download(
            config_asset,
            config_file,
            lambda _value, message: emit(12, message),
            max_size=UOSC_MAX_CONFIG_SIZE,
        )
        _download(
            archive_asset,
            archive_file,
            lambda value, message: emit(15 + max(0, value - 12) * 0.9, message),
            max_size=UOSC_MAX_ARCHIVE_SIZE,
        )
        emit(75, "正在解压 uosc…")
        _safe_extract_zip(archive_file, content)
        for unused_binary in ("ziggy-darwin", "ziggy-linux"):
            (content / "scripts" / "uosc" / "bin" / unused_binary).unlink(missing_ok=True)
        (content / "script-opts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_file, content / "script-opts" / "uosc.conf")
        _write_managed_config(content, checked.remote_version)
        missing = [path.relative_to(content) for path in _required_files(content) if not path.is_file()]
        if missing:
            raise RuntimeError(f"uosc 安装包缺少文件：{', '.join(map(str, missing))}")
        emit(90, "正在启用 uosc…")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        try:
            if target.exists():
                os.replace(target, backup)
        except PermissionError as exc:
            raise RuntimeError(
                "无法替换 uosc：文件正被其他程序占用。请关闭正在使用该目录的程序后重试。"
            ) from exc
        try:
            os.replace(content, target)
        except Exception:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            # 旧版本清理失败不应把已完成的更新报成失败；残留交给启动清理。
            try:
                shutil.rmtree(backup)
            except OSError:
                emit(99, "旧版本备份暂时无法删除，将在下次启动时自动清理")
        version = uosc_installed_version()
        emit(100, f"uosc {version} 已启用")
        return version
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def sweep_uosc_leftovers() -> list[str]:
    """清理 uosc 安装中断留下的临时目录，并自愈半完成的迁移。"""
    cleaned: list[str] = []
    target = uosc_config_dir()
    parent = target.parent
    if not parent.exists():
        return cleaned
    backup = parent / ".uosc-previous"
    try:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
            cleaned.append("已恢复 uosc 的上次备份")
        for leftover in parent.glob(".uosc-install-*"):
            shutil.rmtree(leftover, ignore_errors=True)
            cleaned.append(f"已清理残留 {leftover.name}")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
            cleaned.append(f"已清理残留 {backup.name}")
    except OSError:
        pass
    return cleaned
