"""Resolve and explicitly migrate private, user-local application files.

This module deliberately never probes the legacy project-directory credential
files during normal startup.  Legacy discovery and copying are opt-in through
the functions at the bottom of this module.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


APP_DIR_NAME = "Zhibo"
DATA_DIR_ENV_NAMES = ("ZHIBO_DATA_DIR", "ZHIBO_PRIVATE_DATA_DIR")
COOKIE_FILE_ENV = "ZHIBO_COOKIE_FILE"
FS_CONFIG_ENV = "ZHIBO_FS_CONFIG"
LOG_FILE_ENV = "ZHIBO_LOG_FILE"

__all__ = [
    "APP_DIR_NAME",
    "COOKIE_FILE_ENV",
    "FS_CONFIG_ENV",
    "LOG_FILE_ENV",
    "CredentialMigrationResult",
    "LegacyCredentialFile",
    "configure_legacy_cookie_path",
    "cookie_file_path",
    "ensure_private_parent",
    "fs_config_path",
    "legacy_credential_files",
    "legacy_credentials_notice",
    "log_file_path",
    "migrate_legacy_credentials",
    "user_data_dir",
]


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _configured_path(value: str) -> Path:
    return Path(os.path.expandvars(value.strip())).expanduser()


def user_data_dir(
    environ: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user private data directory without creating it.

    ``ZHIBO_DATA_DIR`` (or its ``ZHIBO_PRIVATE_DATA_DIR`` alias) has priority.
    On Windows, ``LOCALAPPDATA`` is preferred over ``APPDATA`` so the default is
    outside common cloud-synced document folders.
    """
    env = _environment(environ)
    for key in DATA_DIR_ENV_NAMES:
        value = (env.get(key) or "").strip()
        if value:
            return _configured_path(value)

    if platform_name is None:
        platform_name = "darwin" if sys.platform == "darwin" else os.name
    platform_name = platform_name.casefold()
    home = Path.home() if home is None else Path(home)
    if platform_name in {"nt", "windows"}:
        base = (env.get("LOCALAPPDATA") or env.get("APPDATA") or "").strip()
        return _configured_path(base) / APP_DIR_NAME if base else home / "AppData" / "Local" / APP_DIR_NAME
    if platform_name in {"darwin", "mac", "macos"}:
        return home / "Library" / "Application Support" / APP_DIR_NAME

    base = (env.get("XDG_DATA_HOME") or "").strip()
    return _configured_path(base) / APP_DIR_NAME if base else home / ".local" / "share" / APP_DIR_NAME


def _override_or_default(
    env_name: str,
    default_relative_path: Path,
    environ: Mapping[str, str] | None = None,
) -> Path:
    value = (_environment(environ).get(env_name) or "").strip()
    if value:
        return _configured_path(value)
    return user_data_dir(environ) / default_relative_path


def cookie_file_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return the configured cookie file path without opening it."""
    return _override_or_default(COOKIE_FILE_ENV, Path("cookies.txt"), environ)


def fs_config_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return the configured FS1 config path without opening it."""
    return _override_or_default(FS_CONFIG_ENV, Path("fs1") / "rooms.yaml", environ)


def log_file_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return the configured log file path without opening it."""
    return _override_or_default(LOG_FILE_ENV, Path("logs") / "zhibo.log", environ)


def ensure_private_parent(path: Path) -> Path:
    """Create a private-data parent directory when a caller is about to write."""
    parent = Path(path).expanduser().parent
    created = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if created:
        try:
            os.chmod(parent, 0o700)
        except OSError:
            # Windows ACLs, network folders, and user-selected overrides may
            # not support POSIX permission bits. The caller can still use its
            # explicit path while avoiding a hard startup failure.
            pass
    return parent


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def configure_legacy_cookie_path() -> Path:
    """Keep legacy cookie consumers on the safe default without reading files.

    ``yt_dlp_plugin`` historically performed its own environment lookup.
    Legacy callers may use this compatibility shim to obtain the private
    default. An explicit user value always wins.
    """
    configured = (os.environ.get(COOKIE_FILE_ENV) or "").strip()
    if configured:
        return _configured_path(configured)

    path = cookie_file_path()
    os.environ[COOKIE_FILE_ENV] = str(path)
    return path


@dataclass(frozen=True)
class LegacyCredentialFile:
    """Metadata returned only by an explicit status or migration request."""

    name: str
    source: Path
    destination: Path
    exists: bool


@dataclass(frozen=True)
class CredentialMigrationResult:
    name: str
    status: str


def _legacy_project_root(project_root: Path | None) -> Path:
    return (Path(project_root) if project_root is not None else Path(__file__).resolve().parent.parent).expanduser()


def _legacy_source_safety(project_root: Path, source: Path) -> str | None:
    """Reject symlinked source components and paths escaping the project root."""
    try:
        relative = source.relative_to(project_root)
    except ValueError:
        return "skipped_outside_project"

    current = project_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return "skipped_symlink"

    try:
        resolved_root = project_root.resolve(strict=True)
        resolved_source = source.resolve(strict=True)
        resolved_source.relative_to(resolved_root)
    except (OSError, ValueError):
        return "skipped_outside_project"
    return None


def _copy_credential_file(source: Path, destination: Path) -> str:
    """Publish a fully copied credential file without a permission or partial-file window."""
    temp_path: Path | None = None
    try:
        ensure_private_parent(destination)
        with source.open("rb") as source_file:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as destination_file:
                temp_path = Path(destination_file.name)
                _restrict_file(temp_path)
                shutil.copyfileobj(source_file, destination_file)
                destination_file.flush()
                os.fsync(destination_file.fileno())

        # A hard link creates the final name only when it does not already
        # exist. Unlike os.replace(), it cannot overwrite a raced-in file.
        os.link(temp_path, destination)
        _restrict_file(destination)
        return "copied"
    except FileExistsError:
        return "destination_exists"
    except OSError:
        return "copy_failed"
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def legacy_credential_files(
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[LegacyCredentialFile, ...]:
    """Explicitly inspect legacy credential file *existence*, never contents."""
    root = _legacy_project_root(project_root)
    candidates = (
        ("Cookie", root / "cookies.txt", cookie_file_path(environ)),
        ("FS1 configuration", root / "sports" / "rooms.yaml", fs_config_path(environ)),
    )
    return tuple(
        LegacyCredentialFile(name, source, destination, source.is_file())
        for name, source, destination in candidates
    )


def legacy_credentials_notice(
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return a content-free notice for an explicit UI or CLI migration prompt."""
    found = [item.name for item in legacy_credential_files(project_root, environ) if item.exists]
    if not found:
        return ""
    labels = "、".join(found)
    return (
        f"发现旧项目凭据：{labels}。程序不会自动读取或迁移它们；"
        "如需迁移，请使用 `python main.py migrate-credentials`。"
    )


def migrate_legacy_credentials(
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[CredentialMigrationResult, ...]:
    """Explicitly copy legacy credentials to private storage without overwriting.

    The original files are never moved, deleted, printed, or otherwise changed.
    Callers should expose this only behind an explicit user action or command.
    """
    results: list[CredentialMigrationResult] = []
    root = _legacy_project_root(project_root)
    for item in legacy_credential_files(project_root, environ):
        if not item.exists:
            results.append(CredentialMigrationResult(item.name, "not_found"))
            continue
        source_status = _legacy_source_safety(root, item.source)
        if source_status:
            results.append(CredentialMigrationResult(item.name, source_status))
            continue
        if item.destination.exists() or item.destination.is_symlink():
            results.append(CredentialMigrationResult(item.name, "destination_exists"))
            continue

        status = _copy_credential_file(item.source, item.destination)
        results.append(CredentialMigrationResult(item.name, status))
    return tuple(results)
