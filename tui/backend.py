"""TUI ↔ zhibo.monitor 桥接层。

与 webui 时代的线程桥不同：Textual 本身就是 asyncio，监控服务直接跑在
UI 的事件循环里，回调可以安全地同步更新界面。播放器状态同样只在本
循环内访问。UI 通过三个回调接收事件：on_snapshot / on_log / on_player。
"""
from __future__ import annotations

import asyncio
import copy
import datetime
import subprocess
import webbrowser

from zhibo.app_logging import redact_sensitive_text
from zhibo.config import follower_key
from zhibo.detail import build_detail_view
from zhibo.desktop import mpv_command, new_mpv_ipc_path, play_url
from zhibo.monitor import FollowerStatus, MonitorService, StatusHistoryEntry
from zhibo.plugins.base import stream_candidate_urls
from zhibo.plugins.bounded_executor import shutdown_plugin_workers
from zhibo.proxy_config import proxy_for_platform
from zhibo.viewmodel import sort_snapshots, status_snapshot, web_url_for_snapshot


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
