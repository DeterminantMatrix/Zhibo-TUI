"""Single-instance guard with a tiny localhost command channel."""
from __future__ import annotations

import hashlib
import os
import socket
import threading
from pathlib import Path
from typing import Callable


CommandHandler = Callable[[str], None]

HOST = "127.0.0.1"
COMMAND_SHOW = "show"
# 同一 key 依次尝试的候选端口数：上一个实例异常退出后，其端口会处于
# TIME_WAIT 残留约一分钟（或被无关程序占用），单一固定端口会把这种
# 暂时性占用误报成"无法连接现有实例"并拒绝启动。
PORT_CANDIDATES = 8


def instance_key() -> str:
    """Return a stable key for this project checkout."""
    from zhibo import app_root

    return str(app_root()).casefold()


def _port_candidates(key: str):
    for index in range(PORT_CANDIDATES):
        digest = hashlib.sha1(f"{key}#{index}".encode("utf-8")).hexdigest()
        yield 45000 + (int(digest[:8], 16) % 10000)


def _ping(port: int, timeout: float = 0.2) -> bool:
    """探测端口上是否有本程序的活实例（保留命令 ping 不触发业务回调）。"""
    try:
        with socket.create_connection((HOST, port), timeout=timeout) as client:
            client.settimeout(timeout)
            client.sendall(b"ping")
            return client.recv(2) == b"ok"
    except OSError:
        return False


class SingleInstance:
    """Prevent opening a second app instance and receive simple commands."""

    def __init__(self, key: str | None = None, command_handler: CommandHandler | None = None):
        self.key = key or instance_key()
        self.port = next(_port_candidates(self.key))
        self._command_handler = command_handler
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def acquire(self) -> bool:
        # 先扫描全部候选端口：任何一个有活实例响应都视为"已在运行"，
        # 这样即使实例因端口残留换到了后面的候选，后来者也能找到它。
        for port in _port_candidates(self.key):
            if _ping(port):
                return False
        for port in _port_candidates(self.key):
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                server.bind((HOST, port))
                server.listen(5)
            except OSError:
                # 端口被残留占用、落在系统保留区间或被无关程序持有：换下一个候选。
                server.close()
                continue

            self.port = port
            self._socket = server
            self._thread = threading.Thread(target=self._serve, name="zhibo-single-instance", daemon=True)
            self._thread.start()
            return True
        return False

    def set_command_handler(self, command_handler: CommandHandler) -> None:
        self._command_handler = command_handler

    def close(self) -> None:
        self._stopped.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def _serve(self) -> None:
        while not self._stopped.is_set():
            try:
                client, _ = self._socket.accept() if self._socket else (None, None)
            except OSError:
                break
            if client is None:
                continue
            with client:
                try:
                    client.settimeout(1.0)
                    command = client.recv(128).decode("utf-8", errors="ignore").strip()
                    if command == "ping":
                        # 存活探测：不触发业务回调（旧版本实例会把它当成
                        # 未知命令忽略后同样回复 ok，双向兼容）。
                        client.sendall(b"ok")
                    elif command and self._command_handler is not None:
                        try:
                            self._command_handler(command)
                        except Exception:
                            continue
                        client.sendall(b"ok")
                except (OSError, socket.timeout):
                    continue


def notify_existing_instance(command: str = COMMAND_SHOW, key: str | None = None) -> bool:
    for port in _port_candidates(key or instance_key()):
        try:
            with socket.create_connection((HOST, port), timeout=0.5) as client:
                client.settimeout(0.5)
                client.sendall(command.encode("utf-8"))
                if client.recv(2) == b"ok":
                    return True
        except OSError:
            continue
    return False
