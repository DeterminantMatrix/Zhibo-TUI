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
import threading
from typing import Any, Callable

from zhibo.app_logging import get_logger, redact_sensitive_text
from zhibo.config import follower_key, follower_to_edit_payload, preview_follower_edit
from zhibo.detail import build_detail_view
from zhibo.import_preview import ImportPreviewService
from zhibo.monitor import FollowerStatus, MonitorService, StatusHistoryEntry
from zhibo.plugins import get_plugin, list_plugins
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
)


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

    # ---- P2：确认分发 ----------------------------------------------------

    def confirm_dialog(self, kind: str) -> None:
        confirmers = {
            "edit": self._confirm_edit,
            "settings": self._confirm_settings,
            "import": self._confirm_import,
        }
        confirmer = confirmers.get(str(kind or ""))
        if confirmer is None:
            self._finish(str(kind or ""), False, "该对话框没有确认步骤", {})
            return
        self.schedule(confirmer)
