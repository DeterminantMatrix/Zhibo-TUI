"""Safe, reusable preview-and-confirm workflow for follower imports.

The legacy :func:`importer.build_follower_from_url` API intentionally remains a
small building block.  This module adds the state needed by an interactive UI
or a CLI to show validation and collision information *before* it mutates the
CSV configuration.
"""
from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from zhibo.config import follower_key
from zhibo.importer import build_follower_from_url
from zhibo.models import Follower

if TYPE_CHECKING:
    from zhibo.config import ConfigManager


@dataclass(frozen=True, slots=True)
class ImportValidationIssue:
    """A user-actionable validation problem found while creating a preview."""

    code: str
    message: str
    field: str = "url"


@dataclass(frozen=True, slots=True)
class ImportConflict:
    """A collision between an import candidate and an existing follower."""

    kind: str
    message: str
    existing: Follower


@dataclass(frozen=True, slots=True)
class ImportPreview:
    """The complete, non-mutating result of an import attempt.

    ``duplicate`` is a hard stop: importing it would create two monitoring
    entries for the exact same room.  ``conflicts`` require an explicit
    ``allow_conflicts=True`` on confirmation, so a caller cannot accidentally
    add an ambiguous item merely by showing a confirmation dialog.
    """

    raw_url: str
    tag: str
    follower: Follower | None = None
    validation_errors: tuple[ImportValidationIssue, ...] = ()
    duplicate: ImportConflict | None = None
    conflicts: tuple[ImportConflict, ...] = ()

    @property
    def is_valid(self) -> bool:
        return self.follower is not None and not self.validation_errors

    @property
    def requires_conflict_override(self) -> bool:
        return bool(self.conflicts)

    @property
    def can_confirm(self) -> bool:
        """Whether the candidate is valid and not an exact duplicate."""
        return self.is_valid and self.duplicate is None

    @property
    def messages(self) -> tuple[str, ...]:
        """Return presentation-ready validation and collision messages."""
        messages = [issue.message for issue in self.validation_errors]
        if self.duplicate is not None:
            messages.append(self.duplicate.message)
        messages.extend(conflict.message for conflict in self.conflicts)
        return tuple(messages)


class ImportPreviewError(ValueError):
    """Base class for errors raised by explicit import confirmation."""


class ImportConfirmationRequired(ImportPreviewError):
    """Raised when a caller attempts to write without an explicit approval."""


class ImportValidationFailed(ImportPreviewError):
    """Raised when a preview with validation errors is submitted for writing."""


class ImportDuplicateError(ImportPreviewError):
    """Raised when the candidate already exists in the current configuration."""


class ImportConflictError(ImportPreviewError):
    """Raised when a non-duplicate conflict has not been explicitly approved."""


def _copy_follower(follower: Follower) -> Follower:
    """Return a write-safe copy so preview/UI mutation cannot leak into CSV."""
    return Follower(
        name=follower.name,
        plugin=follower.plugin,
        url=follower.url,
        platform=follower.platform,
        quality=follower.quality,
        tags=list(follower.tags),
        extra=copy.deepcopy(follower.extra),
        enabled=follower.enabled,
        fallback_plugins=list(follower.fallback_plugins),
    )


def _normalized_name(follower: Follower) -> str:
    return (follower.name or "").strip().casefold()


def _describe_existing(follower: Follower) -> str:
    platform = (follower.platform or "").strip() or "未知平台"
    return f"{follower.name or '未命名关注项'}（{platform}）"


def _compare_candidate(
    candidate: Follower,
    existing_followers: Iterable[Follower],
) -> tuple[ImportConflict | None, tuple[ImportConflict, ...]]:
    """Classify exact duplicates and ambiguous, but distinct, entries."""
    candidate_key = follower_key(candidate)
    candidate_room_key = candidate_key[:2]
    candidate_name = _normalized_name(candidate)
    duplicate: ImportConflict | None = None
    conflicts: list[ImportConflict] = []

    for existing in existing_followers:
        if not isinstance(existing, Follower):
            continue
        existing_key = follower_key(existing)
        description = _describe_existing(existing)
        if existing_key == candidate_key:
            duplicate = ImportConflict(
                kind="duplicate",
                message=f"该直播间已关注：{description}",
                existing=existing,
            )
            # An exact duplicate is definitive.  Do not overwhelm the caller
            # with weaker name/context conflicts for the same item.
            break

        if existing_key[:2] == candidate_room_key:
            conflicts.append(
                ImportConflict(
                    kind="same_room_different_context",
                    message=(
                        "同一直播间已存在不同上下文配置："
                        f"{description}；请确认是否确实需要重复监控。"
                    ),
                    existing=existing,
                )
            )
        elif candidate_name and _normalized_name(existing) == candidate_name:
            conflicts.append(
                ImportConflict(
                    kind="same_name_different_room",
                    message=(
                        "主播名称与现有关注项相同但直播间不同："
                        f"{description}；请确认是否为同名主播。"
                    ),
                    existing=existing,
                )
            )

    return duplicate, tuple(conflicts)


def _conflict_fingerprint(conflicts: Iterable[ImportConflict]) -> tuple[tuple[str, str, str, str, str], ...]:
    """Return a stable, non-presentational identity for confirmed conflicts.

    The confirmation button is an approval for the conflicts the user saw,
    not a blanket permission to import despite any conflict that appears later.
    Include the existing room identity and name so adding/replacing a row while
    the preview is open forces a new preview rather than silently broadening
    that approval.
    """
    values = [
        (
            conflict.kind,
            *follower_key(conflict.existing),
            _normalized_name(conflict.existing),
        )
        for conflict in conflicts
    ]
    return tuple(sorted(values))


def _validation_issue_from_exception(exc: Exception) -> ImportValidationIssue:
    """Convert legacy ``ValueError`` text into stable codes for the UI."""
    message = str(exc).strip() or "无法解析直播间网址。"
    normalized = message.casefold()
    if "http" in normalized and "support" in normalized:
        code = "unsupported_scheme"
    elif "hostname" in normalized or "host" in normalized or "port" in normalized:
        code = "invalid_host"
    elif "暂不支持" in message:
        code = "unsupported_platform"
    elif "room_id" in normalized:
        code = "missing_room_id"
    elif "请输入" in message or "empty" in normalized:
        code = "empty_url"
    else:
        code = "invalid_url"
    return ImportValidationIssue(code=code, message=message)


async def build_import_preview(
    url: str,
    tag: str = "未分类",
    *,
    existing_followers: Iterable[Follower] = (),
) -> ImportPreview:
    """Build a preview without writing configuration or changing runtime state.

    This preserves the importer's existing blank-tag behavior while turning
    malformed input into structured errors rather than an exception a UI has
    to parse.  Plugin metadata lookups remain best-effort, exactly as they are
    in :func:`build_follower_from_url`.
    """
    raw_url = url if isinstance(url, str) else ""
    normalized_tag = tag.strip() if isinstance(tag, str) else ""
    if not isinstance(url, str):
        return ImportPreview(
            raw_url=raw_url,
            tag=normalized_tag,
            validation_errors=(
                ImportValidationIssue(
                    code="invalid_url",
                    message="直播间网址必须是文本。",
                ),
            ),
        )
    if not isinstance(tag, str):
        return ImportPreview(
            raw_url=raw_url,
            tag="",
            validation_errors=(
                ImportValidationIssue(
                    code="invalid_tag",
                    message="标签必须是文本。",
                    field="tag",
                ),
            ),
        )

    try:
        follower = await build_follower_from_url(url, tag)
    except asyncio.CancelledError:
        raise
    except (TypeError, ValueError) as exc:
        return ImportPreview(
            raw_url=raw_url,
            tag=normalized_tag,
            validation_errors=(_validation_issue_from_exception(exc),),
        )
    except Exception:
        # Do not surface arbitrary plugin/traceback data in a preview message.
        # Normal plugin lookup failures are already best-effort in the legacy
        # builder; this only covers an unexpected integration failure.
        return ImportPreview(
            raw_url=raw_url,
            tag=normalized_tag,
            validation_errors=(
                ImportValidationIssue(
                    code="preview_failed",
                    message="无法构建导入预览，请检查插件和网络后重试。",
                ),
            ),
        )

    duplicate, conflicts = _compare_candidate(follower, existing_followers)
    return ImportPreview(
        raw_url=raw_url,
        tag=normalized_tag or "未分类",
        follower=_copy_follower(follower),
        duplicate=duplicate,
        conflicts=conflicts,
    )


# A concise alias reads naturally at integration call sites.
preview_import = build_import_preview


def _current_followers(config_manager: "ConfigManager") -> list[Follower]:
    reader = getattr(config_manager, "read_followers", None)
    if not callable(reader):
        raise TypeError("ConfigManager 必须提供只读 read_followers() 以确认导入。")
    return list(reader())


def confirm_import_preview(
    preview: ImportPreview,
    config_manager: "ConfigManager",
    *,
    confirmed: bool = False,
    allow_conflicts: bool = False,
) -> Follower:
    """Persist a previously previewed follower after an explicit approval.

    The current CSV is read again immediately before writing.  A conflict
    override is bound to the exact conflict set shown in the preview, so a
    concurrent configuration change cannot turn it into a blanket override.
    """
    if confirmed is not True:
        raise ImportConfirmationRequired("导入预览尚未得到明确确认。")
    if not preview.is_valid or preview.follower is None:
        raise ImportValidationFailed("导入预览存在校验错误，不能写入配置。")
    if preview.duplicate is not None:
        raise ImportDuplicateError(preview.duplicate.message)
    if preview.conflicts and allow_conflicts is not True:
        raise ImportConflictError("导入预览存在冲突；请明确确认后再写入。")

    candidate = _copy_follower(preview.follower)
    current_followers = _current_followers(config_manager)
    duplicate, conflicts = _compare_candidate(candidate, current_followers)
    if duplicate is not None:
        raise ImportDuplicateError(duplicate.message)
    preview_conflict_fingerprint = _conflict_fingerprint(preview.conflicts)
    current_conflict_fingerprint = _conflict_fingerprint(conflicts)
    if conflicts and allow_conflicts is not True:
        raise ImportConflictError("配置已变化并产生冲突；请重新确认后再写入。")
    if allow_conflicts is True and current_conflict_fingerprint != preview_conflict_fingerprint:
        raise ImportConflictError("导入冲突已在预览后变化；请重新生成预览并确认。")

    # Never drop back to the legacy raw append path here.  Import URLs and
    # plugin-derived extras must go through the same credential/URL validation
    # as the interactive editor before they reach the CSV file.
    append_validated = getattr(config_manager, "append_validated_follower", None)
    if not callable(append_validated):
        raise TypeError("ConfigManager 必须提供安全的 append_validated_follower()")
    persisted = append_validated(candidate)
    return persisted.follower


class ImportPreviewService:
    """Small adapter for consumers that already own a ``ConfigManager``."""

    def __init__(self, config_manager: "ConfigManager"):
        self.config_manager = config_manager

    async def preview(self, url: str, tag: str = "未分类") -> ImportPreview:
        return await build_import_preview(
            url,
            tag,
            existing_followers=_current_followers(self.config_manager),
        )

    def confirm(
        self,
        preview: ImportPreview,
        *,
        confirmed: bool = False,
        allow_conflicts: bool = False,
    ) -> Follower:
        return confirm_import_preview(
            preview,
            self.config_manager,
            confirmed=confirmed,
            allow_conflicts=allow_conflicts,
        )
