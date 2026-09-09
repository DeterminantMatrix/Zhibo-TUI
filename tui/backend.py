"""TUI ↔ zhibo.monitor 桥接层。

与 webui 时代的线程桥不同：Textual 本身就是 asyncio，监控服务直接跑在
UI 的事件循环里，回调可以安全地同步更新界面。播放器状态同样只在本
循环内访问。UI 通过三个回调接收事件：on_snapshot / on_log / on_player。
"""
from __future__ import annotations

import asyncio
import copy
import datetime
import json
import subprocess
import webbrowser

from zhibo.app_logging import redact_sensitive_mapping, redact_sensitive_text
from zhibo.config import (
    follower_key,
    follower_to_edit_payload,
    preview_follower_edit,
)
from zhibo.detail import build_detail_view
from zhibo.desktop import mpv_command, new_mpv_ipc_path, play_url
from zhibo.import_preview import ImportPreviewService
from zhibo.monitor import FollowerStatus, MonitorService, StatusHistoryEntry
from zhibo.plugins import list_plugins
from zhibo.plugins.base import stream_candidate_urls
from zhibo.plugins.bounded_executor import shutdown_plugin_workers
from zhibo.proxy_config import (
    PROXY_PLATFORMS,
    normalize_proxy_value,
    proxy_for_platform,
    set_platform_proxies,
)
from zhibo.quality_options import normalize_quality_choice, quality_for_plugin, quality_options
from zhibo.viewmodel import sort_snapshots, status_snapshot, web_url_for_snapshot


# ---- 事务确认页的纯展示格式化（自 webui/Qt 移植；value 一律先脱敏） ------

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


def _display_value(value) -> str:
    safe = redact_sensitive_mapping(value)
    if isinstance(safe, bool):
        return "true" if safe else "false"
    if isinstance(safe, (dict, list, tuple)):
        import json

        return json.dumps(safe, ensure_ascii=False, sort_keys=True)
    return redact_sensitive_text(str(safe))


def _format_changes(title: str, changes: dict, labels: dict) -> str:
    if not changes:
        return "没有检测到实质变化。"
    lines = [title]
    for field, pair in changes.items():
        before, after = pair
        lines.append(f"{labels.get(field, field)}：{_display_value(before)} → {_display_value(after)}")
    lines.extend(("", "确认后会重新核对磁盘配置并以原子方式写入。"))
    return "\n".join(lines)


def _format_import_preview(preview) -> str:
    from zhibo.app_logging import redact_sensitive_text as _rst

    lines = ["导入预览（尚未写入配置）"]
    follower = preview.follower
    if follower is not None:
        lines.extend(
            (
                f"名称：{_rst(follower.name)}",
                f"平台：{_rst(follower.platform or '-')}",
                f"插件：{_rst(follower.plugin)}",
                f"备用插件：{_rst(', '.join(follower.fallback_plugins) or '-')}",
                f"标签：{_rst(', '.join(follower.tags) or '未分类')}",
                f"地址：{_rst(follower.url)}",
                f"画质：{_rst(follower.quality or 'best')}",
            )
        )
    if preview.messages:
        lines.append("")
        lines.extend(f"注意：{_rst(message)}" for message in preview.messages)
    elif follower is not None:
        lines.extend(("", "校验通过；确认后才会写入 followers.csv。"))
    return "\n".join(lines)


class MonitorBridge:
    """监控核心 + 外部 mpv 播放器的生命周期管理。"""

    def __init__(self, config_path: str | None = None, startup_notes: list[str] | None = None) -> None:
        self.config_path = config_path
        self.startup_notes = list(startup_notes or [])
        self.service: MonitorService | None = None
        self.snapshot: dict = {"rows": [], "tags": ["全部"], "polling": False}
        # UI 注册的事件回调（Textual 单循环内同步调用，无需线程封送）。
        self.on_snapshot = None  # callable(dict)
        self.on_log = None  # callable(str)
        self.on_player = None  # callable(idx: int | None)
        self._run_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        # 播放器状态：单播放器 + CDN 候选 + 代数守卫（与 Qt 版同构）。
        self._player_process: subprocess.Popen | None = None
        self._player_ipc = ""
        self._player_generation = 0
        self._player_candidates: list[str] = []
        self._player_candidate_index = 0
        self._player_payload: dict = {}
        self._playing_idx = -1
        self._poll_round = 0
        self._pending: dict = {}

    # ---- 生命周期 -------------------------------------------------------

    async def start(self) -> None:
        self._run_task = asyncio.create_task(self._run(), name="zhibo-tui-monitor")

    async def stop(self) -> None:
        if self._run_task is None:
            return
        self._run_task.cancel()
        try:
            await self._run_task
        except asyncio.CancelledError:
            pass
        self._run_task = None

    async def _run(self) -> None:
        try:
            self.service = MonitorService(self.config_path)
        except Exception as exc:
            self._log(f"监控核心启动失败：{redact_sensitive_text(str(exc))}")
            return
        service = self.service
        service.on_status_change(self._on_status_change)
        service.on_error(self._on_error)
        service.on_poll_start(self._on_poll_start)
        service.on_poll_end(self._on_poll_end)
        self._emit_snapshot()
        self._log(f"监控核心就绪，共 {len(service.followers)} 个关注项")
        try:
            await service.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(f"监控循环异常退出：{redact_sensitive_text(str(exc))}")
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if self.service is not None:
            await self.service.shutdown(timeout=1.5)
        await asyncio.to_thread(shutdown_plugin_workers, True)
        self._terminate_player()

    def _terminate_player(self) -> None:
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

    # ---- 事件推送 -------------------------------------------------------

    def _log(self, text: str) -> None:
        if self.on_log is not None:
            self.on_log(text)

    def _set_playing(self, idx: int) -> None:
        self._playing_idx = idx
        if self.on_player is not None:
            self.on_player(idx if idx >= 0 else None)

    def _emit_snapshot(self) -> None:
        service = self.service
        if service is None:
            return
        rows = [status_snapshot(idx, status) for idx, status in service.followers.items()]
        rows = sort_snapshots(rows, "default")
        self.snapshot = {
            "rows": rows,
            "tags": ["全部"] + [tag for tag in service.all_tags if tag != "全部"],
            "polling": service.is_polling,
            "poll_interval": service.poll_interval,
            "poll_round": self._poll_round,
        }
        if self.on_snapshot is not None:
            self.on_snapshot(self.snapshot)

    def _on_status_change(self, idx, status) -> None:
        direction = "↑" if status.live_info.is_live else "↓"
        action = "开播" if status.live_info.is_live else "下播"
        self._log(f"{direction} {status.follower.name} {action}")
        self._emit_snapshot()

    def _on_error(self, message: str) -> None:
        self._log(f"监控异常：{redact_sensitive_text(str(message))}")

    def _on_poll_start(self, count: int) -> None:
        self._poll_round = count
        self._log(f"开始第 {count} 轮检测…")

    def _on_poll_end(self) -> None:
        self._emit_snapshot()
        self._log(f"轮询结束 {datetime.datetime.now():%H:%M:%S}")

    # ---- 通用 -----------------------------------------------------------

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _require(self) -> MonitorService:
        if self.service is None:
            raise RuntimeError("监控核心尚未就绪")
        return self.service

    # ---- 行操作（UI 调用） ----------------------------------------------

    def refresh(self) -> None:
        self._spawn(self._refresh_once())

    async def _refresh_once(self) -> None:
        try:
            service = self._require()
            if service.is_polling:
                self._log("当前检测尚未结束，已忽略重复刷新")
                return
            self._log("开始手动刷新…")
            await service.poll_all(None, force=True)
            self._emit_snapshot()
            self._log("手动刷新完成")
        except Exception as exc:
            self._log(f"手动刷新失败：{redact_sensitive_text(str(exc))}")

    def play(self, follower_index: int) -> None:
        self._spawn(self._play(follower_index))

    async def _play(self, follower_index: int) -> None:
        try:
            service = self._require()
            if follower_index not in service.followers:
                self._log("没有可用的选中项")
                return
            info = await service.get_stream_info(follower_index)
            follower = service.followers[follower_index].follower
            url = info.flv_url or info.m3u8_url or info.stream_url
            if not url:
                raise RuntimeError("插件没有返回可用流地址")
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
        except asyncio.CancelledError:
            raise
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
        self._spawn(self._stop_player())

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
        self._spawn(self._player_control(str(action or "")))

    async def _player_control(self, action: str) -> None:
        commands = {
            "toggle_pause": ("cycle", "pause"),
            "volume_up": ("add", "volume", "5"),
            "volume_down": ("add", "volume", "-5"),
            "toggle_mute": ("cycle", "mute"),
        }
        command = commands.get(action)
        if command is None:
            return
        process = self._player_process
        if process is None or process.poll() is not None:
            self._log("mpv 当前没有正在播放")
            return
        if not mpv_command(self._player_ipc, *command):
            self._log("无法连接 mpv 控制通道")

    def copy_stream(self, follower_index: int) -> None:
        self._spawn(self._copy_stream(follower_index))

    async def _copy_stream(self, follower_index: int) -> None:
        try:
            service = self._require()
            if follower_index not in service.followers:
                self._log("没有可用的选中项")
                return
            info = await service.get_stream_info(follower_index)
            url = info.flv_url or info.m3u8_url or info.stream_url
            if not url:
                raise RuntimeError("插件没有返回可用流地址")
            ok = await self._copy_clipboard(url)
            if ok:
                self._log("直播流地址已复制到剪贴板")
            else:
                self._log("复制失败：剪贴板不可用")
        except Exception as exc:
            self._log(f"获取直播流失败：{redact_sensitive_text(str(exc))}")

    @staticmethod
    async def _copy_clipboard(text: str) -> bool:
        """Windows 自带 clip.exe：URL 均为 ASCII，直接 utf-8 写入即可。"""
        try:
            process = await asyncio.create_subprocess_exec(
                "clip", stdin=asyncio.subprocess.PIPE
            )
            assert process.stdin is not None
            process.stdin.write(text.encode("utf-8", "replace"))
            process.stdin.close()
            await process.wait()
            return process.returncode == 0
        except (OSError, AssertionError):
            return False

    def open_web(self, follower_index: int) -> None:
        self._spawn(self._open_web(follower_index))

    async def _open_web(self, follower_index: int) -> None:
        try:
            service = self._require()
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
        self._spawn(self._toggle_enabled(follower_index))

    async def _toggle_enabled(self, follower_index: int) -> None:
        try:
            service = self._require()
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
                        message="快捷键停用",
                        is_live=False,
                    )
                )
                service.followers[follower_index] = replacement
            self._emit_snapshot()
            state = "已恢复监控" if updated.enabled else "已停止监控（配置保留）"
            self._log(f"{updated.name} {state}")
        except Exception as exc:
            self._log(redact_sensitive_text(str(exc)))

    # ---- 详情（同步纯函数，直接返回） -----------------------------------

    def get_detail(self, follower_index: int) -> dict | None:
        service = self.service
        if service is None:
            return None
        status = service.followers.get(follower_index)
        if status is None:
            return None
        detail = build_detail_view(
            status, service.get_platform_health(status.follower.platform)
        )
        return {
            "title": detail.title,
            "rows": [
                {"label": row.label, "value": row.value, "tone": row.tone}
                for row in detail.rows
            ],
        }

    # ---- 详情（同步纯函数，直接返回） -----------------------------------

    def get_detail(self, follower_index: int) -> dict | None:
        service = self.service
        if service is None:
            return None
        status = service.followers.get(follower_index)
        if status is None:
            return None
        detail = build_detail_view(
            status, service.get_platform_health(status.follower.platform)
        )
        return {
            "title": detail.title,
            "rows": [
                {"label": row.label, "value": row.value, "tone": row.tone}
                for row in detail.rows
            ],
        }

    # ---- P2 事务：编辑 ---------------------------------------------------

    def get_edit_payload(self, follower_index: int) -> dict | None:
        """详情+编辑合一的表单初值（已脱敏）。"""
        service = self.service
        if service is None:
            return None
        status = service.followers.get(follower_index)
        if status is None:
            return None
        form = follower_to_edit_payload(status.follower, redact=True)
        plugin = str(form.get("plugin", ""))
        platform = str(form.get("platform", ""))
        quality = str(form.get("quality", "best"))
        return {
            "idx": follower_index,
            "name": status.follower.name,
            "form": {
                "enabled": "true" if form.get("enabled", True) else "false",
                "name": form.get("name", ""),
                "tags": "|".join(form.get("tags", [])),
                "plugin": plugin,
                "fallback_plugins": "|".join(form.get("fallback_plugins", [])),
                "platform": platform,
                "url": form.get("url", ""),
                "quality": quality,
                "sport_id": form.get("sport_id", ""),
                "extra": json.dumps(
                    form.get("extra", {}), ensure_ascii=False, sort_keys=True, indent=2
                ),
            },
            "pluginOptions": list_plugins(),
            "qualityOptions": [
                {"label": label, "value": value}
                for label, value in quality_options(plugin, platform, quality)
            ],
        }

    async def preview_edit(self, follower_index: int, values: dict) -> tuple[bool, str]:
        """校验表单并生成脱敏差异文本；成功后暂存待确认。"""
        try:
            service = self._require()
            status = service.followers.get(follower_index)
            if status is None or status.is_checking:
                raise ValueError("编辑目标已变化或正在检测，请重新打开编辑器")
            candidate_values = dict(values)
            old_plugin = status.follower.plugin
            new_plugin = str(candidate_values.get("plugin", old_plugin) or old_plugin).strip()
            new_platform = str(
                candidate_values.get("platform", status.follower.platform)
                or status.follower.platform
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
            return True, _format_changes("配置修改预览（尚未保存）", preview.changes, EDIT_LABELS)
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    async def confirm_edit(self) -> tuple[bool, str]:
        try:
            service = self._require()
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
            return True, f"已保存 {updated.name} 的配置修改"
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    # ---- P2 事务：监控设置 ------------------------------------------------

    async def get_settings(self) -> dict:
        service = self._require()
        settings = service.config_manager.read_monitoring_settings()
        return {
            key: ("true" if value is True else "false" if value is False else str(value))
            for key, value in settings.items()
        }

    async def preview_settings(self, values: dict) -> tuple[bool, str]:
        try:
            service = self._require()
            before = service.config_manager.read_monitoring_settings()
            preview = service.config_manager.preview_monitoring_settings(values)
            if not preview.has_changes:
                raise ValueError("监控设置没有实质变化")
            self._pending["settings"] = {"before": before, "preview": preview}
            return True, _format_changes(
                "监控设置预览（尚未保存）", preview.changes, SETTINGS_LABELS
            )
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    async def confirm_settings(self) -> tuple[bool, str]:
        try:
            service = self._require()
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
            return True, "监控设置已保存并立即生效"
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    # ---- P2 事务：平台代理 ------------------------------------------------

    async def get_proxy(self) -> dict:
        service = self._require()
        payload = {
            platform: service.cfg.platform_proxies.get(platform, "")
            for platform in sorted(PROXY_PLATFORMS)
        }
        return payload

    async def save_proxy(self, values: dict) -> tuple[bool, str]:
        try:
            service = self._require()
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
            return True, f"平台代理设置已保存：{description or '使用默认规则'}"
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    async def test_proxy(self, values: dict) -> list[dict]:
        normalized = {
            platform: str(values.get(platform, "")).strip()
            for platform in sorted(PROXY_PLATFORMS)
        }
        results = await asyncio.gather(*(
            self._test_proxy_endpoint(platform, normalized[platform])
            for platform in sorted(PROXY_PLATFORMS)
        ))
        return list(results)

    @staticmethod
    async def _test_proxy_endpoint(platform: str, raw_value: str) -> dict:
        explicit = raw_value.strip()
        proxy_url = normalize_proxy_value(explicit) if explicit else proxy_for_platform(platform)
        if proxy_url is None:
            return {"platform": platform, "status": "direct", "label": "直连", "detail": "该平台不会经过代理"}
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

    # ---- P2 事务：导入 ----------------------------------------------------

    async def get_import_preview(self, url: str, tag: str) -> tuple[bool, dict | str]:
        try:
            service = self._require()
            preview = await ImportPreviewService(service.config_manager).preview(
                str(url or ""), str(tag or "")
            )
            self._pending.pop("import", None)
            if preview.can_confirm:
                self._pending["import"] = preview
            return True, {
                "previewText": _format_import_preview(preview),
                "canConfirm": preview.can_confirm,
                "requiresOverride": preview.requires_conflict_override,
                "confirmLabel": "确认冲突后导入" if preview.requires_conflict_override else "确认导入",
            }
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    async def confirm_import(self) -> tuple[bool, str]:
        try:
            service = self._require()
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
            return True, f"已导入 {follower.name}"
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    # ---- P2 事务：删除 ----------------------------------------------------

    async def preview_delete(self, follower_index: int) -> tuple[bool, str]:
        try:
            service = self._require()
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
            text = (
                "即将永久删除这个直播间：\n\n"
                f"名称：{follower.name}\n"
                f"平台：{follower.platform}\n"
                f"标签：{tags}\n\n"
                "删除后会立即写入关注列表，此操作不能在程序内撤销。"
            )
            return True, text
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    def _ensure_delete_is_idle(self, service: MonitorService) -> None:
        if service.is_polling or any(status.is_checking for status in service.followers.values()):
            raise ValueError("状态检测进行中，请等待本轮完成后再删除")
        if any(not task.done() for task in service._stream_deadline_tasks.values()):
            raise ValueError("正在获取直播流，请稍后再删除")

    async def confirm_delete(self) -> tuple[bool, str]:
        try:
            service = self._require()
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
                await self._stop_player()
            self._emit_snapshot()
            return True, f"已删除直播间：{removed.name}"
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))

    # ---- P2 事务：通知开关 ------------------------------------------------

    def toggle_notifications(self) -> None:
        self._spawn(self._toggle_notifications_impl())

    async def _toggle_notifications_impl(self) -> tuple[bool, str]:
        try:
            service = self._require()
            candidate = copy.deepcopy(service.cfg)
            candidate.notifications_enabled = not service.cfg.notifications_enabled
            await asyncio.to_thread(service.config_manager.save_config, candidate)
            service.cfg.notifications_enabled = candidate.notifications_enabled
            state = "开启" if candidate.notifications_enabled else "关闭"
            self._log(f"桌面通知已{state}")
            self._emit_snapshot()
            return True, f"桌面通知已{state}"
        except Exception as exc:
            return False, redact_sensitive_text(str(exc))
