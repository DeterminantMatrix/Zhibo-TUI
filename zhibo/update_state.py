"""Installed-version discovery and durable updater history."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

from zhibo.private_data import ensure_private_parent, user_data_dir
from zhibo.tool_runtime import find_tool, managed_executable, tool_version


UPDATE_TARGETS = (
    {
        "value": "streamlink",
        "label": "streamlink",
        "distribution": "streamlink",
        "description": "通用直播解析器，负责 Twitch、YouTube、B站等平台。",
        "kind": "package",
        "restartRequired": True,
    },
    {
        "value": "streamget",
        "label": "streamget",
        "distribution": "streamget",
        "description": "国内外直播平台解析器和元数据回退。",
        "kind": "package",
        "restartRequired": True,
    },
    {
        "value": "yt-dlp",
        "label": "yt-dlp",
        "distribution": "yt-dlp",
        "description": "视频信息读取与下载组件。",
        "kind": "package",
        "restartRequired": True,
    },
    {
        "value": "fs1",
        "label": "FS1 配置",
        "description": "飞速直播接口、Token 和设备参数。",
        "kind": "configuration",
        "restartRequired": False,
    },
    {
        "value": "bilibili_cookie",
        "label": "B站 Cookie",
        "description": "B站账号画质授权信息，仅保存到本机私有目录。",
        "kind": "credential",
        "restartRequired": False,
    },
)


@dataclass(frozen=True)
class PackageUpdatePlan:
    target: str
    distribution: str
    installed_version: str
    remote_version: str
    status: str
    reason: str = ""

    @property
    def action_allowed(self) -> bool:
        return self.status in {"install", "update"}


def check_package_update(target: str, distribution: str | None = None) -> PackageUpdatePlan:
    """Read PyPI metadata and make a download-free package update decision."""
    package = distribution or target
    installed = installed_version(package)
    url = f"https://pypi.org/pypi/{urllib.parse.quote(package, safe='')}/json"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Zhibo-Updater/2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.load(response)
        remote = str((payload.get("info") or {}).get("version") or "").strip()
        if not remote:
            raise ValueError("PyPI 未返回版本号")
        if installed == "未安装":
            status = "install"
        else:
            status = "update" if Version(installed) < Version(remote) else "current"
        return PackageUpdatePlan(target, package, installed, remote, status)
    except (OSError, ValueError, TypeError, InvalidVersion, urllib.error.URLError) as exc:
        return PackageUpdatePlan(
            target,
            package,
            installed,
            "无法确认",
            "unknown",
            f"检查 PyPI 版本失败：{exc}",
        )


def package_plan_ui(plan: PackageUpdatePlan) -> dict:
    labels = {
        "install": "安装",
        "update": "更新",
        "current": "已是最新",
        "unknown": "重新检查",
    }
    hints = {
        "install": f"PyPI 可安装 {plan.remote_version}",
        "update": f"PyPI 新版本 {plan.remote_version}",
        "current": f"PyPI {plan.remote_version}，无需更新",
        "unknown": plan.reason or "暂时无法读取 PyPI 版本",
    }
    return {
        "remoteVersion": plan.remote_version,
        "updateStatus": plan.status,
        "updateHint": hints[plan.status],
        "actionLabel": labels[plan.status],
        "actionEnabled": plan.action_allowed or plan.status == "unknown",
        "actionKind": "recheck" if plan.status == "unknown" else "execute",
    }


def check_update_target(target: str) -> dict:
    """Check exactly one remote-backed component and return normalized UI fields."""
    if target == "uosc":
        from zhibo.mpv_ui import check_uosc_update, uosc_plan_ui

        return uosc_plan_ui(check_uosc_update())
    if target in {"mpv", "ffmpeg"}:
        from zhibo.tool_runtime import check_tool_update, tool_plan_ui

        plan = check_tool_update(target)
        fields = tool_plan_ui(plan)
        fields.update(version=plan.installed_version, installed=plan.installed_version != "未安装")
        return fields

    definition = next(
        (
            item
            for item in UPDATE_TARGETS
            if item["value"] == target and item.get("distribution")
        ),
        None,
    )
    if definition is None:
        raise ValueError(f"该组件不支持远端版本检查：{target}")
    plan = check_package_update(target, str(definition["distribution"]))
    fields = package_plan_ui(plan)
    fields.update(version=plan.installed_version, installed=plan.installed_version != "未安装")
    return fields


def update_history_path() -> Path:
    return user_data_dir() / "update-history.json"


def installed_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "未安装"


def _command_version(executable: str | None, *, missing: str) -> tuple[str, str]:
    if not executable:
        return missing, "missing"
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        first_line = (result.stdout or result.stderr or "").splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        first_line = ""
    return (first_line or Path(executable).name), "ok"


def runtime_environment() -> list[dict]:
    """Return concise runtime/dependency health for the update center."""
    mpv = tool_version("mpv")
    ffmpeg = tool_version("ffmpeg")
    mpv_tone = "missing" if mpv == "未安装" else "ok"
    ffmpeg_tone = "missing" if ffmpeg == "未安装" else "ok"
    installed = [installed_version(name) for name in ("streamlink", "streamget", "yt-dlp")]
    available = sum(version != "未安装" for version in installed)
    return [
        {
            "label": "Python",
            "value": platform.python_version(),
            "detail": f"{platform.system()} · {platform.machine()}",
            "tone": "ok",
        },
        {
            "label": "MPV 播放器",
            "value": mpv,
            "detail": "可以直接播放直播流" if mpv_tone == "ok" else "播放功能不可用，请安装 MPV",
            "tone": mpv_tone,
        },
        {
            "label": "FFmpeg",
            "value": ffmpeg,
            "detail": "支持下载合并" if ffmpeg_tone == "ok" else "最高画质下载合并受限",
            "tone": ffmpeg_tone,
        },
        {
            "label": "解析组件",
            "value": f"{available}/3 可用",
            "detail": "streamlink · streamget · yt-dlp",
            "tone": "ok" if available == 3 else "warning",
        },
    ]


def _load_history(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _display_time(value: object) -> str:
    if not value:
        return "无程序内更新记录"
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "记录格式异常"


def update_items(path: Path | None = None) -> list[dict]:
    history = _load_history(path or update_history_path())
    items: list[dict] = [
        {
            "value": "python",
            "label": "Python",
            "description": "程序当前使用的 Python 运行环境。为避免破坏虚拟环境，不在运行中自动升级解释器。",
            "kind": "runtime",
            "version": platform.python_version(),
            "installed": True,
            "actionLabel": "已安装",
            "actionEnabled": False,
            "restartRequired": False,
            "lastUpdated": "由程序运行环境管理",
            "lastVersion": "",
            "source": f"{platform.system()} · {platform.machine()}",
        }
    ]
    for tool, label, description in (
        ("mpv", "MPV 播放器", "直播流播放组件；自动安装到当前用户的 Zhibo 私有工具目录。"),
        ("ffmpeg", "FFmpeg", "视频与音频合并组件；安装后 yt-dlp 可下载并合并最高画质。"),
    ):
        version = tool_version(tool)
        installed = version != "未安装"
        target_history = history.get(tool, {})
        if not isinstance(target_history, dict):
            target_history = {}
        items.append(
            {
                "value": tool,
                "label": label,
                "description": description,
                "kind": "tool",
                "version": version,
                "installed": installed,
                "actionLabel": "检查更新" if installed else "未安装",
                "actionEnabled": os.name == "nt",
                "actionKind": "check",
                "updateStatus": "unchecked",
                "updateHint": "尚未检查远端版本" if installed else "未安装，点击后检查可用安装包",
                "remoteVersion": "",
                "downloadSize": "",
                "restartRequired": False,
                "lastUpdated": _display_time(target_history.get("updated_at")),
                "lastVersion": str(target_history.get("version") or ""),
                "source": "Zhibo 便携版" if managed_executable(tool) else ("系统已安装" if find_tool(tool) else "可自动安装"),
            }
        )
    from zhibo.mpv_ui import uosc_installed_version

    uosc_version = uosc_installed_version()
    uosc_installed = uosc_version not in {"未安装", "安装不完整"}
    target_history = history.get("uosc", {})
    if not isinstance(target_history, dict):
        target_history = {}
    items.append(
        {
            "value": "uosc",
            "label": "uosc 播放界面",
            "description": "MPV 的轻量现代控制界面，提供时间轴、音量、轨道菜单和右键搜索菜单。",
            "kind": "tool",
            "version": uosc_version,
            "installed": uosc_installed,
            "actionLabel": "检查更新" if uosc_installed else "需要修复" if uosc_version == "安装不完整" else "未安装",
            "actionEnabled": True,
            "actionKind": "check",
            "updateStatus": "unchecked",
            "updateHint": "尚未检查远端版本" if uosc_installed else "点击后检查 uosc 安装包",
            "remoteVersion": "",
            "downloadSize": "",
            "restartRequired": False,
            "lastUpdated": _display_time(target_history.get("updated_at")),
            "lastVersion": str(target_history.get("version") or ""),
            "source": "Zhibo 私有 MPV 配置",
        }
    )
    for definition in UPDATE_TARGETS:
        item = dict(definition)
        target_history = history.get(item["value"], {})
        if not isinstance(target_history, dict):
            target_history = {}
        distribution = item.pop("distribution", "")
        if distribution:
            version = installed_version(distribution)
        elif item["value"] == "fs1":
            version = "内置配置适配器"
        else:
            version = "本地凭据"
        is_remote_package = bool(distribution)
        item.update(
            version=version,
            installed=version != "未安装",
            actionLabel=(
                ("检查更新" if version != "未安装" else "未安装") if is_remote_package
                else "更新配置" if item["value"] == "fs1"
                else "更新凭据"
            ),
            actionEnabled=True,
            actionKind="check" if is_remote_package else "execute",
            updateStatus="unchecked" if is_remote_package else "local",
            updateHint=(
                "尚未检查 PyPI 版本" if is_remote_package and version != "未安装"
                else "未安装，点击后检查 PyPI 可用版本" if is_remote_package
                else ""
            ),
            remoteVersion="",
            lastUpdated=_display_time(target_history.get("updated_at")),
            lastVersion=str(target_history.get("version") or ""),
            source=(
                "本地配置" if item["value"] == "fs1"
                else "本地私有凭据" if item["value"] == "bilibili_cookie"
                else "当前 Python 环境"
            ),
        )
        items.append(item)
    return items


def record_successful_update(target: str, version: str = "", path: Path | None = None) -> None:
    destination = path or update_history_path()
    history = _load_history(destination)
    history[target] = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": str(version or ""),
    }
    ensure_private_parent(destination)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(history, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
