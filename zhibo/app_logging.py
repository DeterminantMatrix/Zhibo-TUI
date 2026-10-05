"""Application logging with reusable secret redaction helpers."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from zhibo.private_data import ensure_private_parent, log_file_path


LOG_FILE = log_file_path()
LOG_DIR = LOG_FILE.parent

# Keep canonical names readable for callers; matching normalizes punctuation and
# case so headers such as X-API-Key and JSON fields such as access_token work.
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "authorization",
        "x-authorization",
        "auth",
        "authentication",
        "auth-key",
        "authkey",
        "x-auth",
        "x-auth-token",
        "proxy-authorization",
        "token",
        "access-token",
        "refresh-token",
        "id-token",
        "cookie",
        "set-cookie",
        "password",
        "passwd",
        "secret",
        "client-secret",
        "credential",
        "credentials",
        "api-key",
        "x-api-key",
        "access-key",
        "secret-key",
        "private-key",
        "key",
        "stream-key",
        "imei",
        "dun-imei",
        "device-id",
        "session",
        "session-id",
        "signature",
        "sig",
        "sign",
        "signed",
        "signing-key",
        "signingkey",
        "x-signature",
        "hmac",
        "jwt",
        "oauth",
        "policy",
        "hdnts",
        "hdnea",
        "csrf",
        "xsrf",
    }
)
_NORMALIZED_SENSITIVE_NAMES = {
    re.sub(r"[^a-z0-9]", "", name.casefold()) for name in SENSITIVE_FIELD_NAMES
}
# Keep suffix matching deliberately narrow: it catches conventional variants
# such as ``session_token`` and ``request_auth`` without treating ordinary
# fields like ``monkey`` or ``design`` as credentials merely because they end
# with ``key`` / ``sign``.
_SENSITIVE_SUFFIXES = (
    "token",
    "secret",
    "password",
    "apikey",
    "credential",
    "auth",
    "signature",
)

_URL_IN_TEXT_RE = re.compile(r"(?i)\b(?:https?|wss?)://[^\s<>'\"]+")
_RAW_SENSITIVE_VALUE_RE = re.compile(
    r"""(?imx)
    (?P<prefix>
        (?<![a-z0-9_.?&-])
        (?:
            proxy[-_]?authorization | authorization |
            (?:access|refresh|id)[-_]?token | token |
            set[-_]?cookie | cookie |
            client[-_]?secret | secret |
            x[-_]?api[-_]?key | api[-_]?key |
            password | passwd |
            dun[-_]?imei | imei | device[-_]?id |
            session(?:[-_]?id)? | signature | sig
        )
        \s*[:=]\s*
    )
    (?P<value>[^\r\n]*)
    """
)
_BARE_AUTH_VALUE_RE = re.compile(
    r"(?i)\b(?:bearer|basic|token)\s+[a-z0-9._~+/=-]{6,}"
)
_KEY_VALUE_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
        (?P<key>"[^"\r\n]+"|'[^'\r\n]+'|[a-z][a-z0-9_.-]*)
        \s*[:=]\s*
    )
    (?P<value>
        "(?:\\.|[^"\\\r\n])*"
        | '(?:\\.|[^'\\\r\n])*'
        | (?:bearer|basic|token)\s+[^\s,;}&\]\r\n]+
        | [^\s,;}&\]\r\n]+
    )
    """
)
_QUERY_PAIR_RE = re.compile(r"(?P<prefix>(?:^|[&;])(?P<key>[^=&;\s]+)=)(?P<value>[^&;\s#]*)")

_CONFIGURED = False


def _normalized_field_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().casefold())


def is_sensitive_field(name: object) -> bool:
    """Return whether a mapping, JSON, header, or query key should be hidden."""
    normalized = _normalized_field_name(name)
    return bool(
        normalized
        and (
            normalized in _NORMALIZED_SENSITIVE_NAMES
            or normalized.endswith(_SENSITIVE_SUFFIXES)
        )
    )


def _redact_query_values(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = unquote_plus(match.group("key"))
        return match.group("prefix") + ("***" if is_sensitive_field(key) else match.group("value"))

    return _QUERY_PAIR_RE.sub(replace, value)


def redact_url(value: object) -> str:
    """Redact URL user info and sensitive query or fragment parameters."""
    text = str(value)
    try:
        parsed = urlsplit(text)
    except ValueError:
        return _redact_query_values(text)

    if not parsed.scheme or not parsed.netloc:
        return _redact_query_values(text)

    netloc = parsed.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            _redact_query_values(parsed.query),
            _redact_query_values(parsed.fragment),
        )
    )


def redact_sensitive_mapping(value: Any) -> Any:
    """Return a recursively redacted copy of a mapping/list payload.

    This is useful before structured diagnostics are passed to a logger. It
    preserves non-string scalar values and never mutates the caller's object.
    """
    if isinstance(value, Mapping):
        return {
            key: "***" if is_sensitive_field(key) else redact_sensitive_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_mapping(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _redact_key_value_match(match: re.Match[str]) -> str:
    key = match.group("key").strip("'\"")
    if is_sensitive_field(key):
        return match.group("prefix") + "***"
    return match.group(0)


def redact_sensitive_text(value: object) -> str:
    """Remove credential-like values before text is written to disk or shown."""
    text = str(value)
    text = _URL_IN_TEXT_RE.sub(lambda match: redact_url(match.group(0)), text)
    text = _RAW_SENSITIVE_VALUE_RE.sub(lambda match: match.group("prefix") + "***", text)
    text = _BARE_AUTH_VALUE_RE.sub("***", text)
    return _KEY_VALUE_RE.sub(_redact_key_value_match, text)


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive_text(record.getMessage())
        record.args = ()
        return True


class _RedactingFormatter(logging.Formatter):
    """Redact the final rendered record, including exception and stack traces."""

    def format(self, record: logging.LogRecord) -> str:
        # A prior handler may have cached an unredacted exception string on this
        # shared record. Re-render it here, then leave the cached value redacted
        # so a later handler cannot append the raw traceback.
        record.exc_text = None
        if getattr(record, "stack_info", None):
            record.stack_info = redact_sensitive_text(record.stack_info)
        rendered = super().format(record)
        if record.exc_text:
            record.exc_text = redact_sensitive_text(record.exc_text)
        return redact_sensitive_text(rendered)


def setup_logging() -> Path:
    """Configure the shared application logger once and return the log file path."""
    global _CONFIGURED
    if _CONFIGURED:
        return LOG_FILE

    ensure_private_parent(LOG_FILE)
    logger = logging.getLogger("zhibo")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    expected_path = str(LOG_FILE.resolve())
    handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "") == expected_path
    ]
    if not handlers:
        handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        handler.addFilter(_RedactingFilter())
        logger.addHandler(handler)
    else:
        for handler in handlers:
            if not any(isinstance(filter_, _RedactingFilter) for filter_ in handler.filters):
                handler.addFilter(_RedactingFilter())
            if not isinstance(handler.formatter, _RedactingFormatter):
                handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))

    _CONFIGURED = True
    return LOG_FILE


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
