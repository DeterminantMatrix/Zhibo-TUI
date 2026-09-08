"""Python → JS 推送通道 — 线程安全队列 + 100ms 批量 evaluate_js。

任意线程调用 submit()；分发线程在 GUI 就绪后把积压事件合并成
一次 window.zhibo.onEvents([...]) 调用，避免高频快照逐条穿透。
"""
from __future__ import annotations

import json
import queue
import threading


class EventPusher:
    BATCH_INTERVAL = 0.1

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._window = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def attach(self, window) -> None:
        self._window = window

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="zhibo-web-push", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def submit(self, kind: str, payload) -> None:
        self._queue.put({"kind": kind, "payload": payload})

    # ---- 分发循环 -------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(self.BATCH_INTERVAL):
            batch = self._drain()
            if not batch:
                continue
            self._dispatch(batch)

    def _drain(self) -> list[dict]:
        batch = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                return batch

    def _dispatch(self, batch: list[dict]) -> None:
        window = self._window
        if window is None:
            return
        try:
            payload = json.dumps(batch, ensure_ascii=False, default=str)
            window.evaluate_js(f"window.zhibo && window.zhibo.onEvents({payload})")
        except Exception:
            # GUI 未就绪或正在关闭：事件丢弃（快照会随下一次轮询重发）。
            pass
