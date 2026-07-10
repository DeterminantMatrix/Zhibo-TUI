from dataclasses import dataclass, field

@dataclass
class Follower:
    """主播/直播间关注信息模型"""
    name: str
    plugin: str
    url: str
    platform: str = ""
    quality: str = "best"
    tags: list[str] = field(default_factory=lambda: ["未分类"])
    extra: dict = field(default_factory=dict)
    enabled: bool = True
    fallback_plugins: list[str] = field(default_factory=list)

@dataclass
class AppConfig:
    """应用程序全局配置"""
    poll_interval: int = 60
    max_concurrent_checks: int = 8
    failure_backoff_after: int = 3
    failure_backoff_polls: int = 2
    notifications_enabled: bool = True
    followers: list[Follower] = field(default_factory=list)
