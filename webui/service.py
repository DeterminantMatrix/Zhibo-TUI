"""Web 前端的监控服务线程 — 复用 zhibo.monitor，推送快照到 JS。

结构与 qt_quick/worker.py 同源：后台 asyncio 线程跑 MonitorService，
请求经 schedule() 投递；区别是结果通过 EventPusher 推给网页，
并维护一份线程安全的最新快照供 getSnapshot() 同步读取。
"""
from __future__ import annotations

import asyncio
import copy
import datetime
import json
import re
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable

from zhibo.app_logging import get_logger, redact_sensitive_text
from zhibo.config import follower_key, follower_to_edit_payload, preview_follower_edit
from zhibo.detail import build_detail_view
from zhibo.desktop import mpv_command, new_mpv_ipc_path, play_url
from zhibo.import_preview import ImportPreviewService
from zhibo.monitor import FollowerStatus, MonitorService, StatusHistoryEntry
from zhibo.plugins import get_plugin, list_plugins
from zhibo.plugins.base import stream_candidate_urls
from zhibo.plugins.bounded_executor import shutdown_plugin_workers
from zhibo.proxy_config import (
    PROXY_PLATFORMS,
    normalize_proxy_value,
    proxy_for_platform,
    set_platform_proxies,
)
from zhibo.quality_options import normalize_quality_choice, quality_for_plugin, quality_options
from zhibo.viewmodel import (
    EDIT_LABELS,
    SETTINGS_LABELS,
    format_changes,
    format_import_preview,
    status_snapshot,
    web_url_for_snapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class WebMonitorService:
    def __init__(self, pusher, config_path: str | None = None) -> None:
        self._pusher = pusher
        self._config_path = config_path
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._service: MonitorService | None = None
        self._run_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._ready = threading.Event()
        self._snapshot_lock = threading.Lock()
        self._last_snapshot: dict = {}
        self._pending: dict = {}
        self._logger = get_logger("zhibo.webui")
        # 开播通知钩子（main.py 注入 Notifier.notify_live；测试注入收集器）。
        self.notify_hook = None
        # 播放器状态：只在监控循环线程内访问（与 Qt 版相同的候选/代数机制）。
        self._player_process: subprocess.Popen | None = None
        self._player_ipc = ""
        self._player_generation = 0
        self._player_candidates: list[str] = []
        self._player_candidate_index = 0
        self._player_payload: dict = {}
        self._playing_idx = -1
        # 更新中心的 pip 子进程（用于退出时回收）。
        self._update_process: asyncio.subprocess.Process | None = None

    # ---- 生命周期 -------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._thread_main, name="zhibo-webui-monitor", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=15)

    def stop(self, timeout: float = 5.0) -> None:
        loop = self._loop
        service = self._service
        if loop and loop.is_running() and service:
            def cancel() -> None:
                service.stop()
                if self._run_task and not self._run_task.done():
                    self._run_task.cancel()
                for task in tuple(self._tasks):
                    if not task.done():
                        task.cancel()

            loop.call_soon_threadsafe(cancel)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except SystemExit as exc:
            message = str(exc.code) if exc.code not in (None, 0) else "监控线程请求退出"
            self._pusher.submit("fatal", {"message": redact_sensitive_text(message)})
        except Exception as exc:
            self._pusher.submit("fatal", {"message": redact_sensitive_text(str(exc) or "监控线程启动失败")})
        finally:
            self._loop = None
            self._service = None
            self._pusher.submit("stopped", {})

    async def _run(self) -> None:
        from zhibo.plugins.bounded_executor import shutdown_plugin_workers

        self._loop = asyncio.get_running_loop()
        service = MonitorService(self._config_path)
        self._service = service
        service.on_status_change(self._on_status_change)
        service.on_error(self._on_error)
        service.on_poll_start(self._on_poll_start)
        service.on_poll_end(self._on_poll_end)
        self._emit_snapshot()
        self._pusher.submit("log", {"text": f"Web 监控已启动，共 {len(service.followers)} 个关注项"})
        self._ready.set()
        try:
            self._run_task = asyncio.create_task(service.run(), name="zhibo-webui-monitor-loop")
            await self._run_task
        except asyncio.CancelledError:
            pass
        finally:
            for task in tuple(self._tasks):
                task.cancel()
            if self._tasks:
                await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
            await service.shutdown(timeout=1.5)
            await asyncio.to_thread(shutdown_plugin_workers, True)
            self._terminate_player_on_exit()

    def _terminate_player_on_exit(self) -> None:
        """应用退出时回收 mpv（Job Object 兜底，这里是正常路径的体面退出）。"""
        process = self._player_process
        self._player_process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass

    # ---- P4：更新中心 ------------------------------------------------------

    def _progress(self, kind: str, value: float, text: str) -> None:
        self._pusher.submit("progress", {"kind": kind, "value": value, "text": text})

    def load_update_center(self) -> None:
        self.schedule(self._load_update_status)

    async def _load_update_status(self) -> None:
        try:
            from zhibo.update_state import update_items

            items = await asyncio.to_thread(update_items)
            self._show_dialog(
                "update",
                {
                    "stage": "form",
                    "items": items,
                    "target": "mpv",
                },
            )
            # 打开即自动检查全部组件的远端版本，免去逐个点击。
            await self._check_update_all()
        except Exception as exc:
            self._finish("update", False, f"读取更新状态失败：{exc}", {})

    def check_update(self, target: str) -> None:
        self.schedule(lambda: self._check_update_target(str(target or "")))

    async def _check_update_target(self, target: str) -> None:
        try:
            from zhibo.update_state import check_update_target

            allowed = {"mpv", "ffmpeg", "uosc", "streamlink", "streamget", "yt-dlp"}
            if target not in allowed:
                raise ValueError("该组件不支持远端版本检查")
            fields = await asyncio.to_thread(check_update_target, target)
            self._show_dialog("update", {"stage": "checked", "target": target, "item": fields})
        except Exception as exc:
            self._show_dialog(
                "update",
                {
                    "stage": "checked",
                    "target": target,
                    "item": {
                        "updateStatus": "unknown",
                        "updateHint": f"检查更新失败：{exc}",
                        "actionLabel": "重新检查",
                        "actionEnabled": True,
                        "actionKind": "recheck",
                    },
                },
            )

    def check_update_all(self) -> None:
        self.schedule(self._check_update_all)

    async def _check_update_all(self) -> None:
        from zhibo.update_state import check_update_target

        allowed = ("mpv", "ffmpeg", "uosc", "streamlink", "streamget", "yt-dlp")
        checking_item = {
            "updateStatus": "checking",
            "updateHint": "正在检查远端版本…",
            "actionLabel": "检查中",
            "actionEnabled": False,
            "actionKind": "none",
        }
        for target in allowed:
            self._show_dialog(
                "update", {"stage": "checked", "target": target, "item": dict(checking_item)}
            )

        async def check_one(target: str) -> None:
            try:
                fields = await asyncio.to_thread(check_update_target, target)
            except Exception as exc:
                fields = {
                    "updateStatus": "unknown",
                    "updateHint": f"检查更新失败：{exc}",
                    "actionLabel": "重新检查",
                    "actionEnabled": True,
                    "actionKind": "recheck",
                }
            self._show_dialog("update", {"stage": "checked", "target": target, "item": fields})

        await asyncio.gather(*(check_one(target) for target in allowed))

    def run_update(self, target: str, content: str = "") -> None:
        self.schedule(lambda: self._run_update(str(target or ""), str(content or "")))

    async def _run_update(self, target: str, content: str) -> None:
        try:
            allowed = {
                "streamlink", "streamget", "yt-dlp", "mpv", "ffmpeg", "uosc", "fs1",
                "bilibili_cookie",
            }
            if target not in allowed:
                raise ValueError("更新目标无效")
            self._show_dialog(
                "update", {"stage": "progress", "progress": 5, "progressText": "正在准备更新…"}
            )
            self._progress("update", 5.0, "正在准备更新…")
            if target == "bilibili_cookie":
                await self._update_bilibili_cookie(content)
                version = "本地凭据"
                await self._record_update(target, version)
                message = "B站 Cookie 已安全更新；后续检测会自动读取新文件"
            elif target == "fs1":
                await self._update_fs1(content)
                version = "内置配置适配器"
                await self._record_update(target, version)
                message = "FS1 配置已更新并重新载入"
            elif target == "uosc":
                from zhibo.mpv_ui import check_uosc_update

                plan = await asyncio.to_thread(check_uosc_update)
                if plan.status == "current":
                    message = f"uosc 已是最新版本（{plan.installed_version}），没有下载安装包"
                    self._progress("update", 100.0, message)
                    self._finish(
                        "update",
                        True,
                        message,
                        {"done": True, "downloaded": False, "target": target, "version": plan.installed_version},
                    )
                    return
                if not plan.action_allowed:
                    raise RuntimeError(plan.reason or "无法确认 uosc 版本，已取消下载")
                action = "安装" if plan.status == "install" else "修复" if plan.status == "repair" else "更新"
                version = await self._install_uosc(plan)
                await self._record_update(target, version)
                message = f"uosc {action}完成：{version}；下一次启动 MPV 时自动使用新界面"
            elif target in {"mpv", "ffmpeg"}:
                from zhibo.tool_runtime import check_tool_update

                plan = await asyncio.to_thread(check_tool_update, target)
                if plan.status == "current":
                    label = "MPV 播放器" if target == "mpv" else "FFmpeg"
                    message = f"{label} 已是最新版本（{plan.installed_version}），没有下载安装包"
                    self._progress("update", 100.0, message)
                    self._finish(
                        "update",
                        True,
                        message,
                        {"done": True, "downloaded": False, "target": target, "version": plan.installed_version},
                    )
                    return
                if not plan.action_allowed:
                    raise RuntimeError(plan.reason or "无法可靠判断当前与远端版本，已取消下载")
                action = {
                    "install": "安装",
                    "update": "更新",
                    "migrate": "切换精简版",
                    "repair": "修复安装",
                }[plan.status]
                version = await self._install_tool(target, plan)
                await self._record_update(target, version)
                label = "MPV 播放器" if target == "mpv" else "FFmpeg"
                message = f"{label} {action}完成：{version}；后续操作将自动使用新版本"
            else:
                from zhibo.update_state import check_package_update, installed_version

                plan = await asyncio.to_thread(check_package_update, target, target)
                if plan.status == "current":
                    message = f"{target} 已是最新版本（{plan.installed_version}），没有执行 pip 下载"
                    self._progress("update", 100.0, message)
                    self._finish(
                        "update",
                        True,
                        message,
                        {"done": True, "downloaded": False, "target": target, "version": plan.installed_version},
                    )
                    return
                if not plan.action_allowed:
                    raise RuntimeError(plan.reason or f"无法确认 {target} 的远端版本，已取消更新")
                action = "安装" if plan.status == "install" else "更新"
                await self._update_package(target)
                version = installed_version(target)
                await self._record_update(target, version)
                message = f"{target} {action}完成：{version}；请重启程序后使用新版本"
            self._progress("update", 100.0, message)
            self._finish(
                "update",
                True,
                message,
                {"done": True, "downloaded": True, "target": target, "version": version},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._finish("update", False, str(exc), {})

    async def _record_update(self, target: str, version: str) -> None:
        from zhibo.update_state import record_successful_update

        try:
            await asyncio.to_thread(record_successful_update, target, version)
        except Exception as exc:
            self._log(f"更新已完成，但更新时间记录失败：{redact_sensitive_text(str(exc))}")

    async def _update_bilibili_cookie(self, content: str) -> None:
        from zhibo.bilibili_cookie import update_bilibili_cookies

        if not content.strip():
            raise ValueError("请粘贴 Netscape cookies.txt 内容")
        self._progress("update", 25.0, "正在校验 B站 Cookie…")
        result = await asyncio.to_thread(update_bilibili_cookies, content)
        self._log(
            f"B站 Cookie：更新 {result.bilibili_records_updated} 条，"
            f"保留其他站点 {result.non_bilibili_records_preserved} 条"
        )

    async def _update_fs1(self, content: str) -> None:
        from zhibo.plugins import get_plugin
        from zhibo.plugins.fs1_plugin import update_from_text

        if not content.strip():
            raise ValueError("请粘贴 FS /v1/room 请求 curl")
        self._progress("update", 25.0, "正在解析并更新 FS1 配置…")
        applied = await asyncio.to_thread(update_from_text, content)
        plugin = get_plugin("fs1")
        if plugin is not None and callable(getattr(plugin, "reload_config", None)):
            plugin.reload_config()
        service = self._require_service()
        service.reset_platform_health("fs1")
        self._emit_snapshot()
        for key in sorted(applied):
            value = "***" if key in {"token", "authorization", "cookie", "imei", "dun_imei"} else applied[key]
            self._log(f"FS1 {key}: {redact_sensitive_text(value)}")

    async def _update_package(self, package: str) -> None:
        if getattr(sys, "frozen", False):
            raise RuntimeError("打包版不能在程序内更新 Python 组件，请下载新版程序覆盖安装")
        self._progress("update", 15.0, f"正在更新 {package}…")
        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
            "cwd": str(PROJECT_ROOT),
        }
        if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--upgrade-strategy",
            "only-if-needed",
            package,
            **kwargs,
        )
        self._update_process = process
        try:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text = redact_sensitive_text(line.decode("utf-8", errors="replace").strip())
                if text:
                    self._log(text)
            code = await process.wait()
            if code != 0:
                raise RuntimeError(f"{package} 更新失败，退出码={code}")
        finally:
            if self._update_process is process:
                self._update_process = None

    async def _install_tool(self, tool: str, plan) -> str:
        from zhibo.tool_runtime import install_portable_tool, seven_zip_available

        if not seven_zip_available():
            self._progress("update", 10.0, f"正在安装 {tool.upper()} 解压支持…")
            await self._update_package("py7zr")

        def report(value: float, message: str) -> None:
            self._progress("update", value, redact_sensitive_text(message))

        return await asyncio.to_thread(install_portable_tool, tool, report, plan=plan)

    async def _install_uosc(self, plan) -> str:
        from zhibo.mpv_ui import install_uosc

        def report(value: float, message: str) -> None:
            self._progress("update", value, redact_sensitive_text(message))

        return await asyncio.to_thread(install_uosc, report, plan=plan)

    # ---- P4：视频下载 ------------------------------------------------------

    def load_download_formats(self, url: str) -> None:
        self.schedule(lambda: self._load_download_formats(str(url or "")))

    async def _load_download_formats(self, url: str) -> None:
        try:
            from zhibo.plugins import get_plugin

            plugin = get_plugin("yt_dlp")
            if plugin is None:
                raise RuntimeError("yt-dlp 插件未加载")
            self._progress("download", -1.0, "正在获取可用格式…")
            formats = await plugin.list_formats(url)
            if not formats:
                raise RuntimeError("没有找到可下载格式")
            self._pending["download"] = {"url": url, "formats": formats}
            self._show_dialog(
                "download",
                {
                    "stage": "formats",
                    "url": url,
                    "formats": [
                        {"index": index, "label": fmt.label, "formatId": fmt.format_id, "hasAudio": fmt.has_audio}
                        for index, fmt in enumerate(formats)
                    ],
                },
            )
        except Exception as exc:
            self._finish("download", False, str(exc), {})

    def start_download(self, format_index: int) -> None:
        self.schedule(lambda: self._download(int(format_index)))

    async def _download(self, format_index: int) -> None:
        try:
            from zhibo.plugins import get_plugin

            pending = self._pending.get("download")
            if not pending:
                raise ValueError("下载格式列表已失效，请重新获取")
            formats = pending["formats"]
            if format_index < 0 or format_index >= len(formats):
                raise ValueError("请选择有效的下载格式")
            fmt = formats[format_index]
            plugin = get_plugin("yt_dlp")
            if plugin is None:
                raise RuntimeError("yt-dlp 插件未加载")

            def on_progress(message: str) -> None:
                safe = redact_sensitive_text(message)
                match = re.search(r"(\d+(?:\.\d+)?)%", safe)
                value = float(match.group(1)) if match else -1.0
                self._progress("download", value, safe)

            self._show_dialog(
                "download",
                {"stage": "progress", "progress": 0, "progressText": f"正在下载 {fmt.label}"},
            )
            path = await plugin.download(
                pending["url"],
                format_id=fmt.format_id,
                progress_cb=on_progress,
                has_audio=fmt.has_audio,
            )
            self._pending.pop("download", None)
            self._progress("download", 100.0, "下载完成")
            self._finish("download", True, f"下载完成：{path}", {"done": True})
        except Exception as exc:
            self._finish("download", False, str(exc), {})

    # ---- 请求投递 -------------------------------------------------------

    def schedule(self, factory: Callable[[], Any]) -> None:
        loop = self._loop
        if not loop or loop.is_closed():
            self._pusher.submit("log", {"text": "监控核心尚未就绪，请稍后重试"})
            return

        def launch() -> None:
            task = asyncio.create_task(factory())
            self._tasks.add(task)

            def finished(completed: asyncio.Task) -> None:
                self._tasks.discard(completed)
                if completed.cancelled():
                    return
                try:
                    error = completed.exception()
                except asyncio.CancelledError:
                    return
                if error is not None:
                    self._pusher.submit(
                        "log", {"text": f"后台操作失败：{redact_sensitive_text(str(error))}"}
                    )

            task.add_done_callback(finished)

        loop.call_soon_threadsafe(launch)

    # ---- 快照 -----------------------------------------------------------

    def _emit_snapshot(self) -> None:
        service = self._service
        if service is None:
            return
        rows = []
        for idx, status in service.followers.items():
            row = status_snapshot(idx, status)
            row["quality_options"] = [
                {"label": label, "value": value}
                for label, value in quality_options(
                    row.get("configured_plugin") or row.get("plugin"),
                    row.get("configured_platform") or row.get("platform"),
                    row.get("configured_quality") or "best",
                )
            ]
            rows.append(row)
        payload = {
            "rows": rows,
            "tags": ["全部"] + [tag for tag in service.all_tags if tag != "全部"],
            "plugin_options": list_plugins(),
            "poll_interval": service.poll_interval,
            "next_poll_at": (
                service.next_poll_at.strftime("%H:%M:%S") if service.next_poll_at else None
            ),
            "notifications_enabled": service.cfg.notifications_enabled,
            "platform_health": {key: health.state for key, health in service.platform_health.items()},
        }
        with self._snapshot_lock:
            self._last_snapshot = payload
        self._pusher.submit("snapshot", payload)

    def snapshot(self) -> dict:
        with self._snapshot_lock:
            return copy.deepcopy(self._last_snapshot)

    # ---- 监控回调（监控线程） -------------------------------------------

    def _on_status_change(self, idx, status) -> None:
        direction = "↑" if status.live_info.is_live else "↓"
        action = "开播" if status.live_info.is_live else "下播"
        self._pusher.submit(
            "liveEvent",
            {
                "idx": idx,
                "is_live": bool(status.live_info.is_live),
                "name": status.follower.name,
                "title": status.live_info.title or "",
                "is_initial": bool(status.is_initial_result),
            },
        )
        self._pusher.submit("log", {"text": f"{direction} {status.follower.name} {action}"})
        # 开播 toast（与 Qt 版一致：下播和启动时的初始结果不通知）。
        if (
            status.live_info.is_live
            and not status.is_initial_result
            and self._service is not None
            and self._service.cfg.notifications_enabled
            and self.notify_hook is not None
        ):
            try:
                self.notify_hook(idx, status.follower.name, status.live_info.title or "")
            except Exception:
                pass
        self._emit_snapshot()

    def _on_error(self, message: str) -> None:
        self._pusher.submit("log", {"text": f"监控异常：{redact_sensitive_text(message)}"})

    def _on_poll_start(self, count: int) -> None:
        self._pusher.submit("polling", {"active": True, "count": count})
        self._pusher.submit("log", {"text": f"开始第 {count} 轮检测…"})

    def _on_poll_end(self) -> None:
        self._emit_snapshot()
        self._pusher.submit("polling", {"active": False, "count": 0})
        self._pusher.submit("log", {"text": f"轮询结束 {datetime.datetime.now():%H:%M:%S}"})

    # ---- P1 操作 --------------------------------------------------------

    def refresh(self) -> None:
        self.schedule(self._refresh_once)

    async def _refresh_once(self) -> None:
        service = self._service
        if service is None:
            return
        if service.is_polling:
            self._pusher.submit("log", {"text": "当前检测尚未结束，已忽略重复刷新"})
            return
        self._pusher.submit("log", {"text": "开始手动刷新…"})
        try:
            await service.poll_all(None, force=True)
            self._emit_snapshot()
            self._pusher.submit("log", {"text": "手动刷新完成"})
        except Exception as exc:
            self._pusher.submit("log", {"text": f"手动刷新失败：{redact_sensitive_text(str(exc))}"})

    def set_quality(self, follower_index: int, quality: str) -> None:
        self.schedule(lambda: self._set_quality(follower_index, quality))

    async def _set_quality(self, follower_index: int, quality: str) -> None:
        try:
            service = self._require_service()
            status = service.followers.get(follower_index)
            if status is None:
                raise ValueError("选中的直播间已不存在")
            if service.is_polling or status.is_checking:
                raise ValueError("状态检测进行中，请在本轮结束后选择画质")
            current = status.follower
            selected = normalize_quality_choice(current.plugin, current.platform, quality)
            if (current.quality or "best").casefold() == selected.casefold():
                self._finish("quality", True, f"{current.name} 已使用该画质", {"quality": selected})
                return
            persisted = await asyncio.to_thread(
                service.config_manager.update_follower,
                follower_index,
                {"quality": selected},
                expected_key=follower_key(current),
                expected_follower=copy.deepcopy(current),
            )
            updated = persisted.follower
            service.cfg.followers[follower_index] = updated
            status.follower = updated
            self._emit_snapshot()
            label = "最优画质" if selected.casefold() == "best" else selected
            self._finish(
                "quality",
                True,
                f"已将 {updated.name} 的默认画质设为 {label}",
                {"quality": selected, "plugin": updated.plugin},
            )
        except Exception as exc:
            self._finish("quality", False, str(exc), {})

    def set_plugin(self, follower_index: int, plugin: str) -> None:
        self.schedule(lambda: self._set_plugin(follower_index, plugin))

    async def _set_plugin(self, follower_index: int, plugin: str) -> None:
        try:
            service = self._require_service()
            status = service.followers.get(follower_index)
            if status is None:
                raise ValueError("选中的直播间已不存在")
            if service.is_polling or status.is_checking:
                raise ValueError("状态检测进行中，请在本轮结束后切换插件")
            current = status.follower
            selected = str(plugin or "").strip()
            if get_plugin(selected) is None:
                raise ValueError(f"未知插件：{selected}")
            if (current.plugin or "").casefold() == selected.casefold():
                self._finish("plugin", True, f"{current.name} 已使用 {selected}", {"plugin": selected})
                return
            remapped_quality = quality_for_plugin(
                current.quality,
                source_plugin=current.plugin,
                target_plugin=selected,
                platform=current.platform,
            )
            values: dict = {"plugin": selected}
            if remapped_quality != (current.quality or "best"):
                values["quality"] = remapped_quality
            persisted = await asyncio.to_thread(
                service.config_manager.update_follower,
                follower_index,
                values,
                expected_key=follower_key(current),
                expected_follower=copy.deepcopy(current),
            )
            updated = persisted.follower
            service.cfg.followers[follower_index] = updated
            status.follower = updated
            self._emit_snapshot()
            note = f"（画质映射为 {remapped_quality}）" if "quality" in values else ""
            self._finish(
                "plugin",
                True,
                f"已将 {updated.name} 的检测插件切换为 {selected}{note}",
                {"plugin": selected, "quality": updated.quality},
            )
        except Exception as exc:
            self._finish("plugin", False, str(exc), {})

    def _require_service(self) -> MonitorService:
        if self._service is None:
            raise RuntimeError("监控核心尚未就绪")
        return self._service

    def _finish(self, kind: str, success: bool, message: str, payload: dict) -> None:
        self._pusher.submit(
            "operationFinished",
            {"kind": kind, "ok": bool(success), "message": message, "payload": payload},
        )

    def _show_dialog(self, kind: str, payload: dict) -> None:
        self._pusher.submit("dialog", {"kind": kind, "payload": payload})

    # ---- P2：详情与编辑 --------------------------------------------------

    def load_details(self, follower_index: int) -> None:
        self.schedule(lambda: self._load_details(follower_index))

    async def _load_details(self, follower_index: int) -> None:
        try:
            service = self._require_service()
            status = service.followers.get(follower_index)
            if status is None:
                raise ValueError("选中的关注项已不存在")
            detail = build_detail_view(
                status, service.get_platform_health(status.follower.platform)
            )
            # 详情与编辑合一：同一份推送同时携带状态详情行和编辑表单初值。
            form = follower_to_edit_payload(status.follower, redact=True)
            plugin = str(form.get("plugin", ""))
            platform = str(form.get("platform", ""))
            self._show_dialog(
                "edit",
                {
                    "idx": follower_index,
                    "detailTitle": detail.title,
                    "detailRows": [
                        {"label": row.label, "value": row.value, "tone": row.tone}
                        for row in detail.rows
                    ],
                    "streamAvailable": detail.stream_available,
                    "form": {
                        "enabled": "true" if form.get("enabled", True) else "false",
                        "name": form.get("name", ""),
                        "tags": "|".join(form.get("tags", [])),
                        "plugin": plugin,
                        "fallback_plugins": "|".join(form.get("fallback_plugins", [])),
                        "platform": platform,
                        "url": form.get("url", ""),
                        "quality": form.get("quality", "best"),
                        "sport_id": form.get("sport_id", ""),
                        "extra": json.dumps(
                            form.get("extra", {}), ensure_ascii=False, sort_keys=True, indent=2
                        ),
                    },
                    "pluginOptions": list_plugins(),
                    "qualityOptions": [
                        {"label": label, "value": value}
                        for label, value in quality_options(
                            plugin, platform, str(form.get("quality", "best"))
                        )
                    ],
                },
            )
        except Exception as exc:
            self._finish("edit", False, str(exc), {})

    def preview_edit(self, follower_index: int, values: dict) -> None:
        self.schedule(lambda: self._preview_edit(follower_index, dict(values or {})))

    async def _preview_edit(self, follower_index: int, values: dict) -> None:
        try:
            service = self._require_service()
            status = service.followers.get(follower_index)
            if status is None or status.is_checking:
                raise ValueError("编辑目标已变化或正在检测，请重新打开编辑器")
            candidate_values = dict(values)
            old_plugin = status.follower.plugin
            new_plugin = str(candidate_values.get("plugin", old_plugin) or old_plugin).strip()
            new_platform = str(
                candidate_values.get("platform", status.follower.platform) or status.follower.platform
            ).strip()
            requested_quality = str(
                candidate_values.get("quality", status.follower.quality) or "best"
            ).strip()
            if new_plugin.casefold() != old_plugin.casefold():
                if requested_quality.casefold() == (status.follower.quality or "best").casefold():
                    candidate_values["quality"] = quality_for_plugin(
                        status.follower.quality,
                        source_plugin=old_plugin,
                        target_plugin=new_plugin,
                        platform=new_platform,
                    )
                else:
                    candidate_values["quality"] = normalize_quality_choice(
                        new_plugin, new_platform, requested_quality
                    )
            preview = preview_follower_edit(
                status.follower,
                candidate_values,
                existing_followers=service.cfg.followers,
                editing_index=follower_index,
            )
            if not preview.has_changes:
                raise ValueError("配置没有实质变化")
            self._pending["edit"] = {
                "index": follower_index,
                "expected_key": follower_key(status.follower),
                "expected_follower": copy.deepcopy(status.follower),
                "preview": preview,
            }
            self._show_dialog(
                "edit",
                {
                    "stage": "confirm",
                    "previewText": format_changes(
                        "配置修改预览（尚未保存）", preview.changes, EDIT_LABELS
                    ),
                    "confirmLabel": "确认保存",
                },
            )
        except Exception as exc:
            self._finish("edit", False, str(exc), {})

    async def _confirm_edit(self) -> None:
        try:
            service = self._require_service()
            pending = self._pending.pop("edit", None)
            if not pending:
                raise ValueError("编辑预览已失效，请重新操作")
            index = pending["index"]
            status = service.followers.get(index)
            if status is None or status.is_checking:
                raise ValueError("编辑目标已变化或正在检测，未写入配置")
            persisted = await asyncio.to_thread(
                service.config_manager.update_follower,
                index,
                pending["preview"].follower,
                expected_key=pending["expected_key"],
                expected_follower=pending["expected_follower"],
            )
            updated = persisted.follower
            old = status.follower
            if index < len(service.cfg.followers):
                service.cfg.followers[index] = updated
            if follower_key(old) != follower_key(updated) or old.enabled != updated.enabled:
                replacement = FollowerStatus(
                    follower=updated,
                    check_state="disabled" if not updated.enabled else "unknown",
                )
                if not updated.enabled:
                    replacement.history.append(
                        StatusHistoryEntry(
                            at=datetime.datetime.now(),
                            state="disabled",
                            message="配置编辑后已禁用",
                            is_live=False,
                        )
                    )
                service.followers[index] = replacement
            else:
                status.follower = updated
            service.get_platform_health(updated.platform)
            self._emit_snapshot()
            self._finish("edit", True, f"已保存 {updated.name} 的配置修改", {"close": True})
        except Exception as exc:
            self._finish("edit", False, str(exc), {})

    # ---- P2：监控设置 ----------------------------------------------------

    def load_settings(self) -> None:
        self.schedule(self._load_settings)

    async def _load_settings(self) -> None:
        try:
            service = self._require_service()
            settings = service.config_manager.read_monitoring_settings()
            payload = {
                key: ("true" if value is True else "false" if value is False else str(value))
                for key, value in settings.items()
            }
            payload["stage"] = "form"
            self._show_dialog("settings", payload)
        except Exception as exc:
            self._finish("settings", False, str(exc), {})

    def preview_settings(self, values: dict) -> None:
        self.schedule(lambda: self._preview_settings(dict(values or {})))

    async def _preview_settings(self, values: dict) -> None:
        try:
            service = self._require_service()
            before = service.config_manager.read_monitoring_settings()
            preview = service.config_manager.preview_monitoring_settings(values)
            if not preview.has_changes:
                raise ValueError("监控设置没有实质变化")
            self._pending["settings"] = {"before": before, "preview": preview}
            self._show_dialog(
                "settings",
                {
                    "stage": "confirm",
                    "previewText": format_changes(
                        "监控设置预览（尚未保存）", preview.changes, SETTINGS_LABELS
                    ),
                    "confirmLabel": "确认保存",
                },
            )
        except Exception as exc:
            self._finish("settings", False, str(exc), {})

    async def _confirm_settings(self) -> None:
        try:
            service = self._require_service()
            pending = self._pending.pop("settings", None)
            if not pending:
                raise ValueError("设置预览已失效，请重新操作")
            persisted = await asyncio.to_thread(
                service.config_manager.update_monitoring_settings,
                pending["preview"].settings,
                expected_settings=pending["before"],
            )
            for field, value in persisted.settings.items():
                setattr(service.cfg, field, value)
            service.apply_monitoring_settings()
            self._emit_snapshot()
            self._finish("settings", True, "监控设置已保存并立即生效", {"close": True})
        except Exception as exc:
            self._finish("settings", False, str(exc), {})

    # ---- P2：平台代理 ----------------------------------------------------

    def load_proxy(self) -> None:
        self.schedule(self._load_proxy)

    async def _load_proxy(self) -> None:
        try:
            service = self._require_service()
            payload = {
                platform: service.cfg.platform_proxies.get(platform, "")
                for platform in sorted(PROXY_PLATFORMS)
            }
            payload["stage"] = "form"
            self._show_dialog("proxy", payload)
        except Exception as exc:
            self._finish("proxy", False, str(exc), {})

    def save_proxy(self, values: dict) -> None:
        self.schedule(lambda: self._save_proxy(dict(values or {})))

    async def _save_proxy(self, values: dict) -> None:
        try:
            service = self._require_service()
            proxies = {
                platform: str(values.get(platform, "")).strip()
                for platform in PROXY_PLATFORMS
                if str(values.get(platform, "")).strip()
            }
            candidate = copy.deepcopy(service.cfg)
            candidate.platform_proxies = proxies
            # save_config 会等待跨进程文件锁（最长 10 秒），不能阻塞事件循环。
            await asyncio.to_thread(service.config_manager.save_config, candidate)
            service.cfg.platform_proxies = dict(proxies)
            set_platform_proxies(proxies)
            description = ", ".join(f"{key}={value}" for key, value in sorted(proxies.items()))
            self._finish(
                "proxy", True, f"平台代理设置已保存：{description or '使用默认规则'}", {"close": True}
            )
        except Exception as exc:
            self._finish("proxy", False, str(exc), {})

    def test_proxy(self, values: dict) -> None:
        self.schedule(lambda: self._test_proxy(dict(values or {})))

    @staticmethod
    async def _test_proxy_endpoint(platform: str, raw_value: str) -> dict:
        explicit = raw_value.strip()
        proxy_url = normalize_proxy_value(explicit) if explicit else proxy_for_platform(platform)
        if proxy_url is None:
            return {
                "platform": platform,
                "status": "direct",
                "label": "直连",
                "detail": "该平台不会经过代理",
            }
        from urllib.parse import urlparse

        parsed = urlparse(proxy_url)
        host = parsed.hostname
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            port = None
        if not host or port is None:
            return {
                "platform": platform,
                "status": "error",
                "label": "地址无效",
                "detail": "请填写端口、主机:端口或完整代理 URL",
            }
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=2.5
            )
            writer.close()
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError) as exc:
            return {
                "platform": platform,
                "status": "error",
                "label": "不可连接",
                "detail": f"{host}:{port} 未响应（{redact_sensitive_text(str(exc))}）",
            }
        source = "自定义" if explicit else "默认"
        return {
            "platform": platform,
            "status": "ok",
            "label": "代理可连接",
            "detail": f"{source}代理 {host}:{port}",
        }

    async def _test_proxy(self, values: dict) -> None:
        try:
            normalized_values = {
                platform: str(values.get(platform, "")).strip()
                for platform in sorted(PROXY_PLATFORMS)
            }
            results = await asyncio.gather(*(
                self._test_proxy_endpoint(platform, normalized_values[platform])
                for platform in sorted(PROXY_PLATFORMS)
            ))
            failed = sum(item["status"] == "error" for item in results)
            direct = sum(item["status"] == "direct" for item in results)
            summary = (
                f"{failed} 个平台代理不可连接"
                if failed
                else f"代理检查通过；{direct} 个平台使用直连"
            )
            self._show_dialog(
                "proxy",
                {
                    "stage": "form",
                    **normalized_values,
                    "health": results,
                    "healthSummary": summary,
                    "healthOk": failed == 0,
                },
            )
        except Exception as exc:
            self._finish("proxy", False, f"代理测试失败：{exc}", {})

    # ---- P2：导入 --------------------------------------------------------

    def preview_import(self, url: str, tag: str) -> None:
        self.schedule(lambda: self._preview_import(str(url or ""), str(tag or "")))

    async def _preview_import(self, url: str, tag: str) -> None:
        try:
            service = self._require_service()
            preview = await ImportPreviewService(service.config_manager).preview(url, tag)
            self._pending.pop("import", None)
            if preview.can_confirm:
                self._pending["import"] = preview
            self._show_dialog(
                "import",
                {
                    "stage": "confirm",
                    "previewText": format_import_preview(preview),
                    "canConfirm": preview.can_confirm,
                    "requiresOverride": preview.requires_conflict_override,
                    "confirmLabel": "确认冲突后导入" if preview.requires_conflict_override else "确认导入",
                },
            )
        except Exception as exc:
            self._finish("import", False, str(exc), {})

    async def _confirm_import(self) -> None:
        try:
            service = self._require_service()
            preview = self._pending.pop("import", None)
            if preview is None:
                raise ValueError("导入预览已失效，请重新操作")
            follower = await asyncio.to_thread(
                ImportPreviewService(service.config_manager).confirm,
                preview,
                confirmed=True,
                allow_conflicts=preview.requires_conflict_override,
            )
            index = len(service.cfg.followers)
            service.cfg.followers.append(follower)
            service.followers[index] = FollowerStatus(
                follower=follower,
                check_state="disabled" if not follower.enabled else "unknown",
            )
            service.get_platform_health(follower.platform)
            self._emit_snapshot()
            self._finish("import", True, f"已导入 {follower.name}", {"close": True})
        except Exception as exc:
            self._finish("import", False, str(exc), {})

    # ---- P3：播放 / 行操作 ------------------------------------------------

    def _log(self, text: str) -> None:
        self._pusher.submit("log", {"text": text})

    def _set_playing(self, idx: int) -> None:
        self._playing_idx = idx
        self._pusher.submit("playerState", {"playing": idx >= 0, "idx": idx})

    def play(self, follower_index: int) -> None:
        self.schedule(lambda: self._play(follower_index))

    async def _play(self, follower_index: int) -> None:
        try:
            service = self._require_service()
            if follower_index not in service.followers:
                self._log("没有可用的选中项")
                return
            info = await service.get_stream_info(follower_index)
            follower = service.followers[follower_index].follower
            url = info.flv_url or info.m3u8_url or info.stream_url
            if not url:
                raise RuntimeError("插件没有返回可用流地址")
            # 进入候选启动流程前先停掉当前播放，保持"单播放器"语义。
            await self._stop_player()
            self._player_ipc = new_mpv_ipc_path()
            self._player_candidates = list(dict.fromkeys(
                str(item) for item in (stream_candidate_urls(info) or [url]) if item
            ))
            self._player_candidate_index = 0
            self._player_payload = {
                "title": f"{follower.name} - Zhibo",
                "headers": dict(info.extra.get("headers", {})),
                "proxy": proxy_for_platform(follower.platform) or "",
            }
            self._set_playing(follower_index)
            await self._start_next_candidate(self._player_generation)
        except Exception as exc:
            self._log(f"获取直播流失败：{redact_sensitive_text(str(exc))}")

    async def _start_next_candidate(self, generation: int) -> None:
        if generation != self._player_generation:
            return
        while self._player_candidate_index < len(self._player_candidates):
            url = self._player_candidates[self._player_candidate_index]
            self._player_candidate_index += 1
            try:
                process = await asyncio.to_thread(
                    play_url,
                    url,
                    title=str(self._player_payload.get("title", "Zhibo")),
                    headers=dict(self._player_payload.get("headers", {})),
                    proxy_url=str(self._player_payload.get("proxy", "")),
                    use_cache=True,
                    ipc_path=self._player_ipc,
                )
            except Exception as exc:
                self._log(f"CDN 候选启动失败：{redact_sensitive_text(str(exc))}")
                continue
            self._player_process = process
            # 与 Qt 版一致：1 秒后验证进程存活，早退则自动切下一条候选。
            await asyncio.sleep(1.0)
            if generation != self._player_generation:
                return
            if process.poll() is None:
                self._log("已启动 mpv 播放")
                return
            code = process.returncode
            self._player_process = None
            self._log(f"CDN 候选启动失败（退出码={code}），自动切换下一条")
        if generation != self._player_generation or not self._player_candidates:
            return
        self._player_process = None
        self._set_playing(-1)
        self._log(f"mpv 的全部 {len(self._player_candidates)} 条 CDN 候选均启动失败")

    def stop_player(self) -> None:
        self.schedule(self._stop_player)

    async def _stop_player(self) -> None:
        self._player_generation += 1
        self._player_candidates = []
        self._player_candidate_index = 0
        self._player_ipc = ""
        self._set_playing(-1)
        process = self._player_process
        self._player_process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return
        # 等待退出并逐级升级到 kill，避免留下句柄或孤儿 mpv 进程。
        try:
            await asyncio.to_thread(process.wait, 2.0)
            self._log("已停止当前 mpv")
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.kill()
            await asyncio.to_thread(process.wait, 1.0)
            self._log("已强制结束当前 mpv")
        except (OSError, subprocess.TimeoutExpired):
            self._log("mpv 进程未能立即退出，可能仍占用播放文件")

    def player_control(self, action: str) -> None:
        self.schedule(lambda: self._player_control(str(action or "")))

    async def _player_control(self, action: str) -> None:
        commands = {
            "toggle_pause": ("cycle", "pause"),
            "volume_up": ("add", "volume", "5"),
            "volume_down": ("add", "volume", "-5"),
            "toggle_mute": ("cycle", "mute"),
        }
        command = commands.get(action)
        if command is None:
            self._log(f"未知的播放器操作：{action}")
            return
        process = self._player_process
        if process is None or process.poll() is not None:
            self._log("mpv 当前没有正在播放")
            return
        if not mpv_command(self._player_ipc, *command):
            self._log("无法连接 mpv 控制通道")

    def copy_stream(self, follower_index: int) -> None:
        self.schedule(lambda: self._copy_stream(follower_index))

    async def _copy_stream(self, follower_index: int) -> None:
        try:
            service = self._require_service()
            if follower_index not in service.followers:
                self._log("没有可用的选中项")
                return
            info = await service.get_stream_info(follower_index)
            url = info.flv_url or info.m3u8_url or info.stream_url
            if not url:
                raise RuntimeError("插件没有返回可用流地址")
            self._pusher.submit("streamUrl", {"idx": follower_index, "url": url})
            self._log("直播流地址已获取，正在复制到剪贴板")
        except Exception as exc:
            self._log(f"获取直播流失败：{redact_sensitive_text(str(exc))}")

    def open_web(self, follower_index: int) -> None:
        self.schedule(lambda: self._open_web(follower_index))

    async def _open_web(self, follower_index: int) -> None:
        try:
            service = self._require_service()
            status = service.followers.get(follower_index)
            if status is None:
                self._log("没有可用的选中项")
                return
            row = status_snapshot(follower_index, status)
            url = web_url_for_snapshot(row)
            if not url.startswith(("http://", "https://")):
                self._log("该关注项没有可直接打开的网页地址")
                return
            await asyncio.to_thread(webbrowser.open, url)
            self._log(f"已在浏览器打开 {status.follower.name} 的直播间页面")
        except Exception as exc:
            self._log(f"打开网页失败：{redact_sensitive_text(str(exc))}")

    def toggle_enabled(self, follower_index: int) -> None:
        self.schedule(lambda: self._toggle_enabled(follower_index))

    async def _toggle_enabled(self, follower_index: int) -> None:
        try:
            service = self._require_service()
            status = service.followers.get(follower_index)
            if status is None:
                raise ValueError("选中的直播间已不存在")
            if service.is_polling or status.is_checking:
                raise ValueError("状态检测进行中，请在本轮结束后切换")
            current = status.follower
            persisted = await asyncio.to_thread(
                service.config_manager.update_follower,
                follower_index,
                {"enabled": not current.enabled},
                expected_key=follower_key(current),
                expected_follower=copy.deepcopy(current),
            )
            updated = persisted.follower
            service.cfg.followers[follower_index] = updated
            if updated.enabled:
                status.follower = updated
            else:
                replacement = FollowerStatus(follower=updated, check_state="disabled")
                replacement.history.append(
                    StatusHistoryEntry(
                        at=datetime.datetime.now(),
                        state="disabled",
                        message="右键快速停用",
                        is_live=False,
                    )
                )
                service.followers[follower_index] = replacement
            self._emit_snapshot()
            state = "已恢复监控" if updated.enabled else "已停止监控（配置保留）"
            self._finish("toggle_enabled", True, f"{updated.name} {state}", {})
        except Exception as exc:
            if self._service is not None:
                self._emit_snapshot()
            self._finish("toggle_enabled", False, str(exc), {})

    def toggle_notifications(self) -> None:
        self.schedule(self._toggle_notifications)

    async def _toggle_notifications(self) -> None:
        try:
            service = self._require_service()
            candidate = copy.deepcopy(service.cfg)
            candidate.notifications_enabled = not service.cfg.notifications_enabled
            # save_config 会等待跨进程文件锁（最长 10 秒），不能阻塞事件循环。
            await asyncio.to_thread(service.config_manager.save_config, candidate)
            service.cfg.notifications_enabled = candidate.notifications_enabled
            state = "开启" if candidate.notifications_enabled else "关闭"
            self._log(f"桌面通知已{state}")
            self._emit_snapshot()
        except Exception as exc:
            self._log(f"切换桌面通知失败：{redact_sensitive_text(str(exc))}")

    @staticmethod
    def _ensure_delete_is_idle(service: MonitorService) -> None:
        if service.is_polling or any(status.is_checking for status in service.followers.values()):
            raise ValueError("状态检测进行中，请等待本轮完成后再删除")
        if any(not task.done() for task in service._stream_deadline_tasks.values()):
            raise ValueError("正在获取直播流，请稍后再删除")

    def preview_delete(self, follower_index: int) -> None:
        self.schedule(lambda: self._preview_delete(follower_index))

    async def _preview_delete(self, follower_index: int) -> None:
        try:
            service = self._require_service()
            self._ensure_delete_is_idle(service)
            status = service.followers.get(follower_index)
            if status is None:
                raise ValueError("选中的直播间已不存在")
            if len(service.cfg.followers) <= 1:
                raise ValueError("至少需要保留一个直播间，无法删除最后一项")
            follower = status.follower
            self._pending["delete"] = {
                "index": follower_index,
                "expected_key": follower_key(follower),
                "expected_follower": copy.deepcopy(follower),
            }
            tags = "、".join(follower.tags) or "无标签"
            self._show_dialog(
                "delete",
                {
                    "stage": "confirm",
                    "previewText": (
                        "即将永久删除这个直播间：\n\n"
                        f"名称：{follower.name}\n"
                        f"平台：{follower.platform}\n"
                        f"标签：{tags}\n\n"
                        "删除后会立即写入关注列表，此操作不能在程序内撤销。"
                    ),
                    "confirmLabel": "确认删除",
                },
            )
        except Exception as exc:
            self._finish("delete", False, str(exc), {})

    async def _confirm_delete(self) -> None:
        try:
            service = self._require_service()
            pending = self._pending.pop("delete", None)
            if not pending:
                raise ValueError("删除确认已失效，请重新选择直播间")
            self._ensure_delete_is_idle(service)
            index = pending["index"]
            removed = await asyncio.to_thread(
                service.config_manager.remove_follower,
                index,
                expected_key=pending["expected_key"],
                expected_follower=pending["expected_follower"],
            )
            service.cfg.followers.pop(index)
            service.followers = {
                old_index if old_index < index else old_index - 1: status
                for old_index, status in service.followers.items()
                if old_index != index
            }
            self._pending.pop("edit", None)
            if self._playing_idx == index:
                # 正在播放的行被删除：先停播放器，避免指向失效下标。
                await self._stop_player()
            self._emit_snapshot()
            self._finish("delete", True, f"已删除直播间：{removed.name}", {"close": True})
        except Exception as exc:
            self._finish("delete", False, str(exc), {})

    # ---- P2：确认分发 ----------------------------------------------------

    def confirm_dialog(self, kind: str) -> None:
        confirmers = {
            "edit": self._confirm_edit,
            "settings": self._confirm_settings,
            "import": self._confirm_import,
            "delete": self._confirm_delete,
        }
        confirmer = confirmers.get(str(kind or ""))
        if confirmer is None:
            self._finish(str(kind or ""), False, "该对话框没有确认步骤", {})
            return
        self.schedule(confirmer)
