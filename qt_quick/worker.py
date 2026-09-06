from __future__ import annotations

import asyncio
import copy
import json
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal

from zhibo.app_logging import redact_sensitive_mapping, redact_sensitive_text
from zhibo.config import (
    follower_key,
    follower_to_edit_payload,
    preview_follower_edit,
)
from zhibo.import_preview import ImportPreviewService
from zhibo.monitor import FollowerStatus, MonitorService, StatusHistoryEntry
from zhibo.plugins.bounded_executor import shutdown_plugin_workers
from zhibo.proxy_config import PROXY_PLATFORMS, normalize_proxy_value, proxy_for_platform, set_platform_proxies
from zhibo.quality_options import normalize_quality_choice, quality_for_plugin
from qt_quick.viewmodel import status_snapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class QuickMonitorBridge(QObject):
    snapshot = Signal(object)
    log = Signal(str)
    polling = Signal(bool, int)
    fatal = Signal(str)
    streamReady = Signal(str, object)
    detailData = Signal(object)
    dialogData = Signal(str, object)
    operationFinished = Signal(str, bool, str, object)
    progress = Signal(str, float, str)
    # 开播/下播事件（含初始结果标记），供托盘通知使用。
    liveEvent = Signal(bool, str, str, bool)
    stopped = Signal()


def _display_value(value: Any) -> str:
    safe = redact_sensitive_mapping(value)
    if isinstance(safe, bool):
        return "true" if safe else "false"
    if isinstance(safe, (dict, list, tuple)):
        return json.dumps(safe, ensure_ascii=False, sort_keys=True)
    return redact_sensitive_text(str(safe))


def _format_changes(title: str, changes: dict[str, tuple[Any, Any]], labels: dict[str, str]) -> str:
    if not changes:
        return "没有检测到实质变化。"
    lines = [title]
    for field, pair in changes.items():
        before, after = pair
        lines.append(f"{labels.get(field, field)}：{_display_value(before)} → {_display_value(after)}")
    lines.extend(("", "确认后会重新核对磁盘配置并以原子方式写入。"))
    return "\n".join(lines)


def _format_import_preview(preview) -> str:
    lines = ["导入预览（尚未写入配置）"]
    follower = preview.follower
    if follower is not None:
        lines.extend(
            (
                f"名称：{redact_sensitive_text(follower.name)}",
                f"平台：{redact_sensitive_text(follower.platform or '-')}",
                f"插件：{redact_sensitive_text(follower.plugin)}",
                f"备用插件：{redact_sensitive_text(', '.join(follower.fallback_plugins) or '-')}",
                f"标签：{redact_sensitive_text(', '.join(follower.tags) or '未分类')}",
                f"地址：{redact_sensitive_text(follower.url)}",
                f"画质：{redact_sensitive_text(follower.quality or 'best')}",
            )
        )
    if preview.messages:
        lines.append("")
        lines.extend(f"注意：{redact_sensitive_text(message)}" for message in preview.messages)
    elif follower is not None:
        lines.extend(("", "校验通过；确认后才会写入 followers.csv。"))
    return "\n".join(lines)


class QuickMonitorThread:
    EDIT_LABELS = {
        "enabled": "启用",
        "name": "名称",
        "tags": "标签",
        "plugin": "主插件",
        "fallback_plugins": "备用插件",
        "platform": "平台",
        "url": "直播间地址",
        "quality": "画质",
        "sport_id": "sport_id",
        "extra": "扩展字段",
    }
    SETTINGS_LABELS = {
        "poll_interval": "轮询间隔（秒）",
        "max_concurrent_checks": "最大并发检测",
        "failure_backoff_after": "失败后退避阈值",
        "failure_backoff_polls": "退避轮数",
        "notifications_enabled": "桌面通知",
    }

    def __init__(self, config_path: str | None = None):
        self.config_path = config_path
        self.bridge = QuickMonitorBridge()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._service: MonitorService | None = None
        self._run_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._pending: dict[str, Any] = {}
        self._update_process: asyncio.subprocess.Process | None = None
        self._stop_requested = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._thread_main, name="zhibo-qml-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_requested.set()
        loop = self._loop
        service = self._service
        run_task = self._run_task
        tasks = tuple(self._tasks)
        if loop and loop.is_running() and service:
            def cancel() -> None:
                service.stop()
                if run_task and not run_task.done():
                    run_task.cancel()
                for task in tasks:
                    if not task.done():
                        task.cancel()

            loop.call_soon_threadsafe(cancel)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)

    def request_refresh(self) -> None:
        self._schedule(self._refresh_once)

    def toggle_notifications(self) -> None:
        self._schedule(self._toggle_notifications)

    def request_stream(self, follower_index: int, purpose: str) -> None:
        self._schedule(lambda: self._get_stream(follower_index, purpose))

    def request_details(self, follower_index: int) -> None:
        self._schedule(lambda: self._load_details(follower_index))

    def request_edit(self, follower_index: int) -> None:
        self._schedule(lambda: self._load_edit(follower_index))

    def preview_edit(self, follower_index: int, values: dict) -> None:
        self._schedule(lambda: self._preview_edit(follower_index, values))

    def preview_delete(self, follower_index: int) -> None:
        self._schedule(lambda: self._preview_delete(follower_index))

    def set_quality(self, follower_index: int, quality: str) -> None:
        self._schedule(lambda: self._set_quality(follower_index, quality))

    def toggle_enabled(self, follower_index: int) -> None:
        self._schedule(lambda: self._toggle_enabled(follower_index))

    def request_settings(self) -> None:
        self._schedule(self._load_settings)

    def preview_settings(self, values: dict) -> None:
        self._schedule(lambda: self._preview_settings(values))

    def request_proxy(self) -> None:
        self._schedule(self._load_proxy)

    def save_proxy(self, values: dict) -> None:
        self._schedule(lambda: self._save_proxy(values))

    def test_proxy(self, values: dict) -> None:
        self._schedule(lambda: self._test_proxy(values))

    def preview_import(self, url: str, tag: str) -> None:
        self._schedule(lambda: self._preview_import(url, tag))

    def confirm_pending(self, kind: str) -> None:
        handlers: dict[str, Callable[[], Any]] = {
            "edit": self._confirm_edit,
            "delete": self._confirm_delete,
            "settings": self._confirm_settings,
            "import": self._confirm_import,
        }
        handler = handlers.get(kind)
        if handler is None:
            self.bridge.operationFinished.emit(kind, False, "没有可确认的操作", {})
            return
        self._schedule(handler)

    def request_download_formats(self, url: str) -> None:
        self._schedule(lambda: self._load_download_formats(url))

    def start_download(self, format_index: int) -> None:
        self._schedule(lambda: self._download(format_index))

    def request_update(self, target: str, content: str = "") -> None:
        self._schedule(lambda: self._run_update(target, content))

    def request_update_status(self) -> None:
        self._schedule(self._load_update_status)

    def request_update_check(self, target: str) -> None:
        self._schedule(lambda: self._check_update_target(target))

    def _schedule(self, factory: Callable[[], Any]) -> None:
        loop = self._loop
        if not loop or not loop.is_running() or self._stop_requested.is_set():
            self.bridge.log.emit("监控核心尚未就绪，请稍后重试")
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
                    self.bridge.log.emit(f"后台操作失败：{redact_sensitive_text(str(error))}")

            task.add_done_callback(finished)

        loop.call_soon_threadsafe(launch)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.bridge.fatal.emit(redact_sensitive_text(str(exc) or "监控线程启动失败"))
        finally:
            self._loop = None
            self._service = None
            self._run_task = None
            self._tasks.clear()
            self._pending.clear()
            self.bridge.stopped.emit()

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._sweep_install_leftovers()
        service = MonitorService(self.config_path)
        self._service = service
        service.on_status_change(self._on_status_change)
        service.on_error(self._on_error)
        service.on_poll_start(self._on_poll_start)
        service.on_poll_end(self._on_poll_end)
        self._emit_snapshot()
        self.bridge.log.emit(f"QML 原生监控已启动，共 {len(service.followers)} 个关注项")
        try:
            self._run_task = asyncio.create_task(service.run(), name="zhibo-qml-monitor-loop")
            await self._run_task
        except asyncio.CancelledError:
            pass
        finally:
            for task in tuple(self._tasks):
                task.cancel()
            if self._tasks:
                await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
            await self._terminate_update_process()
            await service.shutdown(timeout=1.5)
            await asyncio.to_thread(shutdown_plugin_workers, True)

    async def _sweep_install_leftovers(self) -> None:
        """启动时清理上次更新中断留下的目录；尽力而为，不阻断启动。"""
        from zhibo.mpv_ui import sweep_uosc_leftovers
        from zhibo.tool_runtime import sweep_install_leftovers

        try:
            cleaned = await asyncio.to_thread(sweep_install_leftovers)
            cleaned += await asyncio.to_thread(sweep_uosc_leftovers)
        except Exception as exc:
            self.bridge.log.emit(f"安装残留清理失败（可忽略）：{redact_sensitive_text(str(exc))}")
            return
        for message in cleaned:
            self.bridge.log.emit(message)

    def _service_or_error(self) -> MonitorService:
        if self._service is None:
            raise RuntimeError("监控核心尚未就绪")
        return self._service

    def _finish(self, kind: str, success: bool, message: str, payload: dict | None = None) -> None:
        self.bridge.operationFinished.emit(kind, success, redact_sensitive_text(message), payload or {})

    async def _refresh_once(self) -> None:
        service = self._service_or_error()
        if service.is_polling:
            self.bridge.log.emit("当前检测尚未结束，已忽略重复刷新")
            return
        self.bridge.log.emit("开始手动刷新…")
        try:
            # 手动刷新忽略每主播独立间隔，立即全量检测。
            await service.poll_all(None, force=True)
            self._emit_snapshot()
            self.bridge.log.emit("手动刷新完成")
        except Exception as exc:
            self.bridge.log.emit(f"手动刷新失败：{redact_sensitive_text(str(exc))}")

    async def _toggle_notifications(self) -> None:
        service = self._service_or_error()
        candidate = copy.deepcopy(service.cfg)
        candidate.notifications_enabled = not service.cfg.notifications_enabled
        # save_config 会等待跨进程文件锁（最长 10 秒），不能阻塞事件循环。
        await asyncio.to_thread(service.config_manager.save_config, candidate)
        service.cfg.notifications_enabled = candidate.notifications_enabled
        state = "开启" if candidate.notifications_enabled else "关闭"
        self.bridge.log.emit(f"桌面通知已{state}")
        self._emit_snapshot()

    async def _get_stream(self, follower_index: int, purpose: str) -> None:
        from zhibo.plugins.base import stream_candidate_urls

        service = self._service_or_error()
        if follower_index not in service.followers:
            self.bridge.log.emit("没有可用的选中项")
            return
        try:
            info = await service.get_stream_info(follower_index)
            follower = service.followers[follower_index].follower
            url = info.flv_url or info.m3u8_url or info.stream_url
            if not url:
                raise RuntimeError("插件没有返回可用流地址")
            self.bridge.streamReady.emit(
                purpose,
                {
                    "idx": follower_index,
                    "url": url,
                    "urls": stream_candidate_urls(info),
                    "title": f"{follower.name} - Zhibo",
                    "headers": dict(info.extra.get("headers", {})),
                    "proxy": proxy_for_platform(follower.platform) or "",
                },
            )
        except Exception as exc:
            self.bridge.log.emit(f"获取直播流失败：{redact_sensitive_text(str(exc))}")

    async def _load_details(self, follower_index: int) -> None:
        try:
            from zhibo.detail import build_detail_view

            service = self._service_or_error()
            status = service.followers.get(follower_index)
            if status is None:
                raise ValueError("选中的关注项已不存在")
            detail = build_detail_view(status, service.get_platform_health(status.follower.platform))
            self.bridge.detailData.emit(
                {
                    "idx": follower_index,
                    "detailTitle": detail.title,
                    "detailRows": [
                        {"label": row.label, "value": row.value, "tone": row.tone}
                        for row in detail.rows
                    ],
                    "streamAvailable": detail.stream_available,
                }
            )
        except Exception as exc:
            self.bridge.log.emit(f"读取详情失败：{redact_sensitive_text(str(exc))}")

    async def _load_edit(self, follower_index: int) -> None:
        try:
            service = self._service_or_error()
            status = service.followers.get(follower_index)
            if status is None:
                raise ValueError("选中的关注项已不存在")
            if status.is_checking:
                raise ValueError("该关注项正在检测，请等待本轮完成")
            payload = follower_to_edit_payload(status.follower, redact=True)
            payload.update(
                {
                    "stage": "form",
                    "index": follower_index,
                    "enabled": "true" if payload.get("enabled", True) else "false",
                    "tags": "|".join(payload.get("tags", [])),
                    "fallback_plugins": "|".join(payload.get("fallback_plugins", [])),
                    "extra": json.dumps(payload.get("extra", {}), ensure_ascii=False, sort_keys=True, indent=2),
                }
            )
            self.bridge.dialogData.emit("edit", payload)
        except Exception as exc:
            self._finish("edit", False, str(exc))

    async def _preview_edit(self, follower_index: int, values: dict) -> None:
        try:
            service = self._service_or_error()
            status = service.followers.get(follower_index)
            if status is None or status.is_checking:
                raise ValueError("编辑目标已变化或正在检测，请重新打开编辑器")
            candidate_values = dict(values)
            old_plugin = status.follower.plugin
            new_plugin = str(candidate_values.get("plugin", old_plugin) or old_plugin).strip()
            new_platform = str(candidate_values.get("platform", status.follower.platform) or status.follower.platform).strip()
            requested_quality = str(candidate_values.get("quality", status.follower.quality) or "best").strip()
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
            self.bridge.dialogData.emit(
                "edit",
                {
                    "stage": "confirm",
                    "previewText": _format_changes("配置修改预览（尚未保存）", preview.changes, self.EDIT_LABELS),
                    "confirmLabel": "确认保存",
                },
            )
        except Exception as exc:
            self._finish("edit", False, str(exc))

    async def _confirm_edit(self) -> None:
        try:
            service = self._service_or_error()
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
                            at=datetime.now(), state="disabled", message="配置编辑后已禁用", is_live=False
                        )
                    )
                service.followers[index] = replacement
            else:
                status.follower = updated
            service.get_platform_health(updated.platform)
            self._emit_snapshot()
            self._finish("edit", True, f"已保存 {updated.name} 的配置修改", {"close": True})
        except Exception as exc:
            self._finish("edit", False, str(exc))

    @staticmethod
    def _ensure_delete_is_idle(service: MonitorService) -> None:
        if service.is_polling or any(status.is_checking for status in service.followers.values()):
            raise ValueError("状态检测进行中，请等待本轮完成后再删除")
        if any(not task.done() for task in service._stream_deadline_tasks.values()):
            raise ValueError("正在获取直播流，请稍后再删除")

    async def _preview_delete(self, follower_index: int) -> None:
        try:
            service = self._service_or_error()
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
            self.bridge.dialogData.emit(
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
            self._finish("delete", False, str(exc))

    async def _confirm_delete(self) -> None:
        try:
            service = self._service_or_error()
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
            self._emit_snapshot()
            self._finish("delete", True, f"已删除直播间：{removed.name}", {"close": True})
        except Exception as exc:
            self._finish("delete", False, str(exc))

    async def _set_quality(self, follower_index: int, quality: str) -> None:
        try:
            service = self._service_or_error()
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
            if self._service is not None:
                self._emit_snapshot()
            self._finish("quality", False, str(exc))

    async def _toggle_enabled(self, follower_index: int) -> None:
        try:
            service = self._service_or_error()
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
                        at=datetime.now(), state="disabled", message="右键快速停用", is_live=False
                    )
                )
                service.followers[follower_index] = replacement
            self._emit_snapshot()
            state = "已恢复监控" if updated.enabled else "已停止监控（配置保留）"
            self._finish("toggle_enabled", True, f"{updated.name} {state}", {})
        except Exception as exc:
            if self._service is not None:
                self._emit_snapshot()
            self._finish("toggle_enabled", False, str(exc))

    async def _load_settings(self) -> None:
        try:
            service = self._service_or_error()
            settings = service.config_manager.read_monitoring_settings()
            payload = {key: ("true" if value is True else "false" if value is False else str(value)) for key, value in settings.items()}
            payload["stage"] = "form"
            self.bridge.dialogData.emit("settings", payload)
        except Exception as exc:
            self._finish("settings", False, str(exc))

    async def _preview_settings(self, values: dict) -> None:
        try:
            service = self._service_or_error()
            before = service.config_manager.read_monitoring_settings()
            preview = service.config_manager.preview_monitoring_settings(values)
            if not preview.has_changes:
                raise ValueError("监控设置没有实质变化")
            self._pending["settings"] = {"before": before, "preview": preview}
            self.bridge.dialogData.emit(
                "settings",
                {
                    "stage": "confirm",
                    "previewText": _format_changes("监控设置预览（尚未保存）", preview.changes, self.SETTINGS_LABELS),
                    "confirmLabel": "确认保存",
                },
            )
        except Exception as exc:
            self._finish("settings", False, str(exc))

    async def _confirm_settings(self) -> None:
        try:
            service = self._service_or_error()
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
            self._finish("settings", False, str(exc))

    async def _load_proxy(self) -> None:
        try:
            service = self._service_or_error()
            payload = {platform: service.cfg.platform_proxies.get(platform, "") for platform in sorted(PROXY_PLATFORMS)}
            payload["stage"] = "form"
            self.bridge.dialogData.emit("proxy", payload)
        except Exception as exc:
            self._finish("proxy", False, str(exc))

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
            _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.5)
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
            self.bridge.dialogData.emit(
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
            self._finish("proxy", False, f"代理测试失败：{exc}")

    async def _save_proxy(self, values: dict) -> None:
        try:
            service = self._service_or_error()
            proxies = {
                platform: str(values.get(platform, "")).strip()
                for platform in PROXY_PLATFORMS
                if str(values.get(platform, "")).strip()
            }
            candidate = copy.deepcopy(service.cfg)
            candidate.platform_proxies = proxies
            await asyncio.to_thread(service.config_manager.save_config, candidate)
            service.cfg.platform_proxies = dict(proxies)
            set_platform_proxies(proxies)
            description = ", ".join(f"{key}={value}" for key, value in sorted(proxies.items()))
            self._finish("proxy", True, f"平台代理设置已保存：{description or '使用默认规则'}", {"close": True})
        except Exception as exc:
            self._finish("proxy", False, str(exc))

    async def _preview_import(self, url: str, tag: str) -> None:
        try:
            service = self._service_or_error()
            preview = await ImportPreviewService(service.config_manager).preview(url, tag)
            self._pending.pop("import", None)
            if preview.can_confirm:
                self._pending["import"] = preview
            self.bridge.dialogData.emit(
                "import",
                {
                    "stage": "confirm",
                    "previewText": _format_import_preview(preview),
                    "canConfirm": preview.can_confirm,
                    "requiresOverride": preview.requires_conflict_override,
                    "confirmLabel": "确认冲突后导入" if preview.requires_conflict_override else "确认导入",
                },
            )
        except Exception as exc:
            self._finish("import", False, str(exc))

    async def _confirm_import(self) -> None:
        try:
            service = self._service_or_error()
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
            self._finish("import", False, str(exc))

    async def _load_download_formats(self, url: str) -> None:
        try:
            from zhibo.plugins import get_plugin

            plugin = get_plugin("yt_dlp")
            if plugin is None:
                raise RuntimeError("yt-dlp 插件未加载")
            self.bridge.progress.emit("download", -1.0, "正在获取可用格式…")
            formats = await plugin.list_formats(url)
            if not formats:
                raise RuntimeError("没有找到可下载格式")
            self._pending["download"] = {"url": url, "formats": formats}
            self.bridge.dialogData.emit(
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
            self._finish("download", False, str(exc))

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
                self.bridge.progress.emit("download", value, safe)

            self.bridge.dialogData.emit("download", {"stage": "progress", "progress": 0, "progressText": f"正在下载 {fmt.label}"})
            path = await plugin.download(
                pending["url"],
                format_id=fmt.format_id,
                progress_cb=on_progress,
                has_audio=fmt.has_audio,
            )
            self._pending.pop("download", None)
            self.bridge.progress.emit("download", 100.0, "下载完成")
            self._finish("download", True, f"下载完成：{path}", {"done": True})
        except Exception as exc:
            self._finish("download", False, str(exc))

    async def _run_update(self, target: str, content: str) -> None:
        try:
            allowed = {"streamlink", "streamget", "yt-dlp", "mpv", "ffmpeg", "uosc", "fs1", "bilibili_cookie"}
            if target not in allowed:
                raise ValueError("更新目标无效")
            self.bridge.dialogData.emit("update", {"stage": "progress", "progress": 5, "progressText": "正在准备更新…"})
            self.bridge.progress.emit("update", 5.0, "正在准备更新…")
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
                    self.bridge.progress.emit("update", 100.0, message)
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
                    self.bridge.progress.emit("update", 100.0, message)
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
                    self.bridge.progress.emit("update", 100.0, message)
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
            self.bridge.progress.emit("update", 100.0, message)
            self._finish(
                "update",
                True,
                message,
                {"done": True, "downloaded": True, "target": target, "version": version},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._finish("update", False, str(exc))
        finally:
            content = ""

    async def _load_update_status(self) -> None:
        try:
            from zhibo.update_state import update_items

            items = await asyncio.to_thread(update_items)
            self.bridge.dialogData.emit(
                "update",
                {
                    "stage": "form",
                    "items": items,
                    "target": "mpv",
                },
            )
        except Exception as exc:
            self._finish("update", False, f"读取更新状态失败：{exc}")

    async def _check_update_target(self, target: str) -> None:
        try:
            from zhibo.update_state import check_update_target

            allowed = {"mpv", "ffmpeg", "uosc", "streamlink", "streamget", "yt-dlp"}
            if target not in allowed:
                raise ValueError("该组件不支持远端版本检查")
            fields = await asyncio.to_thread(check_update_target, target)
            self.bridge.dialogData.emit(
                "update",
                {"stage": "checked", "target": target, "item": fields},
            )
        except Exception as exc:
            self.bridge.dialogData.emit(
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

    async def _record_update(self, target: str, version: str) -> None:
        from zhibo.update_state import record_successful_update

        try:
            await asyncio.to_thread(record_successful_update, target, version)
        except Exception as exc:
            self.bridge.log.emit(f"更新已完成，但更新时间记录失败：{redact_sensitive_text(str(exc))}")

    async def _update_bilibili_cookie(self, content: str) -> None:
        from zhibo.bilibili_cookie import update_bilibili_cookies

        if not content.strip():
            raise ValueError("请粘贴 Netscape cookies.txt 内容")
        self.bridge.progress.emit("update", 25.0, "正在校验 B站 Cookie…")
        result = await asyncio.to_thread(update_bilibili_cookies, content)
        self.bridge.log.emit(
            f"B站 Cookie：更新 {result.bilibili_records_updated} 条，保留其他站点 {result.non_bilibili_records_preserved} 条"
        )

    async def _update_fs1(self, content: str) -> None:
        from zhibo.plugins import get_plugin
        from zhibo.plugins.fs1_plugin import update_from_text

        if not content.strip():
            raise ValueError("请粘贴 FS /v1/room 请求 curl")
        self.bridge.progress.emit("update", 25.0, "正在解析并更新 FS1 配置…")
        applied = await asyncio.to_thread(update_from_text, content)
        plugin = get_plugin("fs1")
        if plugin is not None and callable(getattr(plugin, "reload_config", None)):
            plugin.reload_config()
        service = self._service_or_error()
        service.reset_platform_health("fs1")
        self._emit_snapshot()
        for key in sorted(applied):
            value = "***" if key in {"token", "authorization", "cookie", "imei", "dun_imei"} else applied[key]
            self.bridge.log.emit(f"FS1 {key}: {redact_sensitive_text(value)}")

    async def _update_package(self, package: str) -> None:
        self.bridge.progress.emit("update", 15.0, f"正在更新 {package}…")
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
                    self.bridge.log.emit(text)
            code = await process.wait()
            if code != 0:
                raise RuntimeError(f"{package} 更新失败，退出码={code}")
        finally:
            if self._update_process is process:
                self._update_process = None

    async def _install_tool(self, tool: str, plan) -> str:
        from zhibo.tool_runtime import install_portable_tool, seven_zip_available

        if not seven_zip_available():
            self.bridge.progress.emit("update", 10.0, f"正在安装 {tool.upper()} 解压支持…")
            await self._update_package("py7zr")

        def report(value: float, message: str) -> None:
            if self._stop_requested.is_set():
                raise RuntimeError("安装已取消")
            self.bridge.progress.emit("update", value, redact_sensitive_text(message))

        return await asyncio.to_thread(install_portable_tool, tool, report, plan=plan)

    async def _install_uosc(self, plan) -> str:
        from zhibo.mpv_ui import install_uosc

        def report(value: float, message: str) -> None:
            if self._stop_requested.is_set():
                raise RuntimeError("uosc 安装已取消")
            self.bridge.progress.emit("update", value, redact_sensitive_text(message))

        return await asyncio.to_thread(install_uosc, report, plan=plan)

    async def _terminate_update_process(self) -> None:
        process = self._update_process
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def _emit_snapshot(self) -> None:
        service = self._service
        if service is None:
            return
        self.bridge.snapshot.emit(
            {
                "rows": [status_snapshot(idx, status) for idx, status in service.followers.items()],
                "tags": ["全部"] + [tag for tag in service.all_tags if tag != "全部"],
                "poll_interval": service.poll_interval,
                "next_poll_at": service.next_poll_at,
                "notifications_enabled": service.cfg.notifications_enabled,
                "platform_health": {key: health.state for key, health in service.platform_health.items()},
            }
        )

    def _on_status_change(self, _idx, status) -> None:
        direction = "↑" if status.live_info.is_live else "↓"
        action = "开播" if status.live_info.is_live else "下播"
        self.bridge.log.emit(f"{direction} {status.follower.name} {action}")
        self.bridge.liveEvent.emit(
            bool(status.live_info.is_live),
            status.follower.name,
            status.live_info.title or "",
            bool(status.is_initial_result),
        )
        self._emit_snapshot()

    def _on_error(self, message: str) -> None:
        self.bridge.log.emit(f"监控异常：{redact_sensitive_text(message)}")

    def _on_poll_start(self, count: int) -> None:
        self.bridge.polling.emit(True, count)
        self.bridge.log.emit(f"开始第 {count} 轮检测…")

    def _on_poll_end(self) -> None:
        self._emit_snapshot()
        self.bridge.polling.emit(False, 0)
        self.bridge.log.emit(f"轮询结束 {datetime.now():%H:%M:%S}")
