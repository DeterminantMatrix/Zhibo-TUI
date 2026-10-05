# 直播监控工具 — 设计规格书

**日期**: 2026-05-12  
**状态**: 已确认，待实施

---

## 1. 概述

基于 Python 的命令行 TUI 直播监控工具，支持多平台主播开播检测与一键 mpv 播放。
通过可扩展的插件体系，同时支持 streamget / streamlink 通用平台和用户自定义的直播源。

## 2. 核心功能

| 功能 | 描述 |
|------|------|
| 持续轮询 | 定时检测所有关注主播的开播状态 |
| 标签分组 | 按 tag 筛选显示主播，`q` 键切换分组 |
| TUI 展示 | textual 构建现代化终端界面，表格+状态栏 |
| 一键播放 | 选中在线主播按 `Enter` → mpv 播放 |
| 插件扩展 | 自定义插件支持非标准直播网站 |

## 3. 插件体系

### 3.1 插件基类

```python
@dataclass
class LiveInfo:
    is_live: bool
    anchor_name: str = ""
    title: str = ""
    stream_url: str = ""      # 直接播放地址
    m3u8_url: str = ""
    flv_url: str = ""
    quality_name: str = ""
    extra: dict = field(default_factory=dict)

class LiveStreamPlugin(ABC):
    name: str  # 唯一标识，如 "streamget", "fs1"

    @abstractmethod
    async def check_live(self, url: str, **kwargs) -> LiveInfo: ...

    @abstractmethod
    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str: ...
```

### 3.2 内置插件

| 插件名 | 实现文件 | 说明 |
|--------|----------|------|
| `streamget` | `plugins/streamget_plugin.py` | 通过 `fetch_web_stream_data` + `fetch_stream_url` 两阶段检测 |
| `streamlink` | `plugins/streamlink_plugin.py` | 通过 `session.streams()` 一步检测（在线程池中运行同步 API） |

### 3.3 自定义插件

放在 `plugins/` 目录下，文件名 = 插件名（不含 `_plugin` 后缀时，类名的 `_Plugin` 部分会自动去除）。自动发现机制：遍历 `plugins/` 目录中 `*_plugin.py` 文件，导入并注册。

### 3.4 platform 映射（streamget 用）

配置文件中的 `platform` 字段映射到 streamget 类：

```python
STREAMGET_PLATFORMS = {
    "douyin": DouyinLiveStream,
    "douyu": DouyuLiveStream,
    "huya": HuyaLiveStream,
    "bilibili": BilibiliLiveStream,
    "youtube": YoutubeLiveStream,
    "twitch": TwitchLiveStream,
    # ... 全量
}
```

## 4. 配置文件

**文件**: `followers.yaml`

```yaml
poll_interval: 60

followers:
  - name: "主播A"
    plugin: streamget
    platform: douyin
    url: "https://live.douyin.com/xxxxxx"
    quality: HD
    tags: ["游戏", "FPS"]

  - name: "体育直播"
    plugin: fs1
    url: "12345"
    quality: gqzm
    tags: ["体育", "NBA"]
    extra:
      sport_id: "1"
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 显示名称 |
| `plugin` | 是 | 使用的插件名 |
| `url` | 是 | 直播间 URL（或 ID） |
| `platform` | streamget 必填 | platform 映射 key |
| `quality` | 否 | 画质偏好，默认 `"best"` |
| `tags` | 否 | 分组标签，默认 `["未分类"]` |
| `extra` | 否 | 透传给插件的自定义参数 |

## 5. 目录结构

```
zhibo/
├── main.py                    # 程序入口
├── config.py                  # 配置加载与校验
├── monitor.py                 # 轮询调度器 + 状态管理
├── tui.py                     # textual TUI 界面
├── mpv_player.py              # mpv 播放封装
├── followers.yaml             # 关注列表配置
└── plugins/
    ├── __init__.py             # 插件自动发现与注册
    ├── base.py                 # LiveInfo / LiveStreamPlugin 基类
    ├── streamget_plugin.py     # streamget 通用插件
    ├── streamlink_plugin.py    # streamlink 通用插件
    └── fs1_plugin.py           # 飞速直播自定义插件（从 sports/fs1.py 迁移）
```

## 6. 模块职责

### 6.1 `config.py`
- 加载 `followers.yaml`
- 校验必填字段
- 默认值填充（tags 默认 `["未分类"]`，quality 默认 `"best"`）
- 从所有 followers 中提取标签列表

### 6.2 `plugins/__init__.py`
- 自动扫描 `plugins/*_plugin.py`
- 注册所有插件到 dict `{name: LiveStreamPlugin}`
- 内置插件常驻，自定义插件可热加载

### 6.3 `monitor.py`
- 持有所有 follower 的状态快照
- `poll_all()`: 并发检测所有 follower（按当前标签筛选）
- 状态变化时发出事件（开播/下播通知）
- 提供 `get_followers_by_tag(tag)` 供 TUI 使用

### 6.4 `tui.py` (textual)
- 顶部: `TabbedContent` 标签栏，自动聚合 tags
- 主体: `DataTable` 显示表格（序号、状态圆点、主播名、标题、画质、插件名）
- 底部: `Footer` 状态栏显示操作提示和日志
- 按键绑定:
  - `↑↓` / `j k`: 移动光标
  - `Enter`: 播放当前选中主播
  - `q`: 切换到下一个标签
  - `r`: 手动触发一轮检测
  - `t`: 退出

### 6.5 `mpv_player.py`
- `play_url(url, title)`: 启动 mpv 子进程
- 参数: `--no-cache`, `--stream-lavf-o=reconnect=1`, `--title=...`

### 6.6 `main.py`
- 创建 asyncio 事件循环
- 启动 textual App
- 在 App 内部挂载定时轮询任务

## 7. 数据流

```
followers.yaml ──→ config.load() ──→ monitor.poll_all()
                                         │
                                    plugins[plugin].check_live(url)
                                         │
                                    [LiveInfo] ──→ TUI 更新表格
                                         │
                                    Enter 键
                                         │
                                    plugins[plugin].get_stream_url()
                                         │
                                    mpv_player.play(stream_url)
```

## 8. 轮询策略

- 按 `poll_interval` 秒定时执行全量检测
- 使用 `asyncio.gather` 并发检测所有 follower
- 单个 follower 检测超时 15 秒
- 检测失败不影响其他 follower
- 仅当 `is_live` 状态变化时记录日志提示

## 9. 错误处理

| 场景 | 处理 |
|------|------|
| 配置文件不存在 | 提示并退出 |
| 未知 plugin 名 | 加载时报错跳过该 follower |
| 单次检测超时 | 标记为离线，记录警告日志 |
| 网络错误 | 标记为错误状态，下次轮询重试 |
| mpv 未安装 | 提示安装 mpv |
| 流地址为空 | 提示无法获取，不启动 mpv |

## 10. 技术选型

| 组件 | 选型 | 原因 |
|------|------|------|
| 语言 | Python 3.12 | streamget/streamlink 均为 Python 库 |
| TUI | textual | 现代化、异步原生、DataTable 组件 |
| 拉流检测 | streamget (主) | 明确 is_live 字段，两步模式适合轮询 |
| 拉流检测 | streamlink (辅) | 国际平台覆盖更广 |
| 播放器 | mpv | 用户指定 |
| 配置格式 | YAML | 简洁可读，与 fs1.py 风格统一 |
