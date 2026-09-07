"""Desktop integration helper: mpv playback.

托盘、通知和窗口管理已由 Qt 前端（``qt_quick``）原生实现，
这里只保留启动 mpv 播放进程的逻辑。
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid

from zhibo.tool_runtime import find_tool
from zhibo.mpv_ui import active_uosc_config_dir


def is_mpv_available() -> bool:
    return find_tool("mpv") is not None


# 把每个 mpv 进程挂到"进程关闭即全部结束"的 Job Object 上：应用无论
# 正常退出还是崩溃，操作系统都会回收它启动的 mpv，不再留孤儿进程。
_JOB_HANDLE = None


def _assign_to_kill_on_close_job(process: subprocess.Popen) -> None:
    global _JOB_HANDLE
    if os.name != "nt":
        return
    try:
        import ctypes

        if _JOB_HANDLE is None:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in
                            ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                             "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.POINTER(ctypes.c_ulonglong)),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32),
                ]
            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryLimit", ctypes.c_size_t),
                    ("JobPeakMemoryLimit", ctypes.c_size_t),
                ]

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                ctypes.windll.kernel32.CloseHandle(handle)
                return
            _JOB_HANDLE = handle
        ctypes.windll.kernel32.AssignProcessToJobObject(
            _JOB_HANDLE, int(process._handle)
        )
    except Exception:
        # Job Object 只是防孤儿保险；失败时保持原行为即可。
        pass


def new_mpv_ipc_path() -> str:
    """为一次 mpv 启动生成独立的命名管道 IPC 地址。"""
    return f"\\\\.\\pipe\\zhibo-mpv-{uuid.uuid4().hex[:8]}"


def mpv_command(ipc_path: str, *command: str) -> bool:
    """向 mpv 的 JSON IPC 管道发送一条命令；管道不可用则静默失败。"""
    if not ipc_path:
        return False
    try:
        with open(ipc_path, "a", encoding="utf-8") as pipe:
            pipe.write(json.dumps({"command": list(command)}) + "\n")
        return True
    except (OSError, ValueError):
        return False


def play_url(
    stream_url: str,
    title: str = "",
    referrer: str = "",
    headers: dict[str, str] | None = None,
    proxy_url: str = "",
    use_cache: bool = False,
    ipc_path: str = "",
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
    if ipc_path:
        cmd.append(f"--input-ipc-server={ipc_path}")
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
        process = subprocess.Popen(cmd)
    except FileNotFoundError:
        raise RuntimeError("未找到 mpv，请先安装 mpv 播放器：https://mpv.io/installation/") from None
    _assign_to_kill_on_close_job(process)
    return process
