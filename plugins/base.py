"""直播流插件基类与数据模型"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class LiveInfo:
    """直播检测结果"""
    is_live: bool
    anchor_name: str = ""
    title: str = ""
    stream_url: str = ""
    m3u8_url: str = ""
    flv_url: str = ""
    quality_name: str = ""
    extra: dict = field(default_factory=dict)


class LiveStreamPlugin(ABC):
    """直播流插件基类 — 所有平台插件必须继承此类"""

    name: str

    @abstractmethod
    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        """检测直播间是否在播，返回 LiveInfo"""
        ...

    @abstractmethod
    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str:
        """获取直播流播放地址"""
        ...
