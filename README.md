# Zhibo TUI

一个基于 Python 与 Textual 的多平台直播监控工具。它按标签展示关注列表，定时检测开播状态，并支持一键用播放器打开直播流。

## 功能

- 监控斗鱼、虎牙、抖音、B 站、Twitch、YouTube、小红书与 FS1 等直播源
- 标签页、搜索和在线 / 离线 / 异常状态筛选
- 多插件回退：streamlink、streamget、yt-dlp 与 FS1 专用插件
- 开播通知、Windows 托盘、mpv / PotPlayer 播放
- 从直播间 URL 导入关注项，以及 YouTube 格式选择下载

## 安装

需要 Python 3.11+。在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

播放普通直播需要将 `mpv` 加入 `PATH`；Twitch / YouTube 也可使用 PotPlayer。

## 配置关注列表

首次运行前，在项目根目录创建 `followers.csv`。示例：

```csv
enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id
true,示例主播,游戏,streamlink,streamget,twitch,https://www.twitch.tv/example,best,
true,LPL,LOL,streamlink,streamget,bilibili,https://live.bilibili.com/6,best,
```

可选的全局设置保存在同目录 `settings.csv`：

```csv
key,value
poll_interval,60
max_concurrent_checks,8
notifications_enabled,true
```

`quality=best` 会请求插件提供的最高可用画质；也可填写平台支持的具体档位。

## 代理

Twitch、YouTube 等国际平台默认通过 `http://127.0.0.1:7890` 代理。可用环境变量覆盖：

```powershell
# 使用其它代理端口
$env:ZHIBO_PLATFORM_PROXY = "http://127.0.0.1:7897"

# 当前会话强制直连
$env:ZHIBO_PLATFORM_PROXY = "direct"
```

## 快捷键

| 按键 | 操作 |
| --- | --- |
| `Enter` | 播放选中直播间 |
| `r` | 手动刷新 |
| `q` | 切换标签页 |
| `o` | 切换全部 / 在线 / 离线 / 异常筛选 |
| `f` | 浏览器打开原直播间 |
| `c` | 复制最新直播流地址 |
| `j` | 导入直播间 URL |
| `h` | 隐藏至托盘 |
| `t` | 退出 |

## 本机敏感文件

`cookies.txt`、`followers.csv`、`settings.csv` 与 `sports/rooms.yaml` 均为本机运行数据，已经被 Git 忽略。请不要提交 Cookie、授权令牌或 FS1 配置。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
