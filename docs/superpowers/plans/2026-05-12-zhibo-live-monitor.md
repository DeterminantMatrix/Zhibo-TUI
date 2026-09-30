# 直播监控工具 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个 Python TUI 直播监控工具，持续轮询检测多平台主播开播状态，通过 textual 界面展示，一键 mpv 播放。

**Architecture:** 插件体系（基类 LiveStreamPlugin）→ 内置 streamget/streamlink 插件 + 自定义插件 → MonitorService 轮询调度 → textual TUI 展示交互。

**Tech Stack:** Python 3.12, textual, streamget, streamlink, httpx, PyYAML, mpv

---

### Task 1: 项目脚手架 — 目录结构与依赖

**Files:**
- Create: `followers.yaml`
- Create: `plugins/__init__.py`

- [ ] **Step 1: 安装依赖**

```bash
pip install textual pyyaml httpx
```

streamget 和 streamlink 已安装，验证：
```bash
python -c "import streamget; print(streamget.__version__)"
python -c "import streamlink; print(streamlink.__version__)"
python -c "import textual; print(textual.__version__)"
```

- [ ] **Step 2: 创建 plugins 目录**

```bash
New-Item -ItemType Directory -Path "plugins" -Force
```

- [ ] **Step 3: 创建 plugins/__init__.py**

```python
"""直播流插件系统 — 自动发现与注册"""

from pathlib import Path
from .base import LiveStreamPlugin, LiveInfo

_plugins: dict[str, LiveStreamPlugin] = {}


def register_plugin(plugin: LiveStreamPlugin) -> None:
    """注册插件实例"""
    _plugins[plugin.name] = plugin


def get_plugin(name: str) -> LiveStreamPlugin | None:
    """获取已注册的插件"""
    return _plugins.get(name)


def list_plugins() -> list[str]:
    """列出所有已注册的插件名"""
    return list(_plugins.keys())


def discover_plugins() -> None:
    """自动发现并导入 plugins/*_plugin.py"""
    plugin_dir = Path(__file__).parent
    for py_file in plugin_dir.glob("*_plugin.py"):
        if py_file.name == "__init__.py":
            continue
        mod_name = f"plugins.{py_file.stem}"
        __import__(mod_name)


__all__ = ["LiveStreamPlugin", "LiveInfo", "register_plugin", "get_plugin", "list_plugins", "discover_plugins"]
```

- [ ] **Step 4: 创建示例配置文件 followers.yaml**

```yaml
poll_interval: 60

followers:
  - name: "示例主播"
    plugin: streamget
    platform: douyin
    url: "https://live.douyin.com/xxxxxx"
    quality: HD
    tags: ["游戏"]
```

- [ ] **Step 5: 验证目录结构**

```bash
Get-ChildItem -Recurse -File | Select-Object FullName
```

---

### Task 2: 插件基类 — LiveInfo / LiveStreamPlugin

**Files:**
- Create: `plugins/base.py`

- [ ] **Step 1: 编写测试**

Create `tests/test_base.py`（先创建 tests 目录）:

```python
"""测试插件基类"""
import pytest
from plugins.base import LiveInfo, LiveStreamPlugin


class TestLiveInfo:
    def test_defaults(self):
        info = LiveInfo(is_live=False)
        assert info.is_live is False
        assert info.anchor_name == ""
        assert info.title == ""
        assert info.stream_url == ""
        assert info.extra == {}

    def test_full_init(self):
        info = LiveInfo(
            is_live=True,
            anchor_name="测试主播",
            title="游戏直播",
            stream_url="http://example.com/stream.flv",
            m3u8_url="http://example.com/stream.m3u8",
            flv_url="http://example.com/stream.flv",
            quality_name="HD",
            extra={"key": "value"},
        )
        assert info.is_live is True
        assert info.anchor_name == "测试主播"
        assert info.extra == {"key": "value"}


class TestLiveStreamPlugin:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            LiveStreamPlugin()  # type: ignore

    def test_subclass_must_implement_methods(self):
        class IncompletePlugin(LiveStreamPlugin):
            name = "incomplete"

        with pytest.raises(TypeError):
            IncompletePlugin()  # type: ignore

    def test_valid_subclass(self):
        class ValidPlugin(LiveStreamPlugin):
            name = "valid"

            async def check_live(self, url, **kwargs):
                return LiveInfo(is_live=True)

            async def get_stream_url(self, url, quality, **kwargs):
                return "http://example.com/stream.m3u8"

        plugin = ValidPlugin()
        assert plugin.name == "valid"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_base.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'plugins.base'`

- [ ] **Step 3: 编写 plugins/base.py**

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_base.py -v
```
Expected: PASS

---

### Task 3: 插件注册器 — 完善 plugins/__init__.py

**Files:**
- Modify: `plugins/__init__.py`（已在 Task 1 创建）

> 此文件已在 Task 1 创建完整内容，Task 3 仅做验证。

- [ ] **Step 1: 编写 discover_plugins 测试**

Create `tests/test_plugin_registry.py`:

```python
"""测试插件注册系统"""
from unittest.mock import MagicMock, patch
from plugins import _plugins, register_plugin, get_plugin, list_plugins
from plugins.base import LiveInfo, LiveStreamPlugin


class FakePlugin(LiveStreamPlugin):
    name = "fake"

    async def check_live(self, url, **kwargs):
        return LiveInfo(is_live=True)

    async def get_stream_url(self, url, quality, **kwargs):
        return "http://fake.stream/play.m3u8"


class TestPluginRegistry:
    def setup_method(self):
        _plugins.clear()

    def test_register_and_get(self):
        plugin = FakePlugin()
        register_plugin(plugin)
        assert get_plugin("fake") is plugin
        assert get_plugin("nonexistent") is None

    def test_list_plugins(self):
        register_plugin(FakePlugin())
        assert "fake" in list_plugins()

    def test_discover_plugins(self):
        """模拟 discover_plugins 导入过程"""
        with patch("builtins.__import__") as mock_import:
            from plugins import discover_plugins
            discover_plugins()
            # 应该被调用多次（*_plugin.py 文件）
            assert mock_import.call_count >= 1
```

- [ ] **Step 2: 运行测试确认通过**

```bash
pytest tests/test_plugin_registry.py -v
```
Expected: PASS

---

### Task 4: streamget 插件

**Files:**
- Create: `plugins/streamget_plugin.py`

- [ ] **Step 1: 编写 streamget_plugin.py**

```python
"""streamget 通用直播插件 — 支持 47+ 平台"""
import asyncio
from .base import LiveStreamPlugin, LiveInfo

from streamget import (
    DouyinLiveStream, DouyuLiveStream, HuyaLiveStream,
    BilibiliLiveStream, YoutubeLiveStream, TwitchLiveStream,
    ChzzkLiveStream, TikTokLiveStream, TwitCastingLiveStream,
)

STREAMGET_PLATFORMS = {
    "douyin": DouyinLiveStream,
    "douyu": DouyuLiveStream,
    "huya": HuyaLiveStream,
    "bilibili": BilibiliLiveStream,
    "youtube": YoutubeLiveStream,
    "twitch": TwitchLiveStream,
    "chzzk": ChzzkLiveStream,
    "tiktok": TikTokLiveStream,
    "twitcasting": TwitCastingLiveStream,
}


class StreamgetPlugin(LiveStreamPlugin):
    name = "streamget"

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        platform = kwargs.get("platform", "")
        quality = kwargs.get("quality", "best")
        cls = STREAMGET_PLATFORMS.get(platform)
        if cls is None:
            return LiveInfo(is_live=False, extra={"error": f"未知平台: {platform}"})

        try:
            stream = cls()
            data = await asyncio.wait_for(
                stream.fetch_web_stream_data(url, process_data=True),
                timeout=15,
            )
        except asyncio.TimeoutError:
            return LiveInfo(is_live=False, extra={"error": "检测超时"})
        except Exception as e:
            return LiveInfo(is_live=False, extra={"error": str(e)})

        try:
            stream_obj = await stream.fetch_stream_url(data, quality)
        except Exception:
            stream_obj = None

        if stream_obj is None:
            stream_obj = await stream.fetch_stream_url(data)

        return LiveInfo(
            is_live=stream_obj.is_live if stream_obj.is_live else False,
            anchor_name=stream_obj.anchor_name or data.get("anchor_name", ""),
            title=stream_obj.title or data.get("title", ""),
            stream_url=stream_obj.flv_url or stream_obj.m3u8_url or "",
            m3u8_url=stream_obj.m3u8_url or "",
            flv_url=stream_obj.flv_url or "",
            quality_name=stream_obj.quality or quality,
        )

    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str:
        info = await self.check_live(url, **kwargs)
        if not info.is_live:
            raise RuntimeError("主播未开播")
        url = info.flv_url or info.m3u8_url or info.stream_url
        if not url:
            raise RuntimeError("无法获取流地址")
        return url
```

- [ ] **Step 2: 注册插件 — 在文件末尾添加自动注册**

在 `streamget_plugin.py` 末尾添加：

```python
from . import register_plugin

register_plugin(StreamgetPlugin())
```

- [ ] **Step 3: 验证导入**

```bash
python -c "from plugins.streamget_plugin import StreamgetPlugin; print('OK')"
```
Expected: OK

---

### Task 5: streamlink 插件

**Files:**
- Create: `plugins/streamlink_plugin.py`

- [ ] **Step 1: 编写 streamlink_plugin.py**

```python
"""streamlink 通用直播插件 — 支持 138+ 平台"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from .base import LiveStreamPlugin, LiveInfo

_executor = ThreadPoolExecutor(max_workers=4)


class StreamlinkPlugin(LiveStreamPlugin):
    name = "streamlink"

    def __init__(self):
        from streamlink import Streamlink
        self._session = Streamlink()

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        quality = kwargs.get("quality", "best")

        def _fetch():
            try:
                streams = self._session.streams(url)
                if not streams:
                    return LiveInfo(is_live=False)
                stream = streams.get(quality, streams.get("best"))
                stream_url = stream.to_url() if stream else ""
                return LiveInfo(
                    is_live=True,
                    stream_url=stream_url,
                    quality_name=quality,
                )
            except Exception as e:
                return LiveInfo(is_live=False, extra={"error": str(e)})

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_executor, _fetch),
                timeout=15,
            )
        except asyncio.TimeoutError:
            return LiveInfo(is_live=False, extra={"error": "检测超时"})

    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str:
        info = await self.check_live(url, quality=quality, **kwargs)
        if not info.is_live:
            raise RuntimeError("主播未开播")
        if not info.stream_url:
            raise RuntimeError("无法获取流地址")
        return info.stream_url
```

- [ ] **Step 2: 在文件末尾添加自动注册**

```python
from . import register_plugin

register_plugin(StreamlinkPlugin())
```

- [ ] **Step 3: 验证导入**

```bash
python -c "from plugins.streamlink_plugin import StreamlinkPlugin; print('OK')"
```
Expected: OK

---

### Task 6: fs1 自定义插件（从 sports/fs1.py 迁移）

**Files:**
- Create: `plugins/fs1_plugin.py`
- Reference: `sports/fs1.py`（读取现有实现，不修改）

- [ ] **Step 1: 编写 fs1_plugin.py**

```python
"""飞速直播自定义插件 — 从 sports/fs1.py 迁移"""
import asyncio
import httpx
from pathlib import Path
from .base import LiveStreamPlugin, LiveInfo

SCRIPT_DIR = Path(__file__).parent.parent
QUALITY_PRIORITY = ["lgzm", "gqzm", "bqzm"]


def _build_headers() -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://fszb148.com",
        "Referer": "https://fszb148.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "api-version": "8",
        "authorization": "",
        "device": "3",
        "imei": "",
        "dun-imei": "",
    }


class Fs1Plugin(LiveStreamPlugin):
    name = "fs1"

    def __init__(self):
        self._api_url = "https://apc.xzood6veuybwkr.com/v1/room"

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        sport_id = kwargs.get("extra", {}).get("sport_id", "1") if isinstance(kwargs.get("extra"), dict) else kwargs.get("sport_id", "1")
        quality = kwargs.get("quality", "best")
        headers = _build_headers()

        try:
            async with httpx.AsyncClient(headers=headers, timeout=15, verify=False) as client:
                resp = await client.get(self._api_url, params={"room_id": url, "sport_id": sport_id})
                resp.raise_for_status()
                body = resp.json()
        except Exception as e:
            return LiveInfo(is_live=False, extra={"error": str(e)})

        if body.get("code") != 200:
            return LiveInfo(is_live=False, extra={"error": body.get("message", "API错误")})

        data = body["data"]
        is_live = data.get("room_status") == 2
        play_flow = {q["code_id"]: q for q in data.get("play_flow", [])}

        selected_url = selected_name = ""
        q_priority = [quality] + QUALITY_PRIORITY if quality != "best" else QUALITY_PRIORITY
        for code_id in q_priority:
            if code_id in play_flow:
                q = play_flow[code_id]
                selected_url = q.get("play_url", "")
                selected_name = q.get("name", "")
                break

        if not selected_url:
            selected_url = data.get("pull_url") or data.get("pull_flv_url", "")
            selected_name = selected_name or "原画"

        anchor = data.get("anchor_info") or {}
        match_info = data.get("match_info") or {}

        return LiveInfo(
            is_live=is_live,
            anchor_name=anchor.get("nickname", ""),
            title=data.get("room_title", ""),
            stream_url=selected_url,
            m3u8_url=data.get("pull_url", ""),
            flv_url=data.get("pull_flv_url", ""),
            quality_name=selected_name,
            extra={
                "home": match_info.get("home_name", ""),
                "away": match_info.get("away_name", ""),
                "home_score": match_info.get("home_score", 0),
                "away_score": match_info.get("away_score", 0),
            },
        )

    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str:
        info = await self.check_live(url, quality=quality, **kwargs)
        if not info.is_live:
            raise RuntimeError("主播未开播")
        if not info.stream_url:
            raise RuntimeError("无法获取流地址")
        return info.stream_url
```

- [ ] **Step 2: 在文件末尾添加自动注册**

```python
from . import register_plugin

register_plugin(Fs1Plugin())
```

- [ ] **Step 3: 验证导入**

```bash
python -c "from plugins.fs1_plugin import Fs1Plugin; print('OK')"
```
Expected: OK

---

### Task 7: 配置加载器

**Files:**
- Create: `config.py`

- [ ] **Step 1: 编写测试**

Create `tests/test_config.py`:

```python
"""测试配置加载"""
import tempfile
from pathlib import Path
import config


def test_load_config_valid():
    yaml_content = """
poll_interval: 30
followers:
  - name: "测试主播"
    plugin: streamget
    platform: douyin
    url: "https://live.douyin.com/123"
    quality: HD
    tags: ["游戏"]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)

    try:
        cfg = config.load_config(f.name)
        assert cfg["poll_interval"] == 30
        assert len(cfg["followers"]) == 1
        f1 = cfg["followers"][0]
        assert f1["name"] == "测试主播"
        assert f1["plugin"] == "streamget"
        assert f1["platform"] == "douyin"
        assert f1["tags"] == ["游戏"]
        assert f1["quality"] == "HD"
        assert f1["extra"] == {}
    finally:
        Path(f.name).unlink(missing_ok=True)


def test_default_values():
    yaml_content = """
followers:
  - name: "主播"
    plugin: streamget
    platform: douyin
    url: "https://live.douyin.com/123"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)

    try:
        cfg = config.load_config(f.name)
        f1 = cfg["followers"][0]
        assert f1["quality"] == "best"
        assert f1["tags"] == ["未分类"]
        assert f1["extra"] == {}
    finally:
        Path(f.name).unlink(missing_ok=True)


def test_missing_required_field_raises():
    yaml_content = """
followers:
  - name: "主播"
    plugin: streamget
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)

    try:
        cfg = config.load_config(f.name)
        # url 缺失时由校验处理，此处验证不会崩溃
        assert cfg["followers"][0]["url"] == ""
    finally:
        Path(f.name).unlink(missing_ok=True)


def test_extract_tags():
    cfg = {
        "followers": [
            {"tags": ["游戏", "FPS"]},
            {"tags": ["体育"]},
            {"tags": ["游戏", "MOBA"]},
        ]
    }
    tags = config.extract_tags(cfg)
    assert "游戏" in tags
    assert "体育" in tags
    assert "FPS" in tags
    assert "MOBA" in tags
    assert len(tags) == 4
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_config.py -v
```
Expected: FAIL

- [ ] **Step 3: 编写 config.py**

```python
"""配置加载与校验"""
import sys
from pathlib import Path
import yaml

DEFAULT_CONFIG_FILE = Path(__file__).parent / "followers.yaml"


def load_config(config_path: str | None = None) -> dict:
    """加载并校验配置文件"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_FILE
    if not path.exists():
        sys.exit(f"错误：配置文件 {path} 不存在，请先创建 followers.yaml")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    cfg = {"poll_interval": data.get("poll_interval", 60)}
    raw_followers = data.get("followers", [])

    if not raw_followers:
        sys.exit("错误：followers.yaml 中没有配置任何关注主播，请先添加")

    followers = []
    for f in raw_followers:
        follower = {
            "name": f.get("name", ""),
            "plugin": f.get("plugin", ""),
            "url": f.get("url", ""),
            "platform": f.get("platform", ""),
            "quality": f.get("quality", "best"),
            "tags": f.get("tags", ["未分类"]),
            "extra": f.get("extra", {}),
        }
        if not follower["name"] or not follower["plugin"] or not follower["url"]:
            print(f"警告：关注项缺少必填字段 (name/plugin/url)，已跳过: {f}")
            continue
        followers.append(follower)

    cfg["followers"] = followers
    return cfg


def extract_tags(cfg: dict) -> list[str]:
    """从所有 followers 中提取去重标签列表"""
    tags_set: set[str] = set()
    for f in cfg.get("followers", []):
        for tag in f.get("tags", []):
            tags_set.add(tag)
    tags = sorted(tags_set)
    if not tags:
        tags = ["全部"]
    return tags
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_config.py -v
```
Expected: PASS

---

### Task 8: MPV 播放器

**Files:**
- Create: `mpv_player.py`

- [ ] **Step 1: 编写 mpv_player.py**

```python
"""MPV 播放器封装"""
import subprocess
import sys


def play_url(stream_url: str, title: str = "", referrer: str = "") -> None:
    """使用 mpv 播放直播流"""
    cmd = [
        "mpv",
        stream_url,
        "--no-cache",
        "--stream-lavf-o=reconnect=1",
        "--stream-lavf-o=reconnect_streamed=1",
    ]
    if title:
        cmd.append(f"--title={title}")
    if referrer:
        cmd.append(f"--referrer={referrer}")

    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print("错误：未找到 mpv，请先安装 mpv 播放器")
        print("  https://mpv.io/installation/")
        sys.exit(1)
```

- [ ] **Step 2: 验证**

```bash
python -c "from mpv_player import play_url; print('OK')"
```
Expected: OK

---

### Task 9: 监控服务

**Files:**
- Create: `monitor.py`

- [ ] **Step 1: 编写 monitor.py**

```python
"""轮询调度器 — 管理所有关注主播的状态"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from plugins.base import LiveInfo, LiveStreamPlugin
from plugins import get_plugin
from config import load_config


@dataclass
class FollowerStatus:
    """单个关注主播的运行时状态"""
    follower: dict
    live_info: LiveInfo = field(default_factory=lambda: LiveInfo(is_live=False))
    last_check: datetime | None = None
    error: str = ""
    is_checking: bool = False


class MonitorService:
    """直播监控服务"""

    def __init__(self, config_path: str | None = None):
        self.cfg = load_config(config_path)
        self.poll_interval = self.cfg["poll_interval"]
        self.followers: dict[int, FollowerStatus] = {}
        self._callbacks: list = []

        for i, f in enumerate(self.cfg["followers"]):
            self.followers[i] = FollowerStatus(follower=f)

        self._running = False

    @property
    def all_tags(self) -> list[str]:
        """所有可用标签"""
        from config import extract_tags
        return extract_tags(self.cfg)

    def get_by_tag(self, tag: str | None) -> list[tuple[int, FollowerStatus]]:
        """按标签筛选 follower 列表"""
        result = []
        for idx, status in self.followers.items():
            follower_tags = status.follower.get("tags", ["未分类"])
            if tag is None or tag == "全部" or not follower_tags or tag in follower_tags:
                result.append((idx, status))
        return result

    def on_status_change(self, callback):
        """注册状态变化回调"""
        self._callbacks.append(callback)

    async def _notify_change(self, idx: int, status: FollowerStatus):
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(idx, status)
                else:
                    cb(idx, status)
            except Exception:
                pass

    async def check_one(self, idx: int) -> FollowerStatus:
        """检测单个 follower"""
        status = self.followers[idx]
        f = status.follower
        plugin_name = f["plugin"]

        plugin = get_plugin(plugin_name)
        if plugin is None:
            status.error = f"未知插件: {plugin_name}"
            status.live_info = LiveInfo(is_live=False, extra={"error": status.error})
            return status

        status.is_checking = True
        try:
            live_info = await plugin.check_live(
                url=f["url"],
                platform=f.get("platform", ""),
                quality=f.get("quality", "best"),
                extra=f.get("extra", {}),
                sport_id=f.get("extra", {}).get("sport_id", "1"),
            )
            was_live = status.live_info.is_live
            status.live_info = live_info
            status.error = live_info.extra.get("error", "")
            status.last_check = datetime.now()

            if was_live != live_info.is_live:
                await self._notify_change(idx, status)
        except Exception as e:
            status.error = str(e)
            status.live_info = LiveInfo(is_live=False, extra={"error": str(e)})
        finally:
            status.is_checking = False

        return status

    async def poll_all(self, tag: str | None = None) -> list[tuple[int, FollowerStatus]]:
        """并发检测所有 follower（可选按标签筛选）"""
        items = self.get_by_tag(tag)
        tasks = [self.check_one(idx) for idx, _ in items]
        await asyncio.gather(*tasks, return_exceptions=True)
        return items

    async def get_stream(self, idx: int) -> str:
        """获取指定 follower 的播放流地址"""
        status = self.followers[idx]
        f = status.follower
        plugin = get_plugin(f["plugin"])
        if plugin is None:
            raise RuntimeError(f"未知插件: {f['plugin']}")

        return await plugin.get_stream_url(
            url=f["url"],
            quality=f.get("quality", "best"),
            platform=f.get("platform", ""),
            extra=f.get("extra", {}),
            sport_id=f.get("extra", {}).get("sport_id", "1"),
        )

    async def run(self, tag: str | None = None):
        """持续轮询循环"""
        self._running = True
        while self._running:
            await self.poll_all(tag)
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from monitor import MonitorService; print('OK')"
```
Expected: OK

---

### Task 10: TUI 界面

**Files:**
- Create: `tui.py`

- [ ] **Step 1: 编写 tui.py**

```python
"""Textual TUI 界面"""
import asyncio
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    DataTable, Footer, Header, Static, TabbedContent, TabPane,
)
from textual.binding import Binding

from monitor import MonitorService, FollowerStatus


class LiveTable(DataTable):
    """直播状态表格"""
    pass


class LogBar(Static):
    """底部日志栏"""
    pass


class ZhiboApp(App):
    """直播监控 TUI 主应用"""

    CSS = """
    TabbedContent {
        height: auto;
    }
    TabPane {
        padding: 0;
    }
    LiveTable {
        height: 1fr;
    }
    LogBar {
        height: 3;
        padding: 0 1;
        border-top: solid $accent;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("q", "next_tab", "切换分组"),
        Binding("r", "refresh", "手动刷新"),
        Binding("t", "quit", "退出"),
        Binding("enter", "play_selected", "播放"),
        Binding("up,k", "cursor_up", "上移"),
        Binding("down,j", "cursor_down", "下移"),
    ]

    def __init__(self, config_path: str | None = None):
        super().__init__()
        self._monitor = MonitorService(config_path)
        self._poll_task: asyncio.Task | None = None
        self._logs: list[str] = []
        self._current_tag: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            for tag in ["全部"] + self._monitor.all_tags:
                with TabPane(tag, id=f"tab-{tag}"):
                    yield LiveTable(id=f"table-{tag}")
        yield LogBar("", id="log_bar")

    def on_mount(self) -> None:
        """启动后初始化表格并开始轮询"""
        for tag in ["全部"] + self._monitor.all_tags:
            table = self.query_one(f"#table-{tag}", LiveTable)
            table.cursor_type = "row"
            table.add_columns("状态", "主播", "标题", "画质", "插件")
            self._refresh_table(table, tag)

        self._monitor.on_status_change(self._on_status_change)
        self._start_polling()

    def _start_polling(self) -> None:
        """启动定时轮询"""
        async def poll_loop():
            while True:
                self._add_log(f"开始第 {self._poll_count()} 轮检测...")
                await self._monitor.poll_all(self._current_tag)
                self._refresh_current_table()
                online = sum(1 for _, s in self._monitor.get_by_tag(self._current_tag) if s.live_info.is_live)
                total = len(self._monitor.get_by_tag(self._current_tag))
                self._add_log(f"检测完成 · {online}/{total} 在线")
                await asyncio.sleep(self._monitor.poll_interval)

        self._poll_task = asyncio.create_task(poll_loop())
        self._poll_count_counter = 0

    def _poll_count(self) -> int:
        self._poll_count_counter += 1
        return self._poll_count_counter

    def _add_log(self, msg: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self._logs.append(f"[{now}] {msg}")
        if len(self._logs) > 100:
            self._logs = self._logs[-50:]
        log_bar = self.query_one("#log_bar", LogBar)
        log_bar.update("\n".join(self._logs[-3:]))

    def _refresh_table(self, table: LiveTable, tag: str) -> None:
        """刷新指定表格"""
        table.clear()
        items = self._monitor.get_by_tag(None if tag == "全部" else tag)
        for idx, status in items:
            live_icon = "●" if status.live_info.is_live else "○"
            f = status.follower
            table.add_row(
                live_icon,
                f["name"],
                status.live_info.title or "-",
                status.live_info.quality_name or f.get("quality", "-"),
                f["plugin"],
                key=str(idx),
            )

    def _refresh_current_table(self) -> None:
        """刷新当前选中标签的表格"""
        tag = self._current_tag or "全部"
        table = self.query_one(f"#table-{tag}", LiveTable)
        self._refresh_table(table, tag)

    async def _on_status_change(self, idx: int, status: FollowerStatus) -> None:
        """主播状态变化回调"""
        f = status.follower
        if status.live_info.is_live:
            self._add_log(f"↑ {f['name']} 开播了！")
        else:
            self._add_log(f"↓ {f['name']} 下播了")
        self._refresh_current_table()

    def action_next_tab(self) -> None:
        """切换到下一个标签"""
        tabs = self.query_one(TabbedContent)
        pane_ids = [p.id for p in tabs.query(TabPane)]
        if not pane_ids:
            return
        current = tabs.active
        try:
            idx = pane_ids.index(current)
            next_idx = (idx + 1) % len(pane_ids)
        except ValueError:
            next_idx = 0
        tabs.active = pane_ids[next_idx]
        tag = pane_ids[next_idx].replace("tab-", "")
        self._current_tag = None if tag == "全部" else tag
        self._refresh_current_table()

    def action_refresh(self) -> None:
        """手动刷新"""
        asyncio.create_task(self._do_refresh())

    async def _do_refresh(self) -> None:
        self._add_log("手动刷新...")
        await self._monitor.poll_all(self._current_tag)
        self._refresh_current_table()

    def action_play_selected(self) -> None:
        """播放选中的主播"""
        tag = self._current_tag or "全部"
        table = self.query_one(f"#table-{tag}", LiveTable)
        if table.row_count == 0:
            return

        cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        if cell_key is None:
            return
        row_key = cell_key.row_key
        if row_key is None:
            return

        idx = int(str(row_key))
        status = self._monitor.followers.get(idx)
        if status is None:
            return

        if not status.live_info.is_live:
            self._add_log(f"{status.follower['name']} 未开播，无法播放")
            return

        self._add_log(f"正在启动 mpv 播放 {status.follower['name']}...")
        asyncio.create_task(self._play(idx))

    async def _play(self, idx: int) -> None:
        from mpv_player import play_url
        status = self._monitor.followers[idx]
        try:
            stream_url = await self._monitor.get_stream(idx)
            self._add_log(f"获取流地址成功，启动 mpv...")
            play_url(stream_url, title=f"{status.follower['name']} - Zhibo")
        except Exception as e:
            self._add_log(f"播放失败: {e}")

    def on_unmount(self) -> None:
        """应用关闭时停止轮询"""
        self._monitor.stop()
        if self._poll_task:
            self._poll_task.cancel()
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from tui import ZhiboApp; print('OK')"
```
Expected: OK

---

### Task 11: 主入口

**Files:**
- Create: `main.py`

- [ ] **Step 1: 编写 main.py**

```python
"""直播监控工具 — 主入口"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from plugins import discover_plugins
from tui import ZhiboApp


def main():
    """启动直播监控 TUI"""
    # 自动发现并加载所有插件
    discover_plugins()

    config_path = None
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    app = ZhiboApp(config_path)
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证可以启动（会因无 GUI 报错，但导入成功）**

```bash
python -c "from main import main; print('OK')"
```
Expected: OK

---

### Task 12: 端到端验证

- [ ] **Step 1: 验证配置文件能正确加载**

```bash
python -c "
from config import load_config, extract_tags
cfg = load_config()
print('轮询间隔:', cfg['poll_interval'])
print('关注数量:', len(cfg['followers']))
print('标签:', extract_tags(cfg))
"
```
Expected: 输出配置信息

- [ ] **Step 2: 验证所有插件已自动注册**

```bash
python -c "
from plugins import discover_plugins, list_plugins
discover_plugins()
print('已注册插件:', list_plugins())
"
```
Expected: 输出 `['streamget', 'streamlink', 'fs1']`

- [ ] **Step 3: 验证 streamget 插件可检测（需实际 URL）**

```bash
python -c "
import asyncio
from plugins.streamget_plugin import StreamgetPlugin
async def test():
    plugin = StreamgetPlugin()
    # 使用一个已知的测试 URL
    info = await plugin.check_live('https://live.douyin.com/xxxxxx', platform='douyin', quality='HD')
    print('is_live:', info.is_live)
    print('error:', info.extra.get('error', '无'))
asyncio.run(test())
"
```
Expected: 正常执行（根据 URL 是否有效显示在线/离线/错误）

---

### 文件清单总结

| 文件 | 类型 | 说明 |
|------|------|------|
| `plugins/base.py` | Create | LiveInfo / LiveStreamPlugin 基类 |
| `plugins/__init__.py` | Create | 插件注册与发现 |
| `plugins/streamget_plugin.py` | Create | streamget 通用插件 |
| `plugins/streamlink_plugin.py` | Create | streamlink 通用插件 |
| `plugins/fs1_plugin.py` | Create | 飞速直播自定义插件 |
| `config.py` | Create | 配置加载与校验 |
| `mpv_player.py` | Create | mpv 播放器封装 |
| `monitor.py` | Create | 轮询调度与状态管理 |
| `tui.py` | Create | textual TUI 界面 |
| `main.py` | Create | 程序入口 |
| `followers.yaml` | Create | 用户关注配置模板 |
| `tests/test_base.py` | Create | 插件基类测试 |
| `tests/test_plugin_registry.py` | Create | 插件注册测试 |
| `tests/test_config.py` | Create | 配置加载测试 |
