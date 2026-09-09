# Zhibo 直播监控

一个基于 Python 与 pywebview（Windows 98 复古风格界面）的多平台直播监控工具。它按标签展示关注列表，定时检测开播状态，并支持一键用播放器打开直播流。

## 功能

- 监控斗鱼、虎牙、抖音、B 站、Twitch、YouTube、小红书与 FS1 等直播源
- 标签页、搜索和在线 / 离线 / 异常状态筛选
- 多插件回退：streamlink、streamget、yt-dlp 与 FS1 专用插件
- 开播 toast 通知（点击直接播放）、系统托盘、最小化隐藏、外部 mpv 播放
- 从直播间 URL 导入关注项，以及 YouTube 格式选择下载
- 更新中心：自动检查并更新 mpv/ffmpeg/uosc/解析组件，管理 B站 Cookie 与 FS1 配置

## 安装与启动

需要 Python 3.12+。在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

双击 `webui\start_web.vbs` 启动（通过 `pythonw.exe` 运行），也可以运行：

```powershell
.venv\Scripts\python.exe -m webui
```

旧版 Qt Quick 界面保留为备用入口：双击 `qt_quick\start_native.vbs`，或 `.venv\Scripts\python.exe -m qt_quick`（需要先 `python -m pip install -r qt_quick\requirements-qt.txt` 安装 PySide6）。

## 目录结构

```text
ZHIBO/
├── webui/               # pywebview 前端（主入口，双击 start_web.vbs）
├── qt_quick/            # 旧版 Qt Quick/QML 前端（备用，双击 start_native.vbs）
├── zhibo/               # 后端核心包
│   ├── monitor.py       #   轮询调度、状态机、平台熔断退避
│   ├── config.py        #   followers.csv / settings.csv 持久化与校验
│   ├── plugins/         #   streamlink / streamget / yt-dlp / FS1 / B站插件
│   └── ...              #   导入预览、代理、画质、工具更新、mpv 播放等
├── tests/               # pytest 测试套件
├── userscripts/         # 配套油猴脚本（FS1 授权导出）
├── docs/                # 历史设计文档与审计记录
├── followers.csv        # 关注列表（本机数据）
├── settings.csv         # 全局设置（本机数据）
└── requirements*.txt    # 依赖清单
```

播放优先使用更新中心安装的便携版 `mpv`，也兼容系统 `PATH` 中的版本。所有平台统一使用 mpv，并自动应用该平台的代理和直播缓冲参数。更新中心还可独立安装轻量的 `uosc` 现代播放界面；它保存在用户私有目录，更新 MPV 时不会被覆盖。

## 配置关注列表

首次运行前，在项目根目录创建 `followers.csv`。示例：

```csv
enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id,extra
true,示例主播,游戏,streamlink,streamget,twitch,https://www.twitch.tv/example,best,,
true,LPL,LOL,streamlink,streamget,bilibili,https://live.bilibili.com/6,best,,
```

关注列表仅支持 CSV。`extra` 是可选 JSON 对象列，供插件保存扩展参数；`sport_id` 继续保留为兼容列。

`extra` 里还可以写 `"poll_interval": 秒数` 为单个主播设置独立轮询间隔（5–3600，覆盖全局间隔），适合低频关注的直播间；手动刷新（`F5`/刷新按钮）不受该间隔限制，始终全量检测：

```csv
enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id,extra
true,低频主播,游戏,streamlink,streamget,twitch,https://www.twitch.tv/example,best,,"{""poll_interval"":""600""}"
```

可选的全局设置保存在同目录 `settings.csv`：

```csv
key,value
poll_interval,60
max_concurrent_checks,8
notifications_enabled,true
```

`quality=best` 会请求插件提供的最高可用画质；也可填写平台支持的具体档位。

## 在界面中安全管理配置

导入直播间时，程序会先进行本地安全校验，再解析并展示预览；重复项会被阻止，名称或房间冲突需要在第二次确认时明确允许，确认前不会修改 `followers.csv`。导入和编辑都会拒绝将 Cookie、授权令牌、认证头、带敏感查询参数的 URL 等写入关注项，也不会把这类 URL 交给插件探测。

编辑关注项采用"编辑草稿 → 脱敏差异预览 → 确认保存"的流程。保存时会再次检查磁盘中的原配置是否变化，避免把其他进程或手工编辑的内容覆盖掉。禁用的关注项仍可在表格中查看和编辑，但不会参与轮询。

通过"设置"窗口可以修改以下全局监控参数，同样需要确认后才会保存：轮询间隔（5–3600 秒）、最大并发检测数（1–16）、失败后进入平台退避的阈值（1–20）、退避轮数（1–60）和桌面通知开关。手工编辑 `settings.csv` 的越界值也会在启动时被限制在安全范围内，随后可在此界面保存为合法值。平台代理不在设置窗口修改，仍使用"代理"窗口。

## 监控保护与状态说明

轮询同时受到单项检测时限、整轮时限和平台级退避保护。网络超时会让对应平台暂缓探测并指数退避；恢复后才会重新进入正常轮询。播放和复制流地址也遵守相同的超时与平台健康检查。

如果一行显示红色状态且仍保留上一次的直播标题或在线圆点，表示"上次确认在线、当前检测异常"，不是当前确认在线；它不会重复触发开播通知，也不能用于播放或复制流地址，需等待下一次确认成功。

## 代理

仅 Twitch、YouTube、Kick、CHZZK、TikTok 与 TwitCasting 默认通过 `http://127.0.0.1:7890` 代理；哔哩哔哩、斗鱼、虎牙、抖音、小红书与 FS1 一律直连。可用环境变量覆盖：

```powershell
# 使用其它代理端口
$env:ZHIBO_PLATFORM_PROXY = "http://127.0.0.1:7897"

# 当前会话强制直连
$env:ZHIBO_PLATFORM_PROXY = "direct"
```

也可在主界面"代理"窗口中，分别为 Twitch、YouTube、Kick、CHZZK、TikTok 和 TwitCasting 设置端口。输入 `7897` 会使用 `http://127.0.0.1:7897`；输入 `direct` 可让该平台直连。

## 本机敏感文件

`followers.csv` 与 `settings.csv` 是项目内的本机运行数据，已经被 Git 忽略。请不要提交 Cookie、授权令牌或 FS1 配置。Git 忽略不等于云盘保护。

Cookie、FS1 配置和日志只存放在用户私有目录，而非项目/WPS 同步目录：Windows 为 `%LOCALAPPDATA%\Zhibo`（优先于 `%APPDATA%`）。默认文件位置为：

- `cookies.txt`
- `fs1\rooms.yaml`
- `logs\zhibo.log`

可用 `ZHIBO_DATA_DIR` 覆盖整个私有目录，或保留原有的单项兼容覆盖：

```powershell
$env:ZHIBO_DATA_DIR = "D:\PrivateData\Zhibo"
$env:ZHIBO_COOKIE_FILE = "D:\PrivateData\Zhibo\cookies.txt"
$env:ZHIBO_FS_CONFIG = "D:\PrivateData\Zhibo\fs1\rooms.yaml"
$env:ZHIBO_LOG_FILE = "D:\PrivateData\Zhibo\logs\zhibo.log"
```

项目内旧位置的 `cookies.txt` 与 `sports/rooms.yaml` 已于 2026-09-07 清理删除，程序不会读取项目内的任何凭据文件。如需将凭据放回自定义位置，可用上文的环境变量显式指定。

在"更新"窗口选择"B站 Cookie"后，可粘贴本人导出的 Netscape `cookies.txt` 内容。程序会校验有效的根域 `SESSDATA`，只替换 B站条目，并保留同一私有文件中的其他站点 Cookie（例如 YouTube）；粘贴内容不会写入日志或详情界面。不要粘贴 HTTP `Cookie:` 请求头，也不要把 Cookie 发给他人。

### FS1 登录授权快速导入

项目内的 [`userscripts/fs1-auth-export.user.js`](<D:/WPS SyncDisk/2.Tool/1.VibeCoding/ZHIBO/userscripts/fs1-auth-export.user.js>) 是配套的 Tampermonkey 脚本。安装后，在已经登录的飞速直播页面刷新，脚本会观察该页面实际发出的 `/v1/room` 请求；点击右下角"导出 FS1 授权"，即可将版本化的 `zhibo.fs1-auth` JSON 写入剪贴板。随后在"更新 → FS1 配置"中直接粘贴并开始更新。

脚本与更新模块的交接字段包括当前站点 origin、`/v1/room` API、`authorization`、`api-version`、`imei`、`dun-imei`、User-Agent 和当前页面可读的 Cookie。更新模块仍兼容旧版 curl，并会校验 HTTPS、固定 API 主机和受限的 `fs` / `fszb` 数字域名；因此 `fs148.com`、`fszb148.com`、`fszb130.com`、`fszb321.com` 等站点可以轮换使用，站点域名不会被写死为 148。房间的 `room_id` / `sport_id` 仍由关注列表配置负责，不会因为导出授权而自动新增关注项。

浏览器脚本不能读取 HttpOnly Cookie，也不能可靠地读取浏览器自动附加但未由页面显式设置的 `Cookie` 请求头；这不会影响当前 FS1 主要使用的 `authorization` 请求头捕获。授权数据只通过剪贴板交给本机更新窗口，不开放常驻 localhost 接收端。不要把导出的 JSON 发给他人；如果已经泄露授权令牌，应先在 FS1 端注销或重新登录。

## 测试

```powershell
python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

默认测试不会访问外部直播平台。需要手动运行真实平台集成测试时：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m integration
```
