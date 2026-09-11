"""Discover, check, and install user-local media tools used by Zhibo."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from zhibo.private_data import user_data_dir


ProgressCallback = Callable[[float, str], None]
MIB = 1024 * 1024
USER_AGENT = "Zhibo-Updater/2"

TOOL_RELEASES = {
    "mpv": {
        "repo": "zhongfly/mpv-winbuild",
        # Only the standard x64 player. This deliberately excludes v3, debug,
        # libmpv, development files, and bundled FFmpeg assets.
        "asset": re.compile(r"^mpv-x86_64-\d{8}-git-[0-9a-f]+\.7z$", re.IGNORECASE),
        "archive": "7z",
        "max_size": 80 * MIB,
    },
    "ffmpeg": {
        # Gyan's essentials 7z contains ffmpeg/ffprobe and the common codecs
        # needed by this application, without the much larger full GPL build.
        "asset_name": "ffmpeg-release-essentials.7z",
        "asset_url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.7z",
        "version_url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.7z.ver",
        "checksum_url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.7z.sha256",
        "archive": "7z",
        "max_size": 64 * MIB,
    },
}


@dataclass(frozen=True)
class ToolUpdatePlan:
    name: str
    installed_version: str
    remote_version: str
    status: str
    asset: dict | None = None
    reason: str = ""

    @property
    def action_allowed(self) -> bool:
        return self.status in {"install", "update", "migrate", "repair"} and self.asset is not None

    @property
    def download_size(self) -> int:
        return int((self.asset or {}).get("size") or 0)


def managed_tools_dir() -> Path:
    return user_data_dir() / "tools"


def managed_executable(name: str) -> Path | None:
    target = managed_tools_dir() / name.casefold()
    if not target.exists():
        return None
    executable_name = f"{name.casefold()}.exe"
    candidates = sorted(
        (path for path in target.rglob(executable_name) if path.is_file()),
        key=lambda path: (len(path.parts), len(str(path))),
    )
    return candidates[0] if candidates else None


def find_tool(name: str) -> str | None:
    key = name.strip().casefold()
    override = os.environ.get(f"ZHIBO_{key.upper()}_PATH", "").strip()
    if override and Path(override).is_file():
        return str(Path(override))
    managed = managed_executable(key)
    if managed is not None:
        return str(managed)
    return shutil.which(key) or shutil.which(f"{key}.exe") or shutil.which(f"{key}.com")


def tool_version(name: str) -> str:
    executable = find_tool(name)
    if not executable:
        return "未安装"
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        line = (result.stdout or result.stderr or "").splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        line = ""
    if name.casefold() == "mpv":
        return line.removeprefix("mpv ").split(" Copyright", 1)[0] or Path(executable).name
    if name.casefold() == "ffmpeg":
        return line.split(" Copyright", 1)[0] or Path(executable).name
    return line or Path(executable).name


def seven_zip_available() -> bool:
    if shutil.which("7z") or shutil.which("7za"):
        return True
    return importlib.util.find_spec("py7zr") is not None


def _request(url: str, *, method: str = "GET", accept: str = "*/*") -> urllib.request.Request:
    return urllib.request.Request(
        url,
        method=method,
        headers={"Accept": accept, "User-Agent": USER_AGENT},
    )


def _read_json(url: str) -> dict:
    with urllib.request.urlopen(_request(url, accept="application/vnd.github+json"), timeout=20) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("远端返回了无效的发布信息")
    return value


def _read_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), timeout=20) as response:
        return response.read().decode("utf-8", errors="replace").strip()


def _remote_size(url: str) -> int:
    with urllib.request.urlopen(_request(url, method="HEAD"), timeout=20) as response:
        return int(response.headers.get("Content-Length") or 0)


def _format_size(size: int) -> str:
    return f"{size / MIB:.1f} MB" if size else "大小未知"


def _is_valid_sha256_digest(value: object) -> bool:
    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(value or ""), re.IGNORECASE))


def _version_tuple(value: str) -> tuple[int, ...] | None:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)", value)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _mpv_ref(value: str) -> str | None:
    commit = re.search(r"-g([0-9a-f]{7,40})(?:\b|$)", value, re.IGNORECASE)
    if commit:
        return commit.group(1)
    version = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?)\b", value)
    return f"v{version.group(1)}" if version else None


def _unknown_plan(name: str, installed: str, reason: str) -> ToolUpdatePlan:
    return ToolUpdatePlan(name, installed, "无法确认", "unknown", reason=reason)


def _check_ffmpeg(installed: str) -> ToolUpdatePlan:
    definition = TOOL_RELEASES["ffmpeg"]
    remote_text = _read_text(str(definition["version_url"]))
    remote_match = re.search(r"\d+(?:\.\d+){1,3}", remote_text)
    if not remote_match:
        return _unknown_plan("ffmpeg", installed, "远端 FFmpeg 版本格式无法识别")
    remote = remote_match.group(0)
    checksum_text = _read_text(str(definition["checksum_url"]))
    checksum_match = re.search(r"\b[0-9a-f]{64}\b", checksum_text, re.IGNORECASE)
    if not checksum_match:
        return _unknown_plan("ffmpeg", installed, "无法取得 FFmpeg 安装包校验值")
    size = _remote_size(str(definition["asset_url"]))
    if size <= 0:
        return _unknown_plan("ffmpeg", installed, "无法确认 FFmpeg 安装包大小")
    if size > int(definition["max_size"]):
        return _unknown_plan("ffmpeg", installed, f"精简包体积异常（{_format_size(size)}），已停止下载")
    asset = {
        "name": definition["asset_name"],
        "browser_download_url": definition["asset_url"],
        "size": size,
        "digest": f"sha256:{checksum_match.group(0).casefold()}",
    }
    if installed == "未安装":
        return ToolUpdatePlan("ffmpeg", installed, remote, "install", asset)
    current = _version_tuple(installed)
    latest = _version_tuple(remote)
    if current is None:
        # Older Zhibo versions installed BtbN's rolling N-* full build. It has
        # no semantic version comparable with Gyan's small stable release.
        # This is a channel migration, not an assertion that 8.x is newer.
        if re.search(r"\bN-\d+", installed, re.IGNORECASE):
            return ToolUpdatePlan(
                "ffmpeg",
                installed,
                remote,
                "migrate",
                asset,
                "当前为旧版开发快照；可切换为体积更小、版本明确的 essentials 稳定版",
            )
        return ToolUpdatePlan(
            "ffmpeg",
            installed,
            remote,
            "repair",
            asset,
            "当前 FFmpeg 无法报告标准版本；可重新安装经过校验的 essentials 版本",
        )
    if latest is None:
        return _unknown_plan("ffmpeg", installed, "远端 FFmpeg 版本无法可靠比较")
    status = "update" if current < latest else "current"
    return ToolUpdatePlan("ffmpeg", installed, remote, status, asset)


def _mpv_remote_commit(release: dict, asset: dict) -> str | None:
    body = str(release.get("body") or "")
    match = re.search(r"mpv-player/mpv/(?:commit|tree)/([0-9a-f]{7,40})", body, re.IGNORECASE)
    if not match:
        match = re.search(r"-git-([0-9a-f]{7,40})\.7z$", str(asset.get("name") or ""), re.IGNORECASE)
    return match.group(1) if match else None


def _check_mpv(installed: str) -> ToolUpdatePlan:
    definition = TOOL_RELEASES["mpv"]
    release = _read_json(f"https://api.github.com/repos/{definition['repo']}/releases/latest")
    asset = next(
        (
            candidate
            for candidate in release.get("assets") or []
            if definition["asset"].fullmatch(str(candidate.get("name") or ""))
        ),
        None,
    )
    if not isinstance(asset, dict):
        return _unknown_plan("mpv", installed, "发布页中没有标准 x64 MPV 安装包")
    size = int(asset.get("size") or 0)
    if size <= 0 or size > int(definition["max_size"]):
        return _unknown_plan("mpv", installed, f"MPV 安装包体积异常（{_format_size(size)}）")
    if not _is_valid_sha256_digest(asset.get("digest")):
        return _unknown_plan("mpv", installed, "MPV 安装包缺少 SHA-256 校验值")
    remote_commit = _mpv_remote_commit(release, asset)
    if not remote_commit:
        return _unknown_plan("mpv", installed, "远端 MPV 提交版本无法识别")
    name_match = re.search(r"mpv-x86_64-(\d{4})(\d{2})(\d{2})-git-", str(asset["name"]), re.IGNORECASE)
    date = "-".join(name_match.groups()) if name_match else str(release.get("tag_name") or "")
    remote = f"git {remote_commit[:10]}" + (f" · {date}" if date else "")
    if installed == "未安装":
        return ToolUpdatePlan("mpv", installed, remote, "install", dict(asset))
    current_ref = _mpv_ref(installed)
    if not current_ref:
        return _unknown_plan("mpv", installed, "当前 MPV 版本无法可靠比较")
    if re.fullmatch(r"[0-9a-f]{7,40}", current_ref, re.IGNORECASE) and (
        remote_commit.casefold().startswith(current_ref.casefold())
        or current_ref.casefold().startswith(remote_commit.casefold())
    ):
        return ToolUpdatePlan("mpv", installed, remote, "current", dict(asset))
    base = urllib.parse.quote(current_ref, safe="")
    head = urllib.parse.quote(remote_commit, safe="")
    comparison = _read_json(f"https://api.github.com/repos/mpv-player/mpv/compare/{base}...{head}")
    compare_status = str(comparison.get("status") or "").casefold()
    if compare_status == "ahead":
        status = "update"
    elif compare_status in {"identical", "behind"}:
        status = "current"
    else:
        return _unknown_plan("mpv", installed, "当前与远端 MPV 版本无法确定先后关系")
    return ToolUpdatePlan("mpv", installed, remote, status, dict(asset))


def check_tool_update(name: str) -> ToolUpdatePlan:
    """Check the remote version and package metadata without downloading it."""
    key = name.strip().casefold()
    if key not in TOOL_RELEASES:
        raise ValueError(f"不支持自动安装的工具：{name}")
    installed = tool_version(key)
    try:
        return _check_mpv(installed) if key == "mpv" else _check_ffmpeg(installed)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        return _unknown_plan(key, installed, f"检查更新失败：{exc}")


def tool_plan_ui(plan: ToolUpdatePlan) -> dict:
    labels = {
        "install": "安装",
        "update": "更新",
        "migrate": "换用精简版",
        "repair": "修复安装",
        "current": "已是最新",
        "unknown": "重新检查",
    }
    hints = {
        "install": f"可安装 {plan.remote_version}",
        "update": f"发现新版本 {plan.remote_version}",
        "migrate": plan.reason,
        "repair": plan.reason,
        "current": f"远端 {plan.remote_version}，无需下载",
        "unknown": plan.reason or "暂时无法读取远端版本，可重新检查",
    }
    return {
        "remoteVersion": plan.remote_version,
        "downloadSize": _format_size(plan.download_size) if plan.download_size else "",
        "updateStatus": plan.status,
        "updateHint": hints[plan.status],
        "actionLabel": labels[plan.status],
        "actionEnabled": os.name == "nt" and (plan.action_allowed or plan.status == "unknown"),
        "actionKind": "recheck" if plan.status == "unknown" else "execute",
    }


def _download(asset: dict, destination: Path, progress: ProgressCallback, *, max_size: int) -> None:
    expected_size = int(asset.get("size") or 0)
    if expected_size <= 0 or expected_size > max_size:
        raise RuntimeError("安装包大小未经确认或超过安全上限，已停止下载")
    expected_digest = str(asset.get("digest") or "")
    if not _is_valid_sha256_digest(expected_digest):
        raise RuntimeError("安装包缺少有效的 SHA-256 校验值，已停止下载")
    request = _request(str(asset["browser_download_url"]), accept="application/octet-stream")
    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or expected_size)
        if total > max_size:
            raise RuntimeError("服务器返回的安装包超过安全上限，已停止下载")
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            received += len(chunk)
            if received > max_size:
                raise RuntimeError("下载内容超过安全上限，已停止下载")
            output.write(chunk)
            digest.update(chunk)
            progress(12 + (received / total * 58), f"正在下载 {asset['name']} · {received / MIB:.1f} MB")
    if received != expected_size:
        raise RuntimeError(f"安装包大小不符（预期 {_format_size(expected_size)}，实际 {_format_size(received)}）")
    if digest.hexdigest().casefold() != expected_digest[7:].casefold():
        raise RuntimeError("安装包 SHA-256 校验失败，已停止安装")


def _validate_archive_member_name(name: object) -> None:
    """Reject absolute, parent-traversal, and malformed archive members."""
    normalized = str(name).replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or re.match(r"^[a-z]:($|/)", normalized, re.IGNORECASE)
        or any(part == ".." for part in PurePosixPath(normalized).parts)
    ):
        raise RuntimeError("安装包包含不安全路径，已停止安装")


def _validate_archive_members(names) -> None:
    for name in names:
        _validate_archive_member_name(name)


def _validate_extracted_tree(destination: Path) -> None:
    """Ensure extraction did not create links or paths outside the target."""
    root = destination.resolve()
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("安装包包含不安全链接，已停止安装")
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            raise RuntimeError("安装包包含不安全路径，已停止安装") from None


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as handle:
        root = destination.resolve()
        for member in handle.infolist():
            _validate_archive_member_name(member.filename)
            candidate = (destination / member.filename).resolve()
            if candidate != root and root not in candidate.parents:
                raise RuntimeError("安装包包含不安全路径，已停止安装")
        handle.extractall(destination)
    _validate_extracted_tree(destination)


def _list_7z_members(executable: str, archive: Path) -> list[str]:
    result = subprocess.run(
        [executable, "l", "-slt", str(archive)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError(f"7-Zip 无法读取安装包目录，退出码={result.returncode}")

    members: list[str] = []
    archive_header_seen = False
    for line in result.stdout.splitlines():
        if not line.startswith("Path = "):
            continue
        name = line[7:].strip()
        if not archive_header_seen:
            archive_header_seen = True
            continue
        members.append(name)
    if not archive_header_seen or not members:
        raise RuntimeError("7-Zip 安装包目录为空或格式无法识别")
    _validate_archive_members(members)
    return members


def _extract_7z(archive: Path, destination: Path) -> None:
    executable = shutil.which("7z") or shutil.which("7za")
    if executable:
        _list_7z_members(executable, archive)
        result = subprocess.run(
            [executable, "x", str(archive), f"-o{destination}", "-y"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(f"7-Zip 解压失败，退出码={result.returncode}")
        _validate_extracted_tree(destination)
        return
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError("缺少 7-Zip 解压支持") from exc
    with py7zr.SevenZipFile(archive, mode="r") as handle:
        _validate_archive_members(handle.getnames())
        handle.extractall(path=destination)
    _validate_extracted_tree(destination)


def install_portable_tool(
    name: str,
    progress: ProgressCallback | None = None,
    *,
    plan: ToolUpdatePlan | None = None,
) -> str:
    """Verify an update plan, then download and atomically install the tool."""
    key = name.strip().casefold()
    if os.name != "nt":
        raise RuntimeError("MPV/FFmpeg 自动安装目前仅支持 Windows")
    if key not in TOOL_RELEASES:
        raise ValueError(f"不支持自动安装的工具：{name}")
    checked = plan or check_tool_update(key)
    if checked.name != key or not checked.action_allowed:
        detail = checked.reason or ("当前已是最新版本" if checked.status == "current" else "更新状态无效")
        raise RuntimeError(f"未通过下载前检查：{detail}")
    asset = checked.asset
    assert asset is not None
    max_size = int(TOOL_RELEASES[key]["max_size"])
    emit = progress or (lambda _value, _message: None)
    emit(
        8,
        f"已确认：当前 {checked.installed_version} → 远端 {checked.remote_version}，安装包 {_format_size(checked.download_size)}",
    )
    root = managed_tools_dir()
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{key}-install-", dir=root))
    content = temporary / "content"
    content.mkdir()
    target = root / key
    backup = root / f".{key}-previous"
    try:
        archive = temporary / str(asset["name"])
        _download(asset, archive, emit, max_size=max_size)
        emit(74, f"正在解压 {key}…")
        if TOOL_RELEASES[key]["archive"] == "zip":
            _safe_extract_zip(archive, content)
        else:
            _extract_7z(archive, content)
        executable = next(content.rglob(f"{key}.exe"), None)
        if executable is None:
            raise RuntimeError(f"安装包中没有找到 {key}.exe")
        emit(90, f"正在启用 {key}…")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        try:
            if target.exists():
                os.replace(target, backup)
        except PermissionError as exc:
            raise RuntimeError(
                f"无法替换 {key}：文件正被其他程序占用。"
                "请关闭正在使用该工具的程序（例如 mpv 播放窗口）后重试。"
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
        version = tool_version(key)
        emit(100, f"{key} {version} 已安装")
        return version
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def sweep_install_leftovers() -> list[str]:
    """清理上次安装中断留下的临时目录并自愈半完成的迁移。

    正常安装结束后 ``.{tool}-previous`` 与 ``.{tool}-install-*`` 都不存在。
    若目标目录缺失而备份还在，说明更新在两次替换之间被打断：先把备份
    恢复为目标，再删除所有残留。全部操作尽力而为，失败不影响启动。
    """
    cleaned: list[str] = []
    root = managed_tools_dir()
    if not root.exists():
        return cleaned
    for key in TOOL_RELEASES:
        target = root / key
        backup = root / f".{key}-previous"
        try:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
                cleaned.append(f"已恢复 {key} 的上次备份")
            for leftover in root.glob(f".{key}-install-*"):
                shutil.rmtree(leftover, ignore_errors=True)
                cleaned.append(f"已清理残留 {leftover.name}")
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
                cleaned.append(f"已清理残留 {backup.name}")
        except OSError:
            continue
    return cleaned
