"""CSV 关注列表与全局设置的加载、保存。

The legacy loading functions in this module deliberately remain permissive so
an existing configuration can still be opened and migrated.  UI edits should
use the explicit edit helpers below instead: they validate every field before
writing and only replace the CSV after a complete, valid table has been
rendered to a sibling temporary file.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

import yaml

from zhibo import app_root, is_frozen
from zhibo.app_logging import is_sensitive_field, redact_sensitive_mapping, redact_sensitive_text, redact_url
from zhibo.models import AppConfig, Follower

BASE_DIR = app_root()
DEFAULT_CSV_FILE = BASE_DIR / "followers.csv"
DEFAULT_YAML_FILE = BASE_DIR / "followers.yaml"

BUILTIN_STREAM_PLUGINS = {"streamget", "streamlink"}
FOLLOWER_COLUMNS = [
    "enabled",
    "name",
    "tags",
    "plugin",
    "fallback_plugins",
    "platform",
    "url",
    "quality",
    "sport_id",
    "extra",
]
TOP_CONFIG_DEFAULTS = {
    "poll_interval": 60,
    "max_concurrent_checks": 8,
    "failure_backoff_after": 3,
    "failure_backoff_polls": 2,
    "notifications_enabled": True,
    "platform_proxies": {},
}


# Fields intentionally accepted by the editing API.  Keeping this allow-list
# small prevents a screen or an imported payload from accidentally persisting
# runtime-only fields such as an authorization header.
FOLLOWER_EDIT_FIELD_ORDER = (
    "enabled",
    "name",
    "tags",
    "plugin",
    "fallback_plugins",
    "platform",
    "url",
    "quality",
    "sport_id",
    "extra",
)
FOLLOWER_EDIT_FIELDS = frozenset(FOLLOWER_EDIT_FIELD_ORDER)

# These are deliberately separate from ``TOP_CONFIG_DEFAULTS``: defaults are
# also used to recover a legacy CSV at load time, while the edit API must
# reject an unsafe user-entered value instead of silently clamping it.
MONITORING_SETTINGS_FIELD_ORDER = (
    "poll_interval",
    "max_concurrent_checks",
    "failure_backoff_after",
    "failure_backoff_polls",
    "notifications_enabled",
)
MONITORING_SETTINGS_FIELDS = frozenset(MONITORING_SETTINGS_FIELD_ORDER)
MONITORING_SETTINGS_LIMITS = {
    # A shorter interval can rapidly fan out into many platform requests;
    # callers still have the manual refresh action for an immediate check.
    "poll_interval": (5, 3600),
    # Each check can start an isolated plugin process, so keep the upper bound
    # suitable for a desktop client rather than allowing process exhaustion.
    "max_concurrent_checks": (1, 16),
    "failure_backoff_after": (1, 20),
    "failure_backoff_polls": (1, 60),
}

CONFIG_LOCK_TIMEOUT_SECONDS = 10.0
CONFIG_LOCK_RETRY_SECONDS = 0.05

_PLUGIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_OPAQUE_ROOM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_QUERY_FIELD_SPLIT_RE = re.compile(r"[&;]")
_TAG_SPLIT_RE = re.compile(r"[|,;，；]")

# These parameters identify an advertising/share click, rather than the room
# being monitored.  Query parameters not listed here are intentionally kept:
# services such as YouTube legitimately use them to identify a video or room.
# ``utm_*`` is a family, so it is handled by the helper below instead of this
# finite allow-list.
_TRACKING_QUERY_PARAMETERS = frozenset(
    {
        "_ga",
        "_gl",
        "dclid",
        "fbclid",
        "gclid",
        "gbraid",
        "igshid",
        "li_fat_id",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "msclkid",
        "srsltid",
        "ttclid",
        "twclid",
        "vero_id",
        "wbraid",
        "yclid",
    }
)

_MAX_NAME_LENGTH = 120
_MAX_PLATFORM_LENGTH = 64
_MAX_QUALITY_LENGTH = 64
_MAX_URL_LENGTH = 2048
_MAX_TAG_COUNT = 16
_MAX_TAG_LENGTH = 64
_MAX_FALLBACK_COUNT = 8
_MAX_EXTRA_DEPTH = 5
_MAX_EXTRA_LIST_ITEMS = 50
_MAX_EXTRA_TEXT_LENGTH = 2048
_MAX_EXTRA_BYTES = 8192
_FORBIDDEN_EXTRA_CONTAINER_FIELDS = frozenset(
    {
        "header",
        "headers",
        "requestheader",
        "requestheaders",
        "httpheader",
        "httpheaders",
        "cookiejar",
    }
)


class FollowerValidationError(ValueError):
    """Validation failure that is safe to display without leaking a value."""

    def __init__(self, errors: Mapping[str, str] | str):
        if isinstance(errors, str):
            errors = {"follower": errors}
        self.errors = dict(errors)
        message = "；".join(f"{field}: {reason}" for field, reason in self.errors.items())
        super().__init__(f"关注项校验失败：{message}")


class MonitoringSettingsValidationError(ValueError):
    """A display-safe validation error for global monitoring settings."""

    def __init__(self, errors: Mapping[str, str] | str):
        if isinstance(errors, str):
            errors = {"settings": errors}
        self.errors = dict(errors)
        message = "；".join(f"{field}: {reason}" for field, reason in self.errors.items())
        super().__init__(f"监控设置校验失败：{message}")


@dataclass(frozen=True)
class FollowerEditPreview:
    """A display-safe normalized edit plus the fields that would change.

    ``before``, ``after`` and ``changes`` are redacted copies intended for a
    confirmation UI.  ``follower`` is the normalized value that can be passed
    to :meth:`ConfigManager.update_follower` or persisted after confirmation.
    """

    follower: Follower
    before: dict[str, Any]
    after: dict[str, Any]
    changes: dict[str, tuple[Any, Any]]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


@dataclass(frozen=True)
class MonitoringSettingsPreview:
    """A safe normalized monitoring-settings diff for a confirmation UI."""

    settings: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    changes: dict[str, tuple[Any, Any]]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


def _detect_encoding(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8-sig"


def _clean_text(value, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _clean_int(value, default: int, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, number)


def _clean_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y", "on", "是", "开"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "否", "关"}:
            return False
    return bool(value)


def _split_cell(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).replace("；", "|").replace(";", "|").split("|")
    return [item.strip() for item in raw_items if str(item).strip()]


def _normalize_plugin_list(value) -> list[str]:
    return [_clean_text(item).casefold() for item in _split_cell(value)]


def _normalize_tags(value) -> list[str]:
    tags = _split_cell(value)
    return tags or ["未分类"]


def _normalize_extra(value, *, strict: bool = False) -> dict:
    """Parse the extensible follower fields from YAML or the CSV JSON column."""
    if isinstance(value, dict):
        return dict(value)
    text = _clean_text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        if strict:
            raise ValueError("extra 不是有效 JSON") from None
        print("警告：extra 不是有效 JSON，已忽略")
        return {}
    if not isinstance(parsed, dict):
        if strict:
            raise ValueError("extra 必须是 JSON 对象")
        print("警告：extra 必须是 JSON 对象，已忽略")
        return {}
    return parsed


def _validation_error(field: str, reason: str) -> FollowerValidationError:
    """Build a field error without embedding the rejected user value."""
    return FollowerValidationError({field: reason})


def _contains_control_characters(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _edit_text(
    value: Any,
    field: str,
    *,
    required: bool = False,
    maximum: int,
    allow_whitespace: bool = True,
) -> str:
    """Normalize a short edit field while rejecting hidden/control input."""
    if isinstance(value, (Mapping, list, tuple, set)):
        raise _validation_error(field, "必须是文本")
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise _validation_error(field, "不能为空")
    if _contains_control_characters(text):
        raise _validation_error(field, "不能包含控制字符")
    if not allow_whitespace and any(char.isspace() for char in text):
        raise _validation_error(field, "不能包含空白字符")
    if len(text) > maximum:
        raise _validation_error(field, f"长度不能超过 {maximum} 个字符")
    return text


def _normalise_edit_tags(value: Any) -> list[str]:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, str):
        values = _TAG_SPLIT_RE.split(value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = list(value)
    else:
        raise _validation_error("tags", "必须是标签列表或分隔文本")

    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in values:
        tag = _edit_text(raw_tag, "tags", maximum=_MAX_TAG_LENGTH)
        if not tag:
            continue
        normalized = tag.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        tags.append(tag)

    if len(tags) > _MAX_TAG_COUNT:
        raise _validation_error("tags", f"最多允许 {_MAX_TAG_COUNT} 个标签")
    return tags or ["未分类"]


def _normalise_plugin_names(value: Any, field: str, *, required: bool, maximum: int) -> list[str]:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, str):
        values = _TAG_SPLIT_RE.split(value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = list(value)
    else:
        raise _validation_error(field, "必须是插件列表或分隔文本")

    plugins: list[str] = []
    seen: set[str] = set()
    for raw_plugin in values:
        plugin = _edit_text(raw_plugin, field, maximum=64, allow_whitespace=False).casefold()
        if not plugin:
            continue
        if not _PLUGIN_NAME_RE.fullmatch(plugin):
            raise _validation_error(field, "插件名只能包含小写字母、数字、_ 或 -")
        if plugin in seen:
            continue
        seen.add(plugin)
        plugins.append(plugin)

    if required and not plugins:
        raise _validation_error(field, "不能为空")
    if len(plugins) > maximum:
        raise _validation_error(field, f"最多允许 {maximum} 个插件")
    return plugins


def _normalise_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y", "on", "是", "启用"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "否", "禁用"}:
            return False
    raise _validation_error("enabled", "必须是 true 或 false")


def _url_has_sensitive_component(url: str) -> bool:
    """Return whether URL auth/query text would be redacted by the log policy."""
    try:
        parsed = urlsplit(url)
        # Accessing these attributes validates malformed ports and IPv6 hosts.
        _ = parsed.port
        _ = parsed.hostname
    except ValueError:
        return True

    if parsed.username is not None or parsed.password is not None:
        return True
    for component in (parsed.query, parsed.fragment):
        for pair in _QUERY_FIELD_SPLIT_RE.split(component):
            key = unquote_plus(pair.partition("=")[0]).strip()
            if key and is_sensitive_field(key):
                return True
    return redact_url(url) != url


def _normalise_edit_url(value: Any, *, plugin: str, platform: str) -> str:
    url = _edit_text(value, "url", required=True, maximum=_MAX_URL_LENGTH, allow_whitespace=False)
    is_fs_room = plugin == "fs1" or platform == "fs1"

    if is_fs_room and _OPAQUE_ROOM_ID_RE.fullmatch(url):
        return url
    if url.startswith("//"):
        url = "https:" + url

    try:
        parsed = urlsplit(url)
    except ValueError:
        raise _validation_error("url", "不是有效的直播间地址") from None
    if not parsed.scheme:
        url = "https://" + url
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise _validation_error("url", "不是有效的直播间地址") from None

    try:
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise _validation_error("url", "主机名或端口无效") from None

    if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
        raise _validation_error("url", "只允许 HTTP(S) 直播间地址")
    if parsed.username is not None or parsed.password is not None:
        raise _validation_error("url", "不能包含账号或密码")
    if port is not None and not 0 < port <= 65535:
        raise _validation_error("url", "端口无效")
    if _url_has_sensitive_component(url):
        raise _validation_error("url", "不能包含令牌、签名或会话参数")
    return url


def _parse_edit_extra(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            raise _validation_error("extra", "必须是有效的 JSON 对象") from None
        if isinstance(parsed, dict):
            return parsed
    raise _validation_error("extra", "必须是 JSON 对象")


def _normalise_extra_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_EXTRA_DEPTH:
        raise _validation_error("extra", f"嵌套层级不能超过 {_MAX_EXTRA_DEPTH}")

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _edit_text(raw_key, "extra", required=True, maximum=64)
            normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
            if is_sensitive_field(key) or normalized_key in _FORBIDDEN_EXTRA_CONTAINER_FIELDS:
                raise _validation_error("extra", "不能保存凭据、Cookie、认证头或设备标识字段")
            normalized[key] = _normalise_extra_value(raw_value, depth=depth + 1)
        return normalized

    if isinstance(value, list):
        if len(value) > _MAX_EXTRA_LIST_ITEMS:
            raise _validation_error("extra", f"列表最多允许 {_MAX_EXTRA_LIST_ITEMS} 项")
        return [_normalise_extra_value(item, depth=depth + 1) for item in value]

    if isinstance(value, str):
        if len(value) > _MAX_EXTRA_TEXT_LENGTH:
            raise _validation_error("extra", f"文本长度不能超过 {_MAX_EXTRA_TEXT_LENGTH} 个字符")
        if _contains_control_characters(value):
            raise _validation_error("extra", "不能包含控制字符")
        if redact_sensitive_text(value) != value or redact_url(value) != value:
            raise _validation_error("extra", "不能包含令牌、Cookie、密码或签名文本")
        return value

    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _validation_error("extra", "不能包含非有限数字")
        return value
    raise _validation_error("extra", "只支持 JSON 标量、列表和对象")


def _normalise_sport_id(value: Any) -> str:
    sport_id = _edit_text(value, "sport_id", maximum=64, allow_whitespace=False)
    if sport_id and not _OPAQUE_ROOM_ID_RE.fullmatch(sport_id):
        raise _validation_error("sport_id", "只能包含字母、数字、.、_、: 或 -")
    return sport_id


def follower_to_edit_payload(follower: Follower, *, redact: bool = True) -> dict[str, Any]:
    """Return a UI-shaped follower payload, separating ``sport_id`` from extra.

    The default is safe for a UI: legacy sensitive extra values and sensitive
    URL components are replaced with ``***``.  Internal patch merging uses
    ``redact=False`` and must never send that result to a log or screen.
    """
    extra = follower.extra if isinstance(follower.extra, dict) else {}
    # JSON round-tripping makes the returned payload independent of the model
    # without accepting arbitrary, non-serializable runtime objects.
    try:
        copied_extra = json.loads(json.dumps(extra, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        copied_extra = dict(extra)
    sport_id = copied_extra.pop("sport_id", "") if isinstance(copied_extra, dict) else ""
    payload: dict[str, Any] = {
        "enabled": follower.enabled,
        "name": follower.name,
        "tags": list(follower.tags or []),
        "plugin": follower.plugin,
        "fallback_plugins": list(follower.fallback_plugins or []),
        "platform": follower.platform,
        "url": follower.url,
        "quality": follower.quality,
        "sport_id": sport_id,
        "extra": copied_extra,
    }
    if not redact:
        return payload

    payload["name"] = redact_sensitive_text(payload["name"])
    payload["platform"] = redact_sensitive_text(payload["platform"])
    payload["url"] = redact_url(payload["url"])
    payload["quality"] = redact_sensitive_text(payload["quality"])
    payload["tags"] = [redact_sensitive_text(tag) for tag in payload["tags"]]
    payload["extra"] = redact_sensitive_mapping(payload["extra"])
    return payload


def _coerce_edit_payload(values: Mapping[str, Any] | Follower) -> dict[str, Any]:
    if isinstance(values, Follower):
        return follower_to_edit_payload(values, redact=False)
    if not isinstance(values, Mapping):
        raise _validation_error("follower", "必须是关注项字段对象")
    unknown = {str(key) for key in values} - FOLLOWER_EDIT_FIELDS
    if unknown:
        raise _validation_error("follower", "包含不支持的编辑字段")
    return dict(values)


def validate_follower_edit(
    values: Mapping[str, Any] | Follower,
    *,
    current: Follower | None = None,
    existing_followers: Sequence[Follower] = (),
    editing_index: int | None = None,
) -> Follower:
    """Validate and normalize a complete or partial follower edit.

    If ``current`` is supplied, omitted fields are retained, so callers can
    safely submit a small patch.  The returned object is detached from input
    mappings.  Sensitive extra keys/values and credential-bearing URLs are
    rejected rather than silently written to disk.
    """
    patch = _coerce_edit_payload(values)
    raw = follower_to_edit_payload(current, redact=False) if current is not None else {}
    raw.update(patch)

    plugin_values = _normalise_plugin_names(
        raw.get("plugin"), "plugin", required=True, maximum=1
    )
    plugin = plugin_values[0]
    platform = _edit_text(
        raw.get("platform"), "platform", maximum=_MAX_PLATFORM_LENGTH
    ).casefold()
    if plugin == "fs1" and not platform:
        platform = "fs1"

    name = _edit_text(raw.get("name"), "name", required=True, maximum=_MAX_NAME_LENGTH)
    quality = _edit_text(
        raw.get("quality", "best"), "quality", maximum=_MAX_QUALITY_LENGTH
    ) or "best"
    tags = _normalise_edit_tags(raw.get("tags"))
    enabled = _normalise_enabled(raw.get("enabled", True))
    fallback_plugins = _normalise_plugin_names(
        raw.get("fallback_plugins"), "fallback_plugins", required=False, maximum=_MAX_FALLBACK_COUNT
    )
    fallback_plugins = [candidate for candidate in fallback_plugins if candidate != plugin]
    url = _normalise_edit_url(raw.get("url"), plugin=plugin, platform=platform)

    extra = _normalise_extra_value(_parse_edit_extra(raw.get("extra")))
    if not isinstance(extra, dict):  # Kept for type checkers and defensive callers.
        raise _validation_error("extra", "必须是 JSON 对象")
    raw_sport_id = raw.get("sport_id", extra.pop("sport_id", ""))
    extra.pop("sport_id", None)
    sport_id = _normalise_sport_id(raw_sport_id)
    if sport_id:
        extra["sport_id"] = sport_id

    try:
        encoded_extra = json.dumps(extra, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise _validation_error("extra", "必须是可保存的 JSON 数据") from None
    if len(encoded_extra) > _MAX_EXTRA_BYTES:
        raise _validation_error("extra", f"编码后不能超过 {_MAX_EXTRA_BYTES} 字节")

    follower = Follower(
        name=name,
        plugin=plugin,
        url=url,
        platform=platform,
        quality=quality,
        tags=tags,
        extra=extra,
        enabled=enabled,
        fallback_plugins=fallback_plugins,
    )
    for index, existing in enumerate(existing_followers):
        if editing_index is not None and index == editing_index:
            continue
        if follower_key(existing) == follower_key(follower):
            raise _validation_error("url", "与已有关注项指向同一直播间")
    return follower


def preview_follower_edit(
    current: Follower | None,
    values: Mapping[str, Any] | Follower,
    *,
    existing_followers: Sequence[Follower] = (),
    editing_index: int | None = None,
) -> FollowerEditPreview:
    """Return a redacted diff for a confirmation screen without writing data."""
    follower = validate_follower_edit(
        values,
        current=current,
        existing_followers=existing_followers,
        editing_index=editing_index,
    )
    before = follower_to_edit_payload(current, redact=True) if current is not None else {}
    after = follower_to_edit_payload(follower, redact=True)
    changes = {
        field: (before.get(field), after.get(field))
        for field in FOLLOWER_EDIT_FIELD_ORDER
        if before.get(field) != after.get(field)
    }
    return FollowerEditPreview(follower=follower, before=before, after=after, changes=changes)


def _monitoring_validation_error(field: str, reason: str) -> MonitoringSettingsValidationError:
    return MonitoringSettingsValidationError({field: reason})


def _monitoring_source_values(source: AppConfig | Mapping[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return {}
    if isinstance(source, AppConfig):
        return {field: getattr(source, field) for field in MONITORING_SETTINGS_FIELD_ORDER}
    if isinstance(source, Mapping):
        return {field: source[field] for field in MONITORING_SETTINGS_FIELD_ORDER if field in source}
    raise _monitoring_validation_error("settings", "必须是设置对象")


def monitoring_settings_payload(source: AppConfig | Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return only the five safe-to-edit global monitoring settings.

    This intentionally excludes proxies and arbitrary settings rows.  There
    are no credential fields in this allow-list, and the result is copied so a
    form cannot mutate an ``AppConfig`` instance by accident.
    """
    values = _monitoring_source_values(source)
    return {
        field: values.get(field, TOP_CONFIG_DEFAULTS[field])
        for field in MONITORING_SETTINGS_FIELD_ORDER
    }


def _normalise_monitoring_integer(value: Any, field: str) -> int:
    minimum, maximum = MONITORING_SETTINGS_LIMITS[field]
    if isinstance(value, bool):
        raise _monitoring_validation_error(field, "必须是整数")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        text = value.strip()
        if not text or not re.fullmatch(r"[0-9]+", text):
            raise _monitoring_validation_error(field, "必须是整数")
        number = int(text)
    else:
        raise _monitoring_validation_error(field, "必须是整数")
    if not minimum <= number <= maximum:
        raise _monitoring_validation_error(field, f"必须介于 {minimum} 和 {maximum} 之间")
    return number


def _normalise_notifications_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y", "on", "是", "启用"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "否", "禁用"}:
            return False
    raise _monitoring_validation_error("notifications_enabled", "必须是 true 或 false")


def _coerce_monitoring_settings_patch(values: AppConfig | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(values, AppConfig):
        return _monitoring_source_values(values)
    if not isinstance(values, Mapping):
        raise _monitoring_validation_error("settings", "必须是设置对象")
    unknown = {str(key) for key in values} - MONITORING_SETTINGS_FIELDS
    if unknown:
        raise _monitoring_validation_error("settings", "包含不支持的设置字段")
    return dict(values)


def validate_monitoring_settings(
    values: AppConfig | Mapping[str, Any],
    *,
    current: AppConfig | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Strictly validate a full or partial global monitoring-settings edit.

    Numeric values accept ordinary integer text for UI inputs, but are never
    silently clamped.  This prevents a typo from turning into a request storm
    or an excessive number of isolated plugin processes.
    """
    normalized = monitoring_settings_payload(current)
    normalized.update(_coerce_monitoring_settings_patch(values))
    for field in MONITORING_SETTINGS_LIMITS:
        normalized[field] = _normalise_monitoring_integer(normalized[field], field)
    normalized["notifications_enabled"] = _normalise_notifications_enabled(
        normalized["notifications_enabled"]
    )
    return normalized


def preview_monitoring_settings(
    current: AppConfig | Mapping[str, Any] | None,
    values: AppConfig | Mapping[str, Any],
) -> MonitoringSettingsPreview:
    """Return a display-safe monitoring-settings diff without writing data."""
    before = validate_monitoring_settings({}, current=current)
    after = validate_monitoring_settings(values, current=before)
    # The allow-list contains no secret settings, but retain the same redaction
    # boundary as follower previews if a caller accidentally extends a mapping.
    safe_before = dict(redact_sensitive_mapping(before))
    safe_after = dict(redact_sensitive_mapping(after))
    changes = {
        field: (safe_before[field], safe_after[field])
        for field in MONITORING_SETTINGS_FIELD_ORDER
        if safe_before[field] != safe_after[field]
    }
    return MonitoringSettingsPreview(
        settings=after,
        before=safe_before,
        after=safe_after,
        changes=changes,
    )


# Short aliases keep the public API easy to discover from a settings screen.
validate_monitor_settings = validate_monitoring_settings
preview_monitor_settings = preview_monitoring_settings


def _normalize_platform(plugin: str, value) -> str:
    platform = _clean_text(value)
    if plugin in BUILTIN_STREAM_PLUGINS:
        return platform.casefold()
    return platform


def _settings_path_for(path: Path) -> Path:
    return path.with_name("settings.csv")


def _warn_unsafe_loaded_setting(field: str, message: str) -> None:
    """Explain a safe load-time fallback without echoing user-controlled data."""
    print(f"警告：监控设置 {field} {message}", file=sys.stderr)


def _safe_loaded_monitoring_integer(value: Any, field: str) -> int:
    """Bound a hand-edited settings value before it reaches the scheduler.

    The interactive editor rejects invalid input, but a CSV can also be edited
    by hand or restored from an older backup.  Loading must never turn that
    into a one-second request loop or an unbounded process fan-out.
    """
    default = TOP_CONFIG_DEFAULTS[field]
    minimum, maximum = MONITORING_SETTINGS_LIMITS[field]
    if value is None:
        return default
    if isinstance(value, bool):
        _warn_unsafe_loaded_setting(field, "格式无效，已使用安全默认值。")
        return default
    try:
        text = str(value).strip()
        if not text or not re.fullmatch(r"[0-9]+", text):
            raise ValueError
        number = int(text)
    except (TypeError, ValueError):
        _warn_unsafe_loaded_setting(field, "格式无效，已使用安全默认值。")
        return default
    bounded = min(max(number, minimum), maximum)
    if bounded != number:
        _warn_unsafe_loaded_setting(
            field,
            f"超出安全范围，运行时已限制为 {bounded}。",
        )
    return bounded


def _safe_loaded_monitoring_bool(value: Any, field: str = "notifications_enabled") -> bool:
    if value is None:
        return bool(TOP_CONFIG_DEFAULTS[field])
    try:
        return _normalise_notifications_enabled(value)
    except MonitoringSettingsValidationError:
        _warn_unsafe_loaded_setting(field, "格式无效，已使用安全默认值。")
        return bool(TOP_CONFIG_DEFAULTS[field])


def safe_monitoring_settings_for_runtime(
    source: AppConfig | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded monitoring subset for the scheduler's final guard.

    This is intentionally more defensive than the interactive validator: a
    hand-edited or programmatically mutated ``AppConfig`` is clamped to safe
    limits instead of raising after the monitor has already started.
    """
    values = monitoring_settings_payload(source)
    return {
        **{
            field: _safe_loaded_monitoring_integer(values.get(field), field)
            for field in MONITORING_SETTINGS_LIMITS
        },
        "notifications_enabled": _safe_loaded_monitoring_bool(
            values.get("notifications_enabled")
        ),
    }


def _base_config(data: dict | None = None, settings_path: Path | None = None) -> dict:
    data = data or {}
    if settings_path and settings_path.exists():
        with open(settings_path, "r", encoding=_detect_encoding(settings_path), newline="") as f:
            for row in csv.DictReader(f):
                key = _clean_text(row.get("key", ""))
                if key:
                    data[key] = row.get("value", "")

    platform_proxies = data.get("platform_proxies", {})
    if not isinstance(platform_proxies, dict):
        platform_proxies = {}
    platform_proxies = {
        str(platform).strip().casefold(): _clean_text(value)
        for platform, value in platform_proxies.items()
        if _clean_text(platform) and _clean_text(value)
    }
    for key, value in data.items():
        if not str(key).startswith("platform_proxy."):
            continue
        platform = str(key).split(".", 1)[1].strip().casefold()
        proxy_value = _clean_text(value)
        if platform and proxy_value:
            platform_proxies[platform] = proxy_value

    return {
        "poll_interval": _safe_loaded_monitoring_integer(data.get("poll_interval"), "poll_interval"),
        "max_concurrent_checks": _safe_loaded_monitoring_integer(
            data.get("max_concurrent_checks"), "max_concurrent_checks"
        ),
        "failure_backoff_after": _safe_loaded_monitoring_integer(
            data.get("failure_backoff_after"), "failure_backoff_after"
        ),
        "failure_backoff_polls": _safe_loaded_monitoring_integer(
            data.get("failure_backoff_polls"), "failure_backoff_polls"
        ),
        "notifications_enabled": _safe_loaded_monitoring_bool(data.get("notifications_enabled")),
        "platform_proxies": platform_proxies,
    }


def _normalize_follower(
    row: dict,
    *,
    strict_extra: bool = False,
    warn: bool = True,
) -> Follower | None:
    plugin = _clean_text(row.get("plugin", "")).casefold()
    extra = _normalize_extra(row.get("extra", {}), strict=strict_extra)
    sport_id = _clean_text(row.get("sport_id", ""))
    if sport_id:
        extra["sport_id"] = sport_id

    kwargs = {
        "name": _clean_text(row.get("name", "")),
        "plugin": plugin,
        "url": _clean_text(row.get("url", "")),
        "platform": _normalize_platform(plugin, row.get("platform", "")),
        "quality": _clean_text(row.get("quality", "best"), default="best"),
        "tags": _normalize_tags(row.get("tags", ["未分类"])),
        "extra": extra,
        "enabled": _clean_bool(row.get("enabled", True), default=True),
        "fallback_plugins": _normalize_plugin_list(row.get("fallback_plugins", [])),
    }
    if not kwargs["name"] or not kwargs["plugin"] or not kwargs["url"]:
        if warn:
            print("警告：关注项缺少必填字段 (name/plugin/url)，已跳过")
        return None
    return Follower(**kwargs)


def _is_tracking_query_parameter(raw_name: str) -> bool:
    """Return whether a URL query key is a known, non-room tracker."""
    name = unquote_plus(raw_name).strip().casefold()
    return name.startswith("utm_") or name in _TRACKING_QUERY_PARAMETERS


def _normalise_identity_query(query: str) -> str:
    """Remove known tracking pairs while retaining meaningful query context.

    The function deliberately preserves non-tracking pair spelling, ordering
    and duplicate keys.  Some platforms give those details meaning, while
    tracker placement/order should never create a second monitoring target.
    ``&`` and legacy ``;`` separators are made canonical for key comparison.
    """
    pairs: list[str] = []
    for raw_pair in _QUERY_FIELD_SPLIT_RE.split(query):
        if not raw_pair:
            continue
        raw_name, _, _ = raw_pair.partition("=")
        if _is_tracking_query_parameter(raw_name):
            continue
        pairs.append(raw_pair)
    return "&".join(pairs)


def _normalise_follower_url_identity(value: object) -> str:
    """Build a comparison-only URL identity without changing saved URLs.

    Scheme and host are case-insensitive, default HTTP(S) ports are implicit,
    a trailing slash/fragment does not identify a different room, and common
    click-tracking parameters are discarded.  The path and meaningful query
    parameters remain case/order-sensitive so platform-specific room context
    is not accidentally collapsed.

    Non-URL values intentionally retain the former simple normalization.  In
    particular FS1 stores an opaque room id in ``Follower.url`` and keeps its
    independent ``sport_id`` context in ``extra``; both remain part of the
    final :func:`follower_key` unchanged.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""

    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        # Legacy configurations can contain malformed URLs.  Keep their
        # previous deterministic behavior rather than making duplicate checks
        # fail while users migrate them through the validated editor.
        return raw.rstrip("/").casefold()

    if not parsed.scheme or not hostname:
        return raw.rstrip("/").casefold()

    scheme = parsed.scheme.casefold()
    try:
        host = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        host = hostname
    host = host.casefold().rstrip(".")
    if not host:
        return raw.rstrip("/").casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    # User info is deliberately not part of a room identity.  Validated edits
    # reject it, and omitting it here prevents a legacy credential from
    # bypassing duplicate detection.
    netloc = host
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        netloc = f"{netloc}:{port}"

    # Keep paths case-sensitive: unlike scheme/hostname, many platforms treat
    # differently cased path segments as distinct resources.  This retains
    # the former trailing-slash behavior without modifying persisted input.
    path = parsed.path.rstrip("/")
    query = _normalise_identity_query(parsed.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def follower_key(follower: Follower) -> tuple[str, str, str]:
    """Return the stable identity used to prevent duplicate room monitoring."""
    platform = (follower.platform or "").strip().casefold()
    url = _normalise_follower_url_identity(follower.url)
    extra = follower.extra if isinstance(follower.extra, dict) else {}
    # FS1 can use the same opaque room id for multiple sports.  Preserve that
    # context separately from URL canonicalization so it remains intentional
    # to monitor those as distinct entries.
    sport_id = str(extra.get("sport_id", "")).strip()
    return platform, url, sport_id


def _atomic_write(path: Path, encoding: str, write_content) -> None:
    """Write beside the target and replace it only after a successful flush."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            write_content(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


class ConfigLockTimeoutError(TimeoutError):
    """A short, display-safe failure when another Zhibo process is writing."""


def _lock_file_path(path: Path) -> Path:
    """Keep a durable sidecar lock beside the data file.

    The sidecar itself is harmless if a process crashes: the OS releases its
    advisory lock when the handle closes.  Keeping the file avoids a remove /
    recreate race where two processes could accidentally lock different files.
    """
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
def _config_write_lock(path: Path, *, timeout: float = CONFIG_LOCK_TIMEOUT_SECONDS):
    """Serialize a complete read/validate/write transaction across processes.

    Atomic replacement protects a file from torn writes but cannot protect two
    writers that both read the same old table.  This lock is deliberately held
    around the caller's complete transaction, not merely around ``os.replace``.
    """
    lock_path = _lock_file_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    acquired = False
    with open(lock_path, "r+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                _try_lock_handle(handle)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ConfigLockTimeoutError("配置正被另一进程修改，请稍后重试。") from None
                time.sleep(CONFIG_LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            if acquired:
                try:
                    _unlock_handle(handle)
                except OSError:
                    pass


def _file_revision(path: Path) -> bytes | None:
    """Fingerprint a file to detect uncooperative/manual writes in a transaction."""
    try:
        return hashlib.sha256(path.read_bytes()).digest()
    except FileNotFoundError:
        return None


def _ensure_unchanged_revision(path: Path, expected: bytes | None) -> None:
    if _file_revision(path) != expected:
        raise ValueError("配置文件在保存期间被外部修改，请重新预览后再确认。")


def _warn_duplicate_followers(followers: list[Follower], path: Path) -> None:
    seen: dict[tuple[str, str, str], Follower] = {}
    for follower in followers:
        key = follower_key(follower)
        previous = seen.get(key)
        if previous is not None:
            print(
                f"警告：{path.name} 中存在重复关注项：{follower.name} 与 {previous.name} "
                f"指向同一直播间 ({follower.platform}/{follower.url})"
            )
        else:
            seen[key] = follower


def extract_tags(cfg: AppConfig) -> list[str]:
    """从所有 followers 中提取去重标签列表。"""
    tags_set: set[str] = set()
    for follower in cfg.followers:
        for tag in follower.tags:
            tags_set.add(tag)
    tags = sorted(tags_set)
    return tags or ["全部"]


def _follower_to_csv_row(follower: Follower) -> dict:
    extra = follower.extra if isinstance(follower.extra, dict) else {}
    csv_extra = {key: value for key, value in extra.items() if key != "sport_id"}
    return {
        "enabled": str(follower.enabled).lower(),
        "name": follower.name,
        "tags": "|".join(follower.tags),
        "plugin": follower.plugin,
        "fallback_plugins": "|".join(follower.fallback_plugins),
        "platform": follower.platform,
        "url": follower.url,
        "quality": follower.quality,
        "sport_id": extra.get("sport_id", ""),
        "extra": json.dumps(csv_extra, ensure_ascii=False, sort_keys=True) if csv_extra else "",
    }


class ConfigManager:
    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CSV_FILE

    def _read_followers_csv(self, *, strict: bool) -> list[Follower]:
        """Read rows without changing legacy ``load_config`` error semantics."""
        if not self.config_path.exists():
            return []
        if self.config_path.suffix.casefold() != ".csv":
            raise ValueError("关注列表只支持 CSV 文件")

        followers: list[Follower] = []
        with open(self.config_path, "r", encoding=_detect_encoding(self.config_path), newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                if strict:
                    raise ValueError("关注列表缺少 CSV 表头")
                return []
            for line_number, row in enumerate(reader, start=2):
                try:
                    follower = _normalize_follower(row, strict_extra=strict, warn=not strict)
                except ValueError as exc:
                    if strict:
                        raise ValueError(f"关注列表第 {line_number} 行无效：{exc}") from None
                    continue
                if follower is None:
                    if strict:
                        raise ValueError(f"关注列表第 {line_number} 行缺少 name、plugin 或 url")
                    continue
                followers.append(follower)
        return followers

    def read_followers(self) -> list[Follower]:
        """Read normalized followers for a preview or confirm operation.

        Unlike :meth:`load_config`, this method is intentionally non-fatal:
        a missing/empty CSV produces ``[]`` and a malformed row produces a
        ``ValueError`` that the UI can present next to the import/edit form.
        It never writes or creates a file.
        """
        return self._read_followers_csv(strict=True)

    def _load_csv(self) -> AppConfig:
        cfg_dict = _base_config(settings_path=_settings_path_for(self.config_path))
        followers = self._read_followers_csv(strict=False)

        if not followers:
            sys.exit(f"错误：{self.config_path.name} 中没有有效的关注主播，请检查 name/plugin/url")
        _warn_duplicate_followers(followers, self.config_path)
        cfg_dict["followers"] = followers
        return AppConfig(**cfg_dict)

    def load_config(self) -> AppConfig:
        if not self.config_path.exists():
            if self.config_path == DEFAULT_CSV_FILE and is_frozen():
                # 打包版没有控制台，sys.exit 的提示用户看不见；首次运行
                # 直接生成可编辑的模板，让界面正常启动。
                template = (
                    "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id,extra" + "\n"
                    + "false,示例主播（请编辑或删除）,示例,streamlink,streamget,bilibili,"
                    "https://live.bilibili.com/6,best,," + "\n"
                )
                self.config_path.write_text(template, encoding="utf-8", newline="")
            if self.config_path == DEFAULT_CSV_FILE and DEFAULT_YAML_FILE.exists():
                sys.exit(
                    "错误：检测到旧版 followers.yaml。请先运行 "
                    "python main.py migrate-yaml followers.yaml"
                )
            sys.exit(f"错误：配置文件 {self.config_path} 不存在，请先创建 followers.csv")
        if self.config_path.suffix.casefold() != ".csv":
            sys.exit("错误：关注列表只支持 followers.csv")
        return self._load_csv()

    def _write_settings_csv_unlocked(self, cfg: AppConfig) -> None:
        """Write settings while the caller owns that settings-file lock."""
        path = _settings_path_for(self.config_path)
        rows = []
        cfg_dict = {
            "poll_interval": cfg.poll_interval,
            "max_concurrent_checks": cfg.max_concurrent_checks,
            "failure_backoff_after": cfg.failure_backoff_after,
            "failure_backoff_polls": cfg.failure_backoff_polls,
            "notifications_enabled": cfg.notifications_enabled,
        }
        for key, value in cfg_dict.items():
            rows.append({"key": key, "value": str(value).lower() if isinstance(value, bool) else value})
        for platform, value in sorted(cfg.platform_proxies.items()):
            if _clean_text(platform) and _clean_text(value):
                rows.append({"key": f"platform_proxy.{platform}", "value": value})
        def write_settings(f) -> None:
            writer = csv.DictWriter(f, fieldnames=["key", "value"])
            writer.writeheader()
            writer.writerows(rows)

        _atomic_write(path, "utf-8-sig", write_settings)

    def _write_settings_csv(self, cfg: AppConfig) -> None:
        path = _settings_path_for(self.config_path)
        with _config_write_lock(path):
            self._write_settings_csv_unlocked(cfg)

    def _write_followers_csv_unlocked(self, followers: Sequence[Follower]) -> None:
        """Write a fully validated follower table while its lock is held."""
        def write_followers(f) -> None:
            writer = csv.DictWriter(f, fieldnames=FOLLOWER_COLUMNS)
            writer.writeheader()
            for follower in followers:
                writer.writerow(_follower_to_csv_row(follower))

        _atomic_write(self.config_path, "utf-8-sig", write_followers)

    def write_followers_csv(self, followers: list[Follower]) -> None:
        """Safely validate and replace followers.csv under a write lock.

        This remains a public convenience API for migrations and tests, so it
        must not become a bypass around the editor/import validation boundary.
        Internal update/append transactions call the ``_unlocked`` helper only
        after they have already validated the full table while holding the
        same lock.
        """
        if not isinstance(followers, Sequence) or isinstance(followers, (str, bytes, bytearray)):
            raise TypeError("关注项必须是列表")
        validated: list[Follower] = []
        for follower in followers:
            validated.append(
                validate_follower_edit(
                    follower,
                    existing_followers=validated,
                )
            )
        with _config_write_lock(self.config_path):
            self._write_followers_csv_unlocked(validated)

    def save_config(self, cfg: AppConfig) -> None:
        """Persist settings without allowing callers to write unsafe limits."""
        if not isinstance(cfg, AppConfig):
            raise TypeError("配置必须是 AppConfig")
        safe_settings = safe_monitoring_settings_for_runtime(cfg)
        for field, value in safe_settings.items():
            setattr(cfg, field, value)
        self._write_settings_csv(cfg)

    def read_monitoring_settings(self) -> dict[str, Any]:
        """Read the safe editable subset of settings without loading followers."""
        current = _base_config(settings_path=_settings_path_for(self.config_path))
        return validate_monitoring_settings({}, current=current)

    def preview_monitoring_settings(
        self, values: AppConfig | Mapping[str, Any]
    ) -> MonitoringSettingsPreview:
        """Build a confirmation diff against the current settings.csv content."""
        return preview_monitoring_settings(self.read_monitoring_settings(), values)

    def update_monitoring_settings(
        self,
        values: AppConfig | Mapping[str, Any],
        *,
        expected_settings: AppConfig | Mapping[str, Any] | None = None,
    ) -> MonitoringSettingsPreview:
        """Validate, preview and atomically save global monitoring settings.

        Existing per-platform proxies are preserved.  ``expected_settings`` is
        optional optimistic-concurrency protection for a confirmation dialog:
        if settings.csv changed after the diff was shown, no write occurs.
        """
        settings_path = _settings_path_for(self.config_path)
        with _config_write_lock(settings_path):
            revision = _file_revision(settings_path)
            current_data = _base_config(settings_path=settings_path)
            current_settings = validate_monitoring_settings({}, current=current_data)
            if expected_settings is not None:
                expected = validate_monitoring_settings(expected_settings)
                if expected != current_settings:
                    raise ValueError("监控设置已在预览后变更，请重新确认修改")

            preview = preview_monitoring_settings(current_settings, values)
            cfg = AppConfig(
                **preview.settings,
                platform_proxies=current_data["platform_proxies"],
            )
            _ensure_unchanged_revision(settings_path, revision)
            self._write_settings_csv_unlocked(cfg)
            return preview

    # Keep both names for screens that use the shorter "monitor" terminology.
    read_monitor_settings = read_monitoring_settings
    update_monitor_settings = update_monitoring_settings

    def update_follower(
        self,
        index: int,
        values: Mapping[str, Any] | Follower,
        *,
        expected_key: tuple[str, str, str] | None = None,
        expected_follower: Follower | None = None,
    ) -> FollowerEditPreview:
        """Validate, preview and atomically replace one follower row.

        ``values`` may be a complete UI form or a partial patch.  The original
        CSV is untouched if validation or the final atomic replacement fails.
        Call :func:`preview_follower_edit` first when the UI needs an explicit
        user confirmation before this method is invoked.
        """
        with _config_write_lock(self.config_path):
            revision = _file_revision(self.config_path)
            followers = self.read_followers()
            if not isinstance(index, int) or isinstance(index, bool):
                raise TypeError("关注项索引必须是整数")
            if index < 0 or index >= len(followers):
                raise IndexError("关注项索引超出范围")
            if expected_key is not None and follower_key(followers[index]) != expected_key:
                raise ValueError("配置已在预览后变化；请重新打开编辑器并确认目标直播间")
            if expected_follower is not None and followers[index] != expected_follower:
                raise ValueError("配置内容已在预览后变化；请重新打开编辑器并确认修改")
            preview = preview_follower_edit(
                followers[index],
                values,
                existing_followers=followers,
                editing_index=index,
            )
            updated_followers = list(followers)
            updated_followers[index] = preview.follower
            _ensure_unchanged_revision(self.config_path, revision)
            self._write_followers_csv_unlocked(updated_followers)
            return preview

    def append_validated_follower(self, values: Mapping[str, Any] | Follower) -> FollowerEditPreview:
        """Validate and append a follower in one inter-process transaction."""
        with _config_write_lock(self.config_path):
            revision = _file_revision(self.config_path)
            followers = self.read_followers()
            # Validate security and field shape before comparing the candidate
            # with the table.  The exact-duplicate message remains compatible
            # with the legacy append API while no unsafe URL/extra is written.
            preview = preview_follower_edit(None, values)
            for existing in followers:
                if follower_key(existing) == follower_key(preview.follower):
                    raise ValueError(f"该直播间已关注：{existing.name} ({existing.platform})")
            _ensure_unchanged_revision(self.config_path, revision)
            self._write_followers_csv_unlocked([*followers, preview.follower])
            return preview

    def remove_follower(
        self,
        index: int,
        *,
        expected_key: tuple[str, str, str] | None = None,
        expected_follower: Follower | None = None,
    ) -> Follower:
        """Atomically remove one follower after verifying the confirmed target."""
        with _config_write_lock(self.config_path):
            revision = _file_revision(self.config_path)
            followers = self.read_followers()
            if not isinstance(index, int) or isinstance(index, bool):
                raise TypeError("关注项索引必须是整数")
            if index < 0 or index >= len(followers):
                raise IndexError("关注项索引超出范围")
            if len(followers) <= 1:
                raise ValueError("至少需要保留一个直播间，无法删除最后一项")
            target = followers[index]
            if expected_key is not None and follower_key(target) != expected_key:
                raise ValueError("配置已在确认后变化；请重新选择要删除的直播间")
            if expected_follower is not None and target != expected_follower:
                raise ValueError("直播间内容已在确认后变化；请重新确认删除")
            _ensure_unchanged_revision(self.config_path, revision)
            self._write_followers_csv_unlocked([*followers[:index], *followers[index + 1 :]])
            return target

    def append_follower(self, follower: Follower) -> None:
        """Compatibility wrapper that now uses the safe append transaction."""
        self.append_validated_follower(follower)


def migrate_yaml_to_csv(
    source: str | Path,
    destination: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Convert a legacy YAML follower file to CSV without changing the source."""
    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"找不到旧配置文件：{source_path}")
    if source_path.suffix.casefold() not in {".yaml", ".yml"}:
        raise ValueError("迁移来源必须是 .yaml 或 .yml 文件")

    target_path = Path(destination) if destination else source_path.with_name("followers.csv")
    if target_path.exists() and not overwrite:
        raise FileExistsError(f"目标文件已存在：{target_path}（请先备份或使用 --force）")

    with open(source_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("旧配置根节点必须是对象")

    raw_followers = data.get("followers", [])
    if not isinstance(raw_followers, list):
        raise ValueError("旧配置中的 followers 必须是列表")

    followers: list[Follower] = []
    for row in raw_followers:
        if not isinstance(row, dict):
            print(f"警告：关注项格式无效，已跳过: {row}")
            continue
        follower = _normalize_follower(row)
        if follower:
            followers.append(follower)
    if not followers:
        raise ValueError("旧配置中没有有效的关注主播，请检查 name/plugin/url")

    cfg_dict = _base_config(data)
    cfg = AppConfig(**cfg_dict, followers=followers)
    manager = ConfigManager(target_path)
    manager.write_followers_csv(cfg.followers)
    manager.save_config(cfg)
    return target_path
