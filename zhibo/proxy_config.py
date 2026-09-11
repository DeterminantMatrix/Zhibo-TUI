"""Per-platform proxy selection."""

from __future__ import annotations

import os


# Twitch and the other international platforms are more reliable through the
# local proxy used by this desktop setup. Set ZHIBO_PLATFORM_PROXY=direct to
# force a direct connection instead.
DEFAULT_PROXY_URL = "http://127.0.0.1:7890"

PROXY_PLATFORMS = {
    "chzzk",
    "kick",
    "tiktok",
    "twitcasting",
    "twitch",
    "youtube",
}

_platform_proxies: dict[str, str] = {}


def normalize_platform(platform: str | None) -> str:
    return str(platform or "").strip().casefold()


def configured_proxy_url() -> str | None:
    value = os.environ.get("ZHIBO_PLATFORM_PROXY")
    if value is None:
        return DEFAULT_PROXY_URL
    value = value.strip()
    if not value or value.casefold() in {"direct", "none", "off"}:
        return None
    return value


def normalize_proxy_value(value: str | None) -> str | None:
    """Convert a UI value into a proxy URL; ``direct`` disables a platform."""
    text = str(value or "").strip()
    if not text or text.casefold() in {"direct", "none", "off"}:
        return None
    if text.isdigit():
        return f"http://127.0.0.1:{text}"
    if "://" not in text:
        return f"http://{text}"
    return text


def set_platform_proxies(proxies: dict[str, str] | None) -> None:
    """Set runtime per-platform overrides loaded from the app configuration."""
    global _platform_proxies
    _platform_proxies = {
        normalize_platform(platform): str(value).strip()
        for platform, value in (proxies or {}).items()
        if normalize_platform(platform) in PROXY_PLATFORMS and str(value).strip()
    }


def configured_platform_proxies() -> dict[str, str]:
    """Return a copy suitable for editing in the UI."""
    return dict(_platform_proxies)


def proxy_for_platform(platform: str | None) -> str | None:
    platform_key = normalize_platform(platform)
    if platform_key not in PROXY_PLATFORMS:
        return None
    env_key = f"ZHIBO_{platform_key.upper()}_PROXY"
    if env_key in os.environ:
        return normalize_proxy_value(os.environ[env_key])
    if platform_key in _platform_proxies:
        return normalize_proxy_value(_platform_proxies[platform_key])
    return configured_proxy_url()
