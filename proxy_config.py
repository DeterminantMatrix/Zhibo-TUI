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


def proxy_for_platform(platform: str | None) -> str | None:
    if normalize_platform(platform) not in PROXY_PLATFORMS:
        return None
    return configured_proxy_url()
