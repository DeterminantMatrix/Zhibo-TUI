# Zhibo 安卓迁移方案（Kotlin）

> 2026-09-10 设计稿。目标：把现有 Python 桌面版（TUI 主线 / Qt legacy）的直播监控能力迁移为
> Kotlin 安卓应用，内置浏览器负责登录取凭据、授权抓取与流地址嗅探兜底。
> 桌面版现状保持不变，数据通过 CSV 互通；核心层按"平台无关 Kotlin"设计，保留将来
> Compose Multiplatform 桌面端的演进路径。

## 1. 结论（TLDR）

- **可以迁移**，且大部分桌面架构（插件抽象、平台熔断、状态机）可以直译成 Kotlin 协程版。
- 推荐形态：**Android 原生 Kotlin App**（Compose UI + Media3 播放器），桌面继续用 Python TUI，
  两者靠 `followers.csv` / `settings.csv` 同格式互通。
- **内置浏览器是安卓版的刚需**，承担三件事：
  1. 登录采集 Cookie（B 站 SESSDATA 等，替代桌面"粘贴 cookies.txt"）；
  2. FS1 授权抓取（被动观察 `/v1/room` 请求头，替代油猴脚本 + 剪贴板）；
  3. resolver 失败时的流地址嗅探兜底（拦 `.m3u8` / `.flv` 请求）。
- 桌面版的 `streamlink` / `yt-dlp` / `mpv` 三个外部工具在安卓上都不存在，必须换方案
  （详见 §6 平台映射表，这是本迁移的主要工作量与风险所在）。

## 2. 双端策略：桌面怎么办

| 选项 | 说明 | 评价 |
| --- | --- | --- |
| A（推荐起步） | 安卓原生 Kotlin；桌面保持 Python TUI；CSV 双向互通 | 零迁移成本，先把安卓做出来 |
| B（远期可选） | Kotlin Multiplatform：`core:*` 模块纯 Kotlin，加 Compose Multiplatform 桌面目标 | 复用 90% 业务逻辑，但 UI 要整套重写，桌面已被 TUI 覆盖，收益低 |

本方案的模块划分按 KMP 兼容写法约束（core 层不 import Android 类，平台能力走接口注入），
从 A 演进到 B 不需要重构。

## 3. 技术选型

| 领域 | 选型 | 理由 |
| --- | --- | --- |
| 语言 | Kotlin 2.x，协程 + Flow | 与桌面 asyncio 模型一一对应 |
| UI | Jetpack Compose + Material3，navigation-compose | 单 Activity 壳 |
| 播放 | androidx.media3（ExoPlayer） | 内置 HLS、FLV（FlvExtractor，H.264/AAC）、DASH；替代 mpv |
| 网络 | OkHttp（KMP 演进时换 Ktor） | 按平台代理、自定义 UA/Referer、超时控制 |
| DI | Hilt | |
| 持久化 | Room（关注项、状态历史）+ DataStore（设置） | 替代两个 CSV，但保留 CSV 导入导出 |
| 凭据 | Android Keystore + EncryptedSharedPreferences | cookies / FS1 授权 / 代理配置 |
| 后台 | 前台服务（specialUse）+ WorkManager 低功耗档 | 见 §7 |
| 序列化 | kotlinx.serialization | `extra` JSON、FS1 报文 |
| 浏览器 | androidx.webkit WebView + CookieManager + shouldInterceptRequest | 见 §8 |

## 4. 模块结构

代码放在仓库 `android/` 子目录（Gradle 单独根，不干扰 Python 工程）。

```text
android/
├── app/                    # Android 壳：导航、DI 装配、前台服务、通知
├── core/
│   ├── model/              # 纯 Kotlin：Follower、LiveInfo、PlatformHealth、AppConfig
│   ├── common/             # 结果类型、时钟、日志接口（expect/actual 锚点）
│   ├── network/            # OkHttp 封装、平台代理路由、UA 池
│   ├── database/           # Room：followers、status_history、diagnostics
│   ├── datastore/          # 设置、凭据仓库（凭据走 Keystore 加密）
│   ├── resolver/           # 插件抽象 + 各平台 Kotlin Resolver（§6）
│   └── monitor/            # MonitorEngine：轮询、并发闸、平台熔断（§7）
├── feature/
│   ├── follows/            # 关注列表：标签、搜索、状态筛选、快捷操作
│   ├── detail/             # 详情/编辑（草稿 → 差异 → 确认，沿用桌面流程）
│   ├── player/             # PlayerManager + 播放页/PiP/通知栏控制
│   ├── browser/            # 内置浏览器（§8）
│   ├── importwizard/       # URL 导入：安全校验 → 预览 → 确认
│   └── settings/           # 全局设置、代理、凭据与平台工具页
└── gradle/…
```

分层规则：`core:*` 不依赖 Android 框架类（日志、时钟、IO 走接口），`feature:*` 只依赖 core 与
Compose。这样 `monitor` / `resolver` 可以直接跑 JVM 单测，与现有 `tests/` 的纯离线测试思路一致。

## 5. 领域模型（与桌面 1:1）

```kotlin
// core/model — 对应 zhibo/models.py + plugins/base.py
data class Follower(
    val id: Long,
    val name: String,
    val plugin: String,               // 主 resolver
    val fallbackPlugins: List<String>,
    val platform: String,
    val url: String,
    val quality: String = "best",
    val tags: List<String> = listOf("未分类"),
    val enabled: Boolean = true,
    val extra: JsonObject,            // 含独立 poll_interval
)

data class LiveInfo(
    val isLive: Boolean,
    val anchorName: String = "",
    val title: String = "",
    val streamUrl: String = "",
    val m3u8Url: String = "",
    val flvUrl: String = "",
    val qualityName: String = "",
    val candidates: List<String>,     // = stream_candidate_urls()
    val extra: JsonObject,
)

interface LiveResolver {
    val name: String
    suspend fun checkLive(url: String): LiveInfo
    suspend fun getStreamUrl(url: String, quality: String): String
    suspend fun listQualities(url: String): List<String>   // 供表格内点改画质
}
```

## 6. 平台 Resolver 映射（迁移的核心工作）

桌面插件 → 安卓方案的逐项对照：

| 桌面（Python） | 平台 | 安卓方案 | 难度 |
| --- | --- | --- | --- |
| streamget.BilibiliLiveStream + bilibili_quality | B 站 | Kotlin 直译：`get_info_by_room` 开播检测（免登录）+ playUrl API 取流 + qn 档位表；高清档读 WebView 采集的 SESSDATA | 低 |
| streamget.DouyuLiveStream | 斗鱼 | Kotlin 直译：房间 API + 签名算法（did/tt/sign）移植；FLV 直放 | 中 |
| streamget.HuyaLiveStream | 虎牙 | Kotlin 直译：移动端 API + payload | 中 |
| streamget.DouyinLiveStream | 抖音 | Kotlin 直译，但 ttwid/验签风控强，保留"浏览器嗅探"兜底通道 | 高 |
| streamlink CLI | Twitch 等 | 无 CLI。国内平台本就靠 streamget；Twitch 走 gql API 移植（需代理） | 中 |
| yt-dlp CLI | YouTube | 三选一：① NewPipeExtractor（Java 库，**GPLv3**，会传染整个 App，需开源决策）② 内置浏览器嗅探 ③ Chaquopy 内嵌 Python 跑 yt-dlp（重，包体 +50MB）。**建议起步用 ②，把 ① 做成可选模块隔离** | 高 |
| fs1_plugin.py | FS1 | Kotlin 直译：AES 解密、域名校验（fs/fszb 数字域白名单）、authorization 头；授权获取改走内置浏览器被动抓取（§8.2） | 中 |
| mpv 外部播放器 | 全部 | Media3 ExoPlayer（§9），"更新中心装 mpv/uosc"在安卓上消失 | — |
| yt-dlp 下载 | YouTube | 阶段 M5 可选项，优先级最低 | — |

Resolver 注册表 + 每 Follower 的 `plugin + fallback_plugins` 回退链、平台默认回退表，逻辑照抄
`monitor.py` 的 `_check_with_plugin` / `_platform_fallback_plugins`。

**风控升级（猫鼠游戏）对策**：resolver 失败 → 自动降级到内置浏览器加载房间页嗅探流地址 →
仍失败则标记"检测异常"。这与桌面"红色状态 = 上次在线、当前异常"的语义一致。

## 7. 监控引擎与后台

`MonitorService`（asyncio）→ `MonitorEngine`（纯 Kotlin 协程，可 JVM 单测）：

- 轮询循环：`Semaphore(maxConcurrentChecks)` 并发闸、整轮时限、单项检测时限 —— 直译；
- 平台熔断：`PlatformHealth`（连续失败 N 次进入指数退避、恢复后重新入列）—— 直译；
- 事件流：`statusChanges: Flow<FollowerStatus>` 替代 `on_status_change` 回调，UI 收集渲染。

安卓侧调度分三档：

| 场景 | 机制 | 间隔 |
| --- | --- | --- |
| App 前台 | 进程内协程循环 | 全局 60s（默认，可 5–3600） |
| 后台保活 | **前台服务**（Android 14+ 声明 `specialUse`，侧载分发不受 Play 审核约束；Android 15 对 dataSync 有 6h 限制，避开） | 同上，常驻通知"监控中 · N 位主播" |
| 省电模式（可选档） | WorkManager 周期任务 | ≥15min，只检测不开播放器 |

开播通知：NotificationChannel（全局 + 按平台），点击通知直接拉起播放页。
厂商杀后台：设置页检测（isIgnoringBatteryOptimizations）并引导白名单，不做保保活黑科技。

## 8. 内置浏览器模块（feature/browser）

一个通用 `BrowserScreen`，三种调用姿态（入口不同、内核同一套）。

### 8.1 结构

```text
feature/browser/
├── BrowserScreen.kt        # WebView 容器 + 地址栏 + 抓取状态指示
├── SniffingWebViewClient.kt# shouldInterceptRequest 观察器
├── CredentialHarvester.kt  # CookieManager → Keystore 凭据仓库
├── SnifferRules.kt         # 正则规则：m3u8/flv、/v1/room 授权头、登录完成判定
└── JsHooks.kt              # fetch/XHR 包装注入（请求头抓取的兜底）
```

### 8.2 三个用途

1. **登录采集**：打开 `bilibili.com` / `twitch.tv` 登录页 → 检测到登录态 Cookie →
   `CookieManager.getCookie()`（特权 API，**可读 HttpOnly**，比桌面油猴脚本能力更强）→
   只提取需要的键（如 `SESSDATA`）→ Keystore 加密存储 → 清掉无关 Cookie。
2. **FS1 授权抓取**（替代 Tampermonkey 脚本）：加载飞速直播页，`shouldInterceptRequest`
   观察发往 `/v1/room` 的请求，记录 `authorization` / `api-version` / `imei` / `dun-imei` / UA，
   校验 HTTPS + `fs`/`fszb` 数字域 + 固定 API 主机（沿用桌面白名单逻辑）后入库。
   某些 WebView 版本 `shouldInterceptRequest` 看不全自定义头时，退回 `JsHooks` 注入包装
   `fetch`/`XMLHttpRequest` 抓头 —— 两条腿走路。
3. **流地址嗅探兜底**：resolver 失败时加载房间页，按 SnifferRules 拦 `.m3u8`/`.flv`，
   命中即得到候选流（进 `LiveInfo.candidates`），或直接在 WebView 内嵌播放。

### 8.3 安全模型（对齐桌面既有原则）

- 抓到的凭据**永不**写入关注项/导出 CSV（沿用导入安全校验器：拒绝 Cookie、token、敏感 query）；
- WebView 禁 `file://`、禁第三方 unknown 来源下载、JS 默认关按需开；
- 凭据页展示各平台"凭据状态"卡片：入库时间、近期是否被使用、重新采集按钮；
- 日志对 authorization/cookie 值脱敏。

## 9. 播放（替代 mpv）

- `PlayerManager` 维护最多 2–3 个 `ExoPlayer` 实例（对齐桌面最多 3 路）；UI 上主推
  1 路全屏 + 1 路画中画（PiP），平板/横屏可分屏多 Surface。
- DataSource：自建 `OkHttpDataSource.Factory`，按平台注入代理、UA、Referer；
  FLV 用 `DefaultExtractorsFactory`（含 FlvExtractor，H.264/AAC）→ `ProgressiveMediaSource`，
  m3u8 → `HlsMediaSource`。**注意：FLV 内 HEVC 不支持**，resolver 侧优先选 H.264 档或 HLS。
- 画质：Follower.quality → resolver 档位列表 → 播放页内切换（重建 MediaSource，对齐桌面
  "表格内点改画质"）。
- 后台播放 + 通知栏控制：`MediaSessionService`；`copy_stream` 改为分享/复制流地址；
  `open_web` 走系统浏览器或内置浏览器。

## 10. 数据与互通

- Room `followers` 表字段与 CSV 完全对齐（`extra` 存 JSON 文本），`status_history` 存状态轨迹，
  `platform_health` 存熔断态（重启恢复）。
- DataStore：poll_interval、max_concurrent_checks、failure_backoff_*、notifications_enabled、
  每平台代理 —— 键名与 `settings.csv` 一致。
- **导入/导出**：完整兼容桌面 `followers.csv` / `settings.csv` 格式，实现双端互迁；
  首次启动提供"从 CSV 导入"向导（Android 存储访问框架 SAF 选文件）。

## 11. 实施里程碑（每阶段真机验证后再进下一阶段）

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| M0 | Gradle 骨架 + core:model/database/datastore + CSV 导入导出 | 真机导入桌面 followers.csv 无损往返 |
| M1 | MonitorEngine + B 站/斗鱼/虎牙 resolver + 前台服务 + 开播通知 | 后台 60s 轮询稳定一晚，通知可点击 |
| M2 | Media3 播放 + 画质切换 + PiP + 多实例管理 | FLV/HLS 各平台真机可播可切 |
| M3 | 内置浏览器：B 站 Cookie 采集、FS1 授权抓取、流嗅探兜底 | 免剪贴板完成两类凭据入库并生效 |
| M4 | 抖音 resolver、Twitch（代理）、YouTube（浏览器嗅探起步）、FS1 完整移植 | 对齐桌面平台覆盖面 |
| M5 | 导入向导安全校验、诊断页、编辑差异确认流、CSV 互通打磨、可选下载 | 功能对齐桌面清单 |

## 12. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 平台风控升级导致 resolver 失效（抖音/YouTube 最甚） | resolver 模块化 + 浏览器嗅探兜底；不做热更新（合规与安全） |
| NewPipeExtractor 的 GPLv3 传染 | 默认不用；若要 YouTube 原生解析，单独评估整体开源或继续浏览器方案 |
| FLV HEVC 播不了 | resolver 固定请求 H.264 档/HLS |
| Android 14/15 前台服务限制 | specialUse 类型 + 用户引导电池白名单；侧载分发 |
| 厂商 ROM 杀后台 | 检测 + 引导（小米/华为/OPPO 各有白名单入口），文档化 |
| WebView 请求头抓不全 | shouldInterceptRequest + JS hook 双通道 |
| 双端数据漂移 | CSV 为唯一交换格式，字段 schema 冻结；破坏性变更需双端同版本发布 |
