"""Web 前端的监控服务线程 — 复用 zhibo.monitor，推送快照到 JS。

结构与 qt_quick/worker.py 同源：后台 asyncio 线程跑 MonitorService，
请求经 schedule() 投递；区别是结果通过 EventPusher 推给网页，
并维护一份线程安全的最新快照供 getSnapshot() 同步读取。
"""
from __future__ import annotations

import asyncio
import copy
import datetime
import threading
from typing import Any, Callable

from zhibo.app_logging import get_logger, redact_sensitive_text
from zhibo.config import follower_key
from zhibo.monitor import MonitorService
from zhibo.plugins import get_plugin, list_plugins
from zhibo.quality_options import normalize_quality_choice, quality_for_plugin, quality_options
from zhibo.viewmodel import status_snapshot


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
        self._logger = get_logger("zhibo.webui")

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
