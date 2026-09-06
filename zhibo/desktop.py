"""Desktop integration helper: mpv playback.

托盘、通知和窗口管理已由 Qt 前端（``qt_quick``）原生实现，
这里只保留启动 mpv 播放进程的逻辑。
"""
from __future__ import annotations

import subprocess

from zhibo.tool_runtime import find_tool
from zhibo.mpv_ui import active_uosc_config_dir


def is_mpv_available() -> bool:
    return find_tool("mpv") is not None


def play_url(
    stream_url: str,
    title: str = "",
    referrer: str = "",
    headers: dict[str, str] | None = None,
    proxy_url: str = "",
    use_cache: bool = False,
) -> subprocess.Popen:
    """Play a stream with mpv."""
    mpv_executable = find_tool("mpv")
    if not mpv_executable:
        raise RuntimeError("未找到 mpv，请在更新中心自动安装 MPV 播放器")
    cmd = [mpv_executable]
    uosc_config = active_uosc_config_dir()
    if uosc_config is not None:
        cmd.append(f"--config-dir={uosc_config}")
    cmd.append(stream_url)
    if use_cache:
        cmd.extend([
            "--cache=yes",
            "--demuxer-max-bytes=268435456",
            "--demuxer-readahead-secs=15",
        ])
    else:
        cmd.append("--no-cache")
    cmd.extend([
        "--stream-lavf-o=reconnect=1",
        "--stream-lavf-o=reconnect_streamed=1",
    ])
    if title:
        cmd.append(f"--title={title}")

    if proxy_url:
        cmd.append(f"--http-proxy={proxy_url}")

    headers = headers or {}
    user_agent = headers.get("User-Agent")
    if user_agent:
        cmd.append(f"--user-agent={user_agent}")

    referrer = referrer or headers.get("Referer", "")
    if referrer:
        cmd.append(f"--referrer={referrer}")

    for key in ("Origin", "Accept"):
        value = headers.get(key)
        if value:
            cmd.append(f"--http-header-fields={key}: {value}")

    try:
        return subprocess.Popen(cmd)
    except FileNotFoundError:
        raise RuntimeError("未找到 mpv，请先安装 mpv 播放器：https://mpv.io/installation/") from None
