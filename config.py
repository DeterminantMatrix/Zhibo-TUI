"""配置加载、保存与 CSV/YAML 兼容。"""
import csv
import os
import sys
import tempfile
from pathlib import Path

import yaml
from models import AppConfig, Follower

BASE_DIR = Path(__file__).parent
DEFAULT_CSV_FILE = BASE_DIR / "followers.csv"
DEFAULT_YAML_FILE = BASE_DIR / "followers.yaml"
DEFAULT_SETTINGS_FILE = BASE_DIR / "settings.csv"

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
]
TOP_CONFIG_DEFAULTS = {
    "poll_interval": 60,
    "max_concurrent_checks": 8,
    "failure_backoff_after": 3,
    "failure_backoff_polls": 2,
    "notifications_enabled": True,
}


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


def _normalize_platform(plugin: str, value) -> str:
    platform = _clean_text(value)
    if plugin in BUILTIN_STREAM_PLUGINS:
        return platform.casefold()
    return platform


def _settings_path_for(path: Path) -> Path:
    if path.suffix.casefold() == ".csv":
        return path.with_name("settings.csv")
    return DEFAULT_SETTINGS_FILE


def _base_config(data: dict | None = None, settings_path: Path | None = None) -> dict:
    data = data or {}
    if settings_path and settings_path.exists():
        with open(settings_path, "r", encoding=_detect_encoding(settings_path), newline="") as f:
            for row in csv.DictReader(f):
                key = _clean_text(row.get("key", ""))
                if key:
                    data[key] = row.get("value", "")

    return {
        "poll_interval": _clean_int(data.get("poll_interval"), default=TOP_CONFIG_DEFAULTS["poll_interval"]),
        "max_concurrent_checks": _clean_int(
            data.get("max_concurrent_checks"),
            default=TOP_CONFIG_DEFAULTS["max_concurrent_checks"],
        ),
        "failure_backoff_after": _clean_int(
            data.get("failure_backoff_after"),
            default=TOP_CONFIG_DEFAULTS["failure_backoff_after"],
        ),
        "failure_backoff_polls": _clean_int(
            data.get("failure_backoff_polls"),
            default=TOP_CONFIG_DEFAULTS["failure_backoff_polls"],
        ),
        "notifications_enabled": _clean_bool(
            data.get("notifications_enabled"),
            default=TOP_CONFIG_DEFAULTS["notifications_enabled"],
        ),
    }


def _normalize_follower(row: dict) -> Follower | None:
    plugin = _clean_text(row.get("plugin", "")).casefold()
    extra = row.get("extra", {}) if isinstance(row.get("extra", {}), dict) else {}
    sport_id = _clean_text(row.get("sport_id", ""))
    if sport_id:
        extra = {**extra, "sport_id": sport_id}

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
        print(f"警告：关注项缺少必填字段 (name/plugin/url)，已跳过: {row}")
        return None
    return Follower(**kwargs)


def follower_key(follower: Follower) -> tuple[str, str, str]:
    """Return the stable identity used to prevent duplicate room monitoring."""
    platform = (follower.platform or "").strip().casefold()
    url = (follower.url or "").strip().rstrip("/").casefold()
    extra = follower.extra if isinstance(follower.extra, dict) else {}
    sport_id = str(extra.get("sport_id", "")).strip()
    return platform, url, sport_id


def _atomic_write(path: Path, encoding: str, write_content) -> None:
    """Write beside the target and replace it only after a successful flush."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            write_content(f)
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


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
    }


class ConfigManager:
    def __init__(self, config_path: str | Path | None = None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = DEFAULT_CSV_FILE if DEFAULT_CSV_FILE.exists() else DEFAULT_YAML_FILE

    def _load_yaml(self) -> AppConfig:
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        cfg_dict = _base_config(data)
        raw_followers = data.get("followers", [])
        if not raw_followers:
            sys.exit(f"错误：{self.config_path.name} 中没有配置任何关注主播，请先添加")

        followers = []
        for row in raw_followers:
            if not isinstance(row, dict):
                print(f"警告：关注项格式无效，已跳过: {row}")
                continue
            follower = _normalize_follower(row)
            if follower:
                followers.append(follower)

        if not followers:
            sys.exit(f"错误：{self.config_path.name} 中没有有效的关注主播，请检查 name/plugin/url")
        _warn_duplicate_followers(followers, self.config_path)
        cfg_dict["followers"] = followers
        return AppConfig(**cfg_dict)

    def _load_csv(self) -> AppConfig:
        cfg_dict = _base_config(settings_path=_settings_path_for(self.config_path))
        followers = []
        with open(self.config_path, "r", encoding=_detect_encoding(self.config_path), newline="") as f:
            for row in csv.DictReader(f):
                follower = _normalize_follower(row)
                if follower:
                    followers.append(follower)

        if not followers:
            sys.exit(f"错误：{self.config_path.name} 中没有有效的关注主播，请检查 name/plugin/url")
        _warn_duplicate_followers(followers, self.config_path)
        cfg_dict["followers"] = followers
        return AppConfig(**cfg_dict)

    def load_config(self) -> AppConfig:
        if not self.config_path.exists():
            sys.exit(f"错误：配置文件 {self.config_path} 不存在，请先创建 followers.csv")
        if self.config_path.suffix.casefold() == ".csv":
            return self._load_csv()
        return self._load_yaml()

    def _write_settings_csv(self, cfg: AppConfig) -> None:
        path = _settings_path_for(self.config_path)
        rows = []
        cfg_dict = {
            "poll_interval": cfg.poll_interval,
            "max_concurrent_checks": cfg.max_concurrent_checks,
            "failure_backoff_after": cfg.failure_backoff_after,
            "failure_backoff_polls": cfg.failure_backoff_polls,
            "notifications_enabled": cfg.notifications_enabled,
        }
        for key in TOP_CONFIG_DEFAULTS:
            if key in cfg_dict:
                rows.append({"key": key, "value": str(cfg_dict[key]).lower() if isinstance(cfg_dict[key], bool) else cfg_dict[key]})
        def write_settings(f) -> None:
            writer = csv.DictWriter(f, fieldnames=["key", "value"])
            writer.writeheader()
            writer.writerows(rows)

        _atomic_write(path, "utf-8-sig", write_settings)

    def write_followers_csv(self, followers: list[Follower]) -> None:
        def write_followers(f) -> None:
            writer = csv.DictWriter(f, fieldnames=FOLLOWER_COLUMNS)
            writer.writeheader()
            for follower in followers:
                writer.writerow(_follower_to_csv_row(follower))

        _atomic_write(self.config_path, "utf-8-sig", write_followers)

    def save_config(self, cfg: AppConfig) -> None:
        top_keys = set(TOP_CONFIG_DEFAULTS)
        if self.config_path.suffix.casefold() == ".csv":
            self._write_settings_csv(cfg)
            return

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        cfg_dict = {
            "poll_interval": cfg.poll_interval,
            "max_concurrent_checks": cfg.max_concurrent_checks,
            "failure_backoff_after": cfg.failure_backoff_after,
            "failure_backoff_polls": cfg.failure_backoff_polls,
            "notifications_enabled": cfg.notifications_enabled,
        }
        for key in top_keys:
            if key in cfg_dict:
                data[key] = cfg_dict[key]
        def write_yaml(f) -> None:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        _atomic_write(self.config_path, "utf-8", write_yaml)

    def append_follower(self, follower: Follower) -> None:
        if self.config_path.suffix.casefold() == ".csv":
            rows: list[dict] = []
            if self.config_path.exists() and self.config_path.stat().st_size > 0:
                with open(self.config_path, "r", encoding=_detect_encoding(self.config_path), newline="") as f:
                    rows = list(csv.DictReader(f))
            for row in rows:
                existing = _normalize_follower(row)
                if existing and follower_key(existing) == follower_key(follower):
                    raise ValueError(f"该直播间已关注：{existing.name} ({existing.platform})")
            rows.append(_follower_to_csv_row(follower))

            def write_rows(f) -> None:
                writer = csv.DictWriter(f, fieldnames=FOLLOWER_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)

            _atomic_write(self.config_path, "utf-8-sig", write_rows)
            return

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        followers = data.setdefault("followers", [])
        for row in followers:
            existing = _normalize_follower(row) if isinstance(row, dict) else None
            if existing and follower_key(existing) == follower_key(follower):
                raise ValueError(f"该直播间已关注：{existing.name} ({existing.platform})")

        follower_dict = {
            "name": follower.name,
            "plugin": follower.plugin,
            "url": follower.url,
            "platform": follower.platform,
            "quality": follower.quality,
            "tags": follower.tags,
            "extra": follower.extra,
            "enabled": follower.enabled,
            "fallback_plugins": follower.fallback_plugins,
        }
        followers.append(follower_dict)

        def write_yaml(f) -> None:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        _atomic_write(self.config_path, "utf-8", write_yaml)
