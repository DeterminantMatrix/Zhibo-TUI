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


def instance_key() -> str:
    """Return a stable key for this project checkout."""
    return str(Path(__file__).resolve().parent).casefold()


def _port_for_key(key: str) -> int:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return 45000 + (int(digest[:8], 16) % 10000)


class SingleInstance:
    """Prevent opening a second app instance and receive simple commands."""

    def __init__(self, key: str | None = None, command_handler: CommandHandler | None = None):
        self.key = key or instance_key()
        self.port = _port_for_key(self.key)
        self._command_handler = command_handler
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def acquire(self) -> bool:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            server.bind((HOST, self.port))
            server.listen(5)
        except OSError:
            server.close()
            return False

        self._socket = server
        self._thread = threading.Thread(target=self._serve, name="zhibo-single-instance", daemon=True)
        self._thread.start()
        return True

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
                    command = client.recv(128).decode("utf-8", errors="ignore").strip()
                except OSError:
                    continue
            if command and self._command_handler is not None:
                self._command_handler(command)


def notify_existing_instance(command: str = COMMAND_SHOW, key: str | None = None) -> bool:
    port = _port_for_key(key or instance_key())
    try:
        with socket.create_connection((HOST, port), timeout=0.5) as client:
            client.sendall(command.encode("utf-8"))
        return True
    except OSError:
        return False
