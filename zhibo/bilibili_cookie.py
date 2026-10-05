"""Safely validate and update the Bilibili portion of a Netscape cookie jar.

The UI passes user-pasted cookies to this module as an in-memory string.  This
module deliberately has no logging and never includes a cookie value (or the
input text) in a result or exception.  It replaces only Bilibili records in a
shared ``cookies.txt`` file so a YouTube cookie jar is not accidentally erased.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from zhibo.private_data import cookie_file_path, ensure_private_parent


NETSCAPE_HEADER = "# Netscape HTTP Cookie File"
HTTPONLY_PREFIX = "#HttpOnly_"
MAX_COOKIE_INPUT_BYTES = 1_024 * 1_024
MAX_COOKIE_LINES = 10_000
MAX_COOKIE_LINE_LENGTH = 16_384
COOKIE_LOCK_TIMEOUT_SECONDS = 10.0
COOKIE_LOCK_RETRY_SECONDS = 0.05

_EXPIRY_RE = re.compile(r"^[0-9]{1,19}$")

__all__ = [
    "BilibiliCookieError",
    "BilibiliCookieStoreError",
    "BilibiliCookieUpdateResult",
    "BilibiliCookieValidationError",
    "CookieRecord",
    "MAX_COOKIE_INPUT_BYTES",
    "parse_netscape_cookie_text",
    "update_bilibili_cookies",
]


class BilibiliCookieError(Exception):
    """Base class for cookie-update failures safe to show in the UI."""


class BilibiliCookieValidationError(BilibiliCookieError, ValueError):
    """The pasted cookie data is not a safe Bilibili Netscape cookie export."""


class BilibiliCookieStoreError(BilibiliCookieError, RuntimeError):
    """The cookie jar could not be safely read, merged, locked, or written."""


@dataclass(frozen=True, slots=True)
class CookieRecord:
    """One validated Netscape cookie record.

    Cookie values are intentionally not rendered by this module.  They remain
    private implementation data used only to produce the replacement file.
    """

    domain: str
    include_subdomains: bool
    path: str
    secure: bool
    expiry: int
    name: str
    value: str
    http_only: bool = False

    def serialized(self) -> str:
        domain = f"{HTTPONLY_PREFIX}{self.domain}" if self.http_only else self.domain
        return "\t".join(
            (
                domain,
                "TRUE" if self.include_subdomains else "FALSE",
                self.path,
                "TRUE" if self.secure else "FALSE",
                str(self.expiry),
                self.name,
                self.value,
            )
        )


@dataclass(frozen=True, slots=True)
class BilibiliCookieUpdateResult:
    """Content-free outcome of a successful cookie update."""

    bilibili_records_updated: int
    non_bilibili_records_preserved: int


def _raise_invalid(message: str) -> None:
    """Raise a fixed error; never put rejected values in an exception."""
    raise BilibiliCookieValidationError(message)


def _contains_disallowed_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _is_valid_domain(domain: str) -> bool:
    bare_domain = domain[1:] if domain.startswith(".") else domain
    if not bare_domain or len(bare_domain) > 253 or _contains_disallowed_control(bare_domain):
        return False
    if any(char.isspace() or char in "/:@" for char in bare_domain):
        return False
    labels = bare_domain.split(".")
    return all(label and not label.startswith("-") and not label.endswith("-") for label in labels)


def _is_valid_name(name: str) -> bool:
    if not name or len(name) > 512 or _contains_disallowed_control(name):
        return False
    return not any(char.isspace() or char in "()<>@,;:\\\"/[]?={}=" for char in name)


def _is_bilibili_domain(domain: str) -> bool:
    bare_domain = domain.lstrip(".").casefold()
    return bare_domain == "bilibili.com" or bare_domain.endswith(".bilibili.com")


def _is_bilibili_root_domain(domain: str) -> bool:
    return domain.lstrip(".").casefold() == "bilibili.com"


def _validate_record(parts: list[str], *, http_only: bool) -> CookieRecord:
    if len(parts) != 7:
        _raise_invalid("Cookie 条目格式无效，请重新导出 Netscape cookies.txt。")

    raw_domain, raw_subdomains, path, raw_secure, raw_expiry, name, value = parts
    domain = raw_domain.strip().casefold()
    if not _is_valid_domain(domain):
        _raise_invalid("Cookie 域名格式无效，请重新导出 Netscape cookies.txt。")

    subdomains = raw_subdomains.strip().casefold()
    secure = raw_secure.strip().casefold()
    if subdomains not in {"true", "false"} or secure not in {"true", "false"}:
        _raise_invalid("Cookie 标志格式无效，请重新导出 Netscape cookies.txt。")
    if not path.startswith("/") or len(path) > 2_048 or _contains_disallowed_control(path):
        _raise_invalid("Cookie 路径格式无效，请重新导出 Netscape cookies.txt。")
    if not _EXPIRY_RE.fullmatch(raw_expiry.strip()):
        _raise_invalid("Cookie 有效期格式无效，请重新导出 Netscape cookies.txt。")
    if not _is_valid_name(name):
        _raise_invalid("Cookie 名称格式无效，请重新导出 Netscape cookies.txt。")
    if len(value) > MAX_COOKIE_LINE_LENGTH or "\t" in value or "\r" in value or "\n" in value:
        _raise_invalid("Cookie 内容格式无效，请重新导出 Netscape cookies.txt。")

    return CookieRecord(
        domain=domain,
        include_subdomains=subdomains == "true",
        path=path,
        secure=secure == "true",
        expiry=int(raw_expiry),
        name=name,
        value=value,
        http_only=http_only,
    )


def parse_netscape_cookie_text(text: str, *, require_header: bool = True) -> tuple[CookieRecord, ...]:
    """Parse a bounded Netscape cookie export without exposing its values.

    The special Netscape ``#HttpOnly_`` prefix is a cookie record, not a
    comment, and is retained so browsers/tools keep its semantics after an
    update.  Other comments are intentionally discarded on write.
    """
    if not isinstance(text, str):
        _raise_invalid("Cookie 内容必须是 Netscape cookies.txt 文本。")
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        _raise_invalid("Cookie 文本编码无效，请重新导出。")
    if encoded_size > MAX_COOKIE_INPUT_BYTES:
        _raise_invalid("Cookie 内容超过 1 MiB 限制，请重新导出所需站点的 Cookie。")

    lines = text.lstrip("\ufeff").splitlines()
    if len(lines) > MAX_COOKIE_LINES:
        _raise_invalid("Cookie 行数超过安全限制，请重新导出所需站点的 Cookie。")
    if not lines:
        _raise_invalid("Cookie 内容为空，请重新导出 Netscape cookies.txt。")

    has_header = False
    records: list[CookieRecord] = []
    for line in lines:
        if len(line) > MAX_COOKIE_LINE_LENGTH:
            _raise_invalid("Cookie 单行长度超过安全限制，请重新导出。")
        stripped = line.strip()
        if stripped.casefold() == NETSCAPE_HEADER.casefold():
            has_header = True
            continue
        if not line:
            continue

        http_only = line.startswith(HTTPONLY_PREFIX)
        if http_only:
            line = line[len(HTTPONLY_PREFIX) :]
        elif line.startswith("#"):
            continue

        records.append(_validate_record(line.split("\t"), http_only=http_only))

    if require_header and not has_header:
        _raise_invalid("粘贴内容不是 Netscape cookies.txt 格式。")
    if not records:
        _raise_invalid("Cookie 内容没有有效条目，请重新导出 Netscape cookies.txt。")
    return tuple(records)


def _require_usable_bilibili_session(records: tuple[CookieRecord, ...], *, now: float | None = None) -> tuple[CookieRecord, ...]:
    bilibili_records = tuple(record for record in records if _is_bilibili_domain(record.domain))
    if not bilibili_records:
        _raise_invalid("未找到 bilibili.com 的有效 Cookie 条目。")

    # Cookie keys are unique within a jar.  Ambiguous repeated Bilibili keys
    # are rejected rather than guessing which credential the server will use.
    seen_keys: set[tuple[str, str, str]] = set()
    for record in bilibili_records:
        key = (record.domain, record.path, record.name)
        if key in seen_keys:
            _raise_invalid("B站 Cookie 包含重复条目，请重新导出。")
        seen_keys.add(key)

    timestamp = time.time() if now is None else now
    usable_sessdata = any(
        _is_bilibili_root_domain(record.domain)
        and record.name == "SESSDATA"
        and bool(record.value)
        and (record.expiry == 0 or record.expiry > timestamp)
        for record in bilibili_records
    )
    if not usable_sessdata:
        _raise_invalid("未找到有效的 bilibili.com SESSDATA 条目，请重新登录并导出。")
    return bilibili_records


def _read_existing_records(path: Path) -> tuple[CookieRecord, ...]:
    if not path.exists():
        return ()
    if path.is_symlink() or not path.is_file():
        raise BilibiliCookieStoreError("现有 Cookie 文件无法安全合并，未进行更新。")
    try:
        data = path.read_bytes()
    except OSError:
        raise BilibiliCookieStoreError("无法读取现有 Cookie 文件，未进行更新。") from None
    if len(data) > MAX_COOKIE_INPUT_BYTES:
        raise BilibiliCookieStoreError("现有 Cookie 文件超过安全限制，未进行更新。")
    if not data.strip():
        return ()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BilibiliCookieStoreError("现有 Cookie 文件不是可安全合并的 Netscape 格式，未更新。") from None
    try:
        return parse_netscape_cookie_text(text)
    except BilibiliCookieValidationError:
        raise BilibiliCookieStoreError("现有 Cookie 文件不是可安全合并的 Netscape 格式，未更新。") from None


def _lock_file_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _try_lock_handle(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _cookie_write_lock(path: Path, *, timeout: float = COOKIE_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialize the read/merge/replace transaction across processes."""
    lock_path = _lock_file_path(path)
    try:
        ensure_private_parent(lock_path)
        lock_path.touch(exist_ok=True)
        with lock_path.open("r+b") as handle:
            # Windows' byte-range lock needs a byte to lock.
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _try_lock_handle(handle)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise BilibiliCookieStoreError("B站 Cookie 正在由另一进程更新，请稍后重试。") from None
                    time.sleep(COOKIE_LOCK_RETRY_SECONDS)
            try:
                yield
            finally:
                try:
                    _unlock_handle(handle)
                except OSError:
                    pass
    except BilibiliCookieStoreError:
        raise
    except OSError:
        raise BilibiliCookieStoreError("无法锁定 Cookie 文件，请检查私有目录权限后重试。") from None


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows and explicitly selected user paths may not support POSIX mode
        # bits.  The atomic write still avoids a partially written credential.
        pass


def _atomic_write_cookie_file(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        ensure_private_parent(path)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            _restrict_file(temporary_path)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _restrict_file(path)
    except OSError:
        raise BilibiliCookieStoreError("无法安全保存 B站 Cookie，请检查私有目录权限后重试。") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _render_cookie_jar(records: tuple[CookieRecord, ...]) -> str:
    lines = [NETSCAPE_HEADER, "# Managed by Zhibo; do not edit during an update.", ""]
    lines.extend(record.serialized() for record in records)
    return "\n".join(lines) + "\n"


def update_bilibili_cookies(
    netscape_text: str,
    *,
    cookie_file: Path | str | None = None,
) -> BilibiliCookieUpdateResult:
    """Atomically replace only Bilibili records in the private cookie jar.

    ``netscape_text`` must be a bounded Netscape export containing a usable
    root-domain ``SESSDATA`` record.  Non-Bilibili rows in pasted input are
    deliberately ignored.  Existing non-Bilibili rows are retained after
    validating the entire existing jar, so a malformed jar is never silently
    truncated while updating Bilibili authentication.
    """
    parsed_input = parse_netscape_cookie_text(netscape_text)
    bilibili_records = _require_usable_bilibili_session(parsed_input)
    destination = Path(cookie_file) if cookie_file is not None else cookie_file_path()

    with _cookie_write_lock(destination):
        existing_records = _read_existing_records(destination)
        preserved_records = tuple(record for record in existing_records if not _is_bilibili_domain(record.domain))
        _atomic_write_cookie_file(destination, _render_cookie_jar((*preserved_records, *bilibili_records)))

    return BilibiliCookieUpdateResult(
        bilibili_records_updated=len(bilibili_records),
        non_bilibili_records_preserved=len(preserved_records),
    )
