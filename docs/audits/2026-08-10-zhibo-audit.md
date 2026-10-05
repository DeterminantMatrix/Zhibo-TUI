# ZHIBO 直播监控项目安全、稳定性、架构与资源审计报告

- 审计日期：2026-08-10
- 审计目录：D:\WPS SyncDisk\2.Tool\1.VibeCoding\ZHIBO
- 当前分支：codex/initial-zhibo-tui
- 审计性质：本地代码、配置处理、单元/模拟测试和静态扫描
- 外部操作边界：未使用真实直播平台、真实账号、真实 Cookie、真实签名流地址、真实 FS1 请求或外部更新安装

## 1. 执行摘要

本轮完成了 ZHIBO 的入口、三套 UI、监控调度、多平台插件、播放链路、配置持久化、日志脱敏、外部工具更新和测试体系审计。已对能够在本地安全确认且属于低风险局部修复的问题完成代码修复和回归测试。

结论如下：

- 未发现当前工作树中被 Git 跟踪的 Cookie、设置 CSV、FS1 房间 YAML、日志目录或锁文件。
- 未发现 Python 内置 eval(、exec(、yaml.load(、shell=True 或 os.system(。词法扫描中的 app.exec() 和 asyncio.create_subprocess_exec() 属于 Qt/asyncio API，不是 Python 内置 exec 或 shell 调用。外部命令调用使用参数数组；pickle 仅用于受控的模块级 worker 参数序列化，不能把任意用户文本直接当作代码执行。
- 已修复 FS1 普通授权 API 可由本地配置改写到任意 HTTPS 主机、配置可关闭 TLS 证书验证的问题；普通 API 现在使用固定可信主机、固定路径和强制 TLS 验证。
- 已修复 Qt Desktop/Qt Quick 共享快照把旧配置中的签名流 URL 原样传入 UI、浏览器和下载入口的问题。
- 已修复外部 MPV 更新资产缺少 SHA-256 时仍可形成安装计划的问题，并为 ZIP、7z 和 py7zr 解压增加成员路径、链接和越界检查。
- 已修复平台退避抖动造成的浮点上限越界；同时保留 FS1 认证失败所需的专用 1800 秒熔断下限。
- 已为配置 CSV 原子写入补充 flush 和 fsync，降低断电或系统崩溃造成临时文件未落盘的概率。
- 隔离测试环境下完整测试为 336 passed, 1 skipped, 5 deselected；全部 Python 文件语法检查通过；git diff --check 通过。
- 未运行 pytest -m integration，因为该标记要求真实外部直播平台，不属于本轮安全范围。

当前没有需要阻塞发布的已确认 P0/P1 代码问题。仍有两类需要后续决策或真实环境验证的风险：PyPI 依赖更新目前没有项目级 hash 锁定；FS1 播放 API 仍采用命名空间 allow-list 而不是固定主机清单。这两项已在第 8 节单列，未在本轮擅自扩大改动范围。

## 2. 当前架构与边界

### 2.1 组件关系

~~~mermaid
flowchart TD
    A["Textual main.py"] --> B["ui.app.ZhiboApp"]
    C["Qt Desktop"] --> D["qt_desktop.worker.MonitorThread"]
    E["Qt Quick"] --> F["qt_quick.worker.QuickMonitorThread"]
    B --> G["MonitorService"]
    D --> G
    F --> G
    G --> H["Scheduler / poll_all"]
    H --> I["room deadline + global deadline"]
    H --> J["global semaphore C"]
    H --> K["platform semaphore 1"]
    H --> L["platform health / backoff"]
    I --> M["check_one"]
    M --> N["plugin fallback chain"]
    N --> O["streamlink"]
    N --> P["streamget"]
    N --> Q["yt-dlp"]
    N --> R["FS1"]
    O --> S["B站轻量状态探测"]
    O --> T["bounded worker process"]
    Q --> T
    G --> U["ConfigManager / CSV / settings"]
    G --> V["redacted logs and UI snapshots"]
    G --> W["get_stream_info"]
    W --> X["playback-stage CDN candidates"]
    X --> Y["mpv"]
    Z["Update / Tool Runtime"] --> AA["MPV / FFmpeg / uosc"]
~~~

三套 UI 共用同一个 MonitorService 领域和调度核心：

- Textual：main.py -> ui/app.py -> MonitorService。
- Qt Widgets：qt_desktop/main.py -> qt_desktop/worker.py -> MonitorService。
- Qt Quick：qt_quick/main.py -> qt_quick/worker.py -> MonitorService。

这使得房间状态、平台熔断、超时、回退和配置保存逻辑基本集中，UI 只负责事件转发、快照和显示。Qt Desktop/Qt Quick 的监控线程在退出时会取消事件循环、调用 MonitorService.shutdown()，再回收插件 worker。

### 2.2 监控流程

~~~mermaid
flowchart LR
    A["配置关注项"] --> B["poll_all"]
    B --> C["按标签筛选"]
    C --> D["全局并发 semaphore"]
    D --> E["平台 semaphore=1"]
    E --> F{"平台处于 cooldown?"}
    F -- "是" --> G["skipped/backoff"]
    F -- "否" --> H["单房间 deadline"]
    H --> I["check_one"]
    I --> J["_check_with_fallbacks"]
    J --> K{"明确在线/离线?"}
    K -- "是" --> L["更新确认状态和历史"]
    K -- "否" --> M["保留上次确认状态，记录 error"]
    M --> N["房间失败计数"]
    N --> O["必要时平台 health/backoff"]
~~~

已确认的保护包括：

- 单项 deadline 和整轮 deadline 使用单调时钟；排队项目在整轮预算耗尽时标记为暂缓，不伪装成网络失败。
- 任务 token 防止过期任务覆盖新结果；被取消的任务会进入跟踪集合并在关闭时继续回收。
- check_one() 只把无错误、符合结果约束的 LiveInfo 视为确认结果；异常不直接覆盖最近一次已确认的在线/离线状态。
- 状态区分 unknown、online、offline、error、skipped、backoff、disabled。
- B站先走轻量 no_playurl=1 状态探测；离线房间不会继续支付 CDN/Streamlink 解析成本。
- B站单房间连接故障只记录房间来源；至少两个不同来源连续失败后才进入平台级退避。
- 多 CDN 候选只在播放阶段消费，不把某一 CDN 不可达直接等同于主播离线。

### 2.3 插件回退流程

~~~mermaid
flowchart TD
    A["主插件"] --> B{"结果有错误或在线但无流?"}
    B -- "否" --> C["返回结果"]
    B -- "是" --> D["去重后的 fallback 插件"]
    D --> E{"平台共享错误?"}
    E -- "是" --> F["停止同一房间继续回退"]
    E -- "否" --> G["继续下一插件"]
    G --> H{"仍无确认结果?"}
    H -- "是" --> I["error/check_error"]
    H -- "否" --> C
~~~

B站的 CDN/播放 URL 解析失败不会被当成“平台已离线”；平台健康度只在连接、超时、认证等可共享错误达到规则后变化。

### 2.4 播放流程

~~~mermaid
flowchart LR
    A["用户播放"] --> B["MonitorService.get_stream_info"]
    B --> C["平台 cooldown / 检测中的检查"]
    C --> D["完整插件检测和回退"]
    D --> E["LiveInfo + stream_candidates"]
    E --> F["去重的候选 URL"]
    F --> G["mpv 启动"]
    G --> H{"进程立即失败?"}
    H -- "是" --> I["播放下一 CDN 候选"]
    H -- "否" --> J["保持当前播放器"]
~~~

播放阶段的多 CDN 回退位于 plugins/base.py、Textual ui/app.py 和 Qt Quick controller；它不参与监控在线状态判断。

### 2.5 入口、依赖和 README 差异

- 入口点包括 main.py 的 Textual 主程序、qt_desktop/main.py 的 Qt Widgets 原型和 qt_quick/main.py 的 Qt Quick 程序；插件通过 plugins/__init__.py 注册，配置由 config.py 的 ConfigManager 负责。
- requirements.txt 对 Textual、PyYAML、httpx、PyCryptodome、packaging、streamget、streamlink 和 yt-dlp 使用固定版本；requirements-dev.txt 复用运行时依赖并增加 pytest、pytest-asyncio。pytest.ini 默认排除 integration 标记，避免普通回归测试访问真实外部平台。
- README 与实际代码总体一致：三套 UI、平台插件、配置预览、代理、Cookie 更新、播放、下载、uosc 和私有数据目录均有对应实现。
- README 中“Qt Quick/QML 版本与原 Textual 版本及 Widgets 原型相互独立”应理解为入口和 UI 生命周期相互独立；三者实际上共享 MonitorService、插件注册表和配置层，这一点以代码为准。
- README 尚未细化本轮新增的 FS1 普通 API 固定 host/path allow-list、MPV digest 缺失时禁止更新、7z 成员检查和 pip 更新缺少 hash lock 的限制；这些已在本报告的安全和未确认风险章节补充。

## 3. 已确认问题与修复

严重级别按本项目本地桌面应用语境定义：P0 为可直接造成大范围或立即失控的严重问题；P1 为可能导致凭据泄露、任意代码/文件影响或高概率数据损坏的问题；P2 为需要特定配置、影响可靠性或供应链边界的问题；P3 为防御性增强、可维护性或资源优化项。

### 3.1 安全问题

| 编号 | 级别 | 状态 | 文件/位置 | 触发条件与执行链 | 影响与处理 |
|---|---|---|---|---|---|
| SEC-01 | P1 | 已修复 | plugins/fs1_plugin.py:123-146, 429-490, 653-662 | 本地 FS1 YAML 或导入内容把普通 api_url 改成任意 HTTPS 主机，随后 Fs1Plugin.check_live() 用带授权头的 httpx.AsyncClient 请求；旧配置还可能要求关闭 TLS 验证。 | 授权头可能被发送到非预期主机，或被中间人读取。普通 API 现在必须匹配固定可信主机、固定 /v1/room 路径、无 query/fragment；运行时不可信地址回退到默认端点，TLS 强制为 True。测试覆盖 query、path、任意主机、运行时降级和 TLS 设置。未做真实 FS1 请求。 |
| SEC-02 | P1 | 已修复 | qt_desktop/viewmodel.py:10-36 | 旧 followers.csv 中残留签名 URL 时，快照曾把原值传给 Qt Widgets/QML；随后可能进入浏览器打开、下载对话框或 UI 复制路径。 | 签名 URL 在有效期内可能被本地屏幕、日志导出或剪贴板使用。快照现在对 URL 使用 redact_url()；回归测试确认 query token/signature 等不会出现在快照。代价是旧签名 URL 不再被 UI 直接打开，需要重新获取流。 |
| SEC-03 | P1 | 已修复 | tool_runtime.py:155-156, 237-254, 324-451 | 外部 MPV 资产没有 digest 时，旧逻辑仍可能生成可安装计划；压缩包成员还需要防范父目录、绝对路径和链接。 | 外部工具安装属于高权限供应链边界，完整性不足可能导致恶意文件落地。更新计划现在要求 sha256:<64 hex>；下载前失败；ZIP、7z、py7zr 均在解压前验证成员名，解压后验证越界路径和符号链接。没有下载或执行真实外部资产；真实 7z/py7zr CLI 行为列入待验证项。 |
| SEC-04 | P2 | 已修复 | config.py:1026-1038 | 配置内容写入临时文件并替换目标前没有显式 flush/fsync。 | 断电或系统崩溃时存在临时文件内容尚未落盘的耐久性窗口。现在在 os.replace() 前 flush 并 fsync；相关测试通过 monkeypatch 验证 fsync 调用。Windows 目录项持久化和同步盘断电语义未做真实验证。 |
| SEC-05 | P2 | 已修复 | monitor.py:56-76, 429-475, 487-495 | 平台退避加入随机抖动后，浮点结果可能出现 900.0000000000291 一类的上限越界；健康恢复也需要清理专用 cap。 | 可能导致边界测试失败、UI 倒计时和策略上限不一致。新增 retry_after_cap_seconds，通用上限严格为 900 秒，FS1 认证/授权故障继续保持 1800 秒专用熔断，健康恢复重置 cap。 |

### 3.2 已确认但未自动改变的供应链边界

| 编号 | 级别 | 状态 | 文件/位置 | 事实 | 后续建议 |
|---|---|---|---|---|---|
| SEC-06 | P2 | 保留，需设计决策 | qt_quick/worker.py:1002-1021、Textual 同类更新路径 | Streamlink、Streamget、yt-dlp 以及 py7zr 的更新通过 sys.executable -m pip install --upgrade <allowlisted package> 执行。参数使用数组，当前没有 shell 注入，但没有项目级 hash lock、签名验证或内部镜像固定。 | 后续增加受控 requirements lock、hash 校验、允许版本范围和镜像策略；在策略确定前不自动安装或改写更新流程。本轮未运行 pip 更新。 |
| SEC-07 | P2 | 部分缓解，保留 | plugins/fs1_plugin.py:136-146, 365-397 | FS1 播放 API 不是固定单主机，而是匹配 openim-php-api.<namespace>.cc 的 HTTPS 路径；请求会继承带授权头的客户端。普通 /v1/room API 已固定，但播放端仍依赖命名空间信任。 | 代码层面已拒绝其他主机、端口、query、fragment 和非 HTTPS 地址；但命名空间的长期所有权、DNS、证书链和服务端行为未做外部验证。建议取得正式主机清单后改为固定 allow-list，或使用签名配置/证书 pinning。 |

## 4. 稳定性审计

### 4.1 已确认的稳定性保护

| 区域 | 当前结论 | 证据 |
|---|---|---|
| 单房间超时 | 已隔离。插件检测、完整播放信息和整轮调度均有 deadline；阻塞的 Streamlink/yt-dlp 工作在可终止 worker 中。 | monitor.py:855-980, 1060-1125；plugins/bounded_executor.py |
| 轮询重叠 | 已隔离。poll_all() 受 _poll_lock 保护；调度器、手动刷新和 UI 退出路径共享取消状态。 | monitor.py:982-1054, 1132-1163 |
| 旧在线状态 | 已保留但标记检查异常，不产生虚假的上下线通知。 | monitor.py:532-537, 641-654 |
| B站离线判断 | 已分离轻量状态探测与 CDN 解析；离线房间不进入 Streamlink 解析。 | plugins/bilibili_quality.py:91-145；plugins/streamlink_plugin.py:132-197 |
| 平台故障范围 | 已区分房间来源和平台共享故障；单个 B站房间错误不会立即暂停全部 B站房间。 | monitor.py:441-485；tests/test_monitor_health.py:79-203 |
| 错误状态 | 已区分 error、skipped、backoff、disabled 与确认的 online/offline。 | monitor.py:45-52, 532-570, 656-677 |
| 通知去重 | 只在确认状态变化时通知；历史事件有相邻重复去重和 20 条上限。 | monitor.py:540-563, 798-824 |
| 进程退出 | Streamlink/yt-dlp worker 可取消、终止、回收；UI 退出会继续调用插件 worker shutdown。 | plugins/bounded_executor.py；ui/app.py:2437-2670；qt_desktop/worker.py；qt_quick/worker.py |
| 配置一致性 | 编辑 API 使用字段 allow-list、严格校验、侧边锁、文件 revision 检查、原子替换和 fsync。 | config.py:417-659, 1026-1120, 1220-1330 |

### 4.2 稳定性剩余观察项

- 非受控第三方库自身的阻塞、子进程或内部重试行为无法仅凭本地单元测试完全证明；当前外层 deadline 和可终止 worker 能够限制主要影响，但需要真实平台长时间运行测试确认。
- streamget 依赖库的内部网络实现不完全由本项目控制，平台级请求次数和连接复用应在真实运行中采样。
- get_stream_info() 在播放前再次完整检测，这是安全上合理的“不要播放旧流”选择，但在用户频繁点击播放时会重复网络请求；可以后续增加短 TTL 的确认结果缓存，缓存必须绑定房间、质量和凭据版本，并禁止直接复用过期签名 URL。

## 5. 资源与性能估算

### 5.1 轮询请求量

设关注房间数为 N，轮询间隔为 T 秒，最大并发为 C：

~~~text
每分钟逻辑房间检测次数 ≈ 60N / T
实际网络请求数 = 逻辑检测次数 × 每个插件的请求数 + 回退额外请求
~~~

不能把 60N/T 直接当成 HTTP 请求数：

- 一般插件一次逻辑检查可能包含解析器的多次 HTTP 请求。
- B站 best 质量首先有 1 次轻量直播状态请求；在线后通常还会有播放信息请求和房间元数据请求。存在 Cookie 且首次请求被认证拒绝时，播放信息可能再做一次未登录尝试，因此 B站在线检查大致为 2–4 个 HTTP 请求，具体取决于插件、Cookie 和服务端响应。
- Streamlink 主插件若在线且缺少标题/主播名，可能增加一次 Streamget 元数据回退。
- Streamlink/Streamget 的 B站质量增强会再次请求播放信息并保存多个 CDN 候选。
- 插件回退只有主插件返回错误、在线但无流或不可用结果时才发生；平台共享错误会提前停止同一房间的继续回退。

示例：N=100, T=60 时，逻辑检查约为 100 次/分钟；实际 HTTP 数应按平台分布和回退率采样，不能用固定倍数替代测量。

### 5.2 并发、任务和进程

- 配置默认轮询间隔为 60 秒，允许范围为 5–3600 秒。
- max_concurrent_checks 默认 8，允许范围为 1–16；全局 semaphore 限制实际进入插件检查的并发。
- 每个平台在一轮中使用 Semaphore(1)，避免同一平台突发并发。
- Streamlink 的进程隔离池上限为 8；yt-dlp 隔离池上限为 4；超出容量会返回“检测繁忙”，不会无限排队。
- poll_all() 会为选中的关注项创建协程任务，但等待和真正插件执行受上述 semaphore/deadline 限制；极大规模关注列表仍会有 O(N) 的任务对象和快照开销。
- 状态历史每房间最多 20 条；Textual 日志面板最多 500 行；Qt Quick 日志最多 800 行；Qt Desktop QPlainTextEdit 最多 1200 个 block；文件日志约 1 MB × 5 轮转。
- UI 线程/事件循环外的监控线程和桌面托盘线程均有退出 join 超时；这避免正常退出无限等待，但若第三方库无视终止仍可能遗留进程，需要真实 Windows 长时间运行验证。

### 5.3 低风险资源优化建议

1. 复用按平台或按插件的 httpx.AsyncClient，在每轮结束时关闭；当前 B站状态、元数据、播放请求以及 FS1 请求多处按调用创建客户端，连接池复用不足。风险是客户端生命周期和代理/Cookie 变更需要重新设计，建议作为 P3 分阶段实现。
2. 对超大关注列表采用窗口化调度，减少一次性创建 O(N) 任务；保持当前全局 semaphore 和每平台 semaphore 语义不变。
3. 为每个插件记录请求次数、回退次数、超时次数和平均耗时，只保留聚合计数，避免把 URL 或响应体写入日志。
4. 对播放按钮增加短时间的操作去重；不缓存签名 URL，或只缓存元数据并在真正播放时重新取流。

## 6. 日志、隐私和 Git 检查

### 6.1 保护边界

- app_logging.py:18-236 对 URL userinfo、敏感 query、Authorization/Bearer、Cookie、token、signature、密码和设备标识递归脱敏。
- 文件 logger 使用私有数据目录、RotatingFileHandler(maxBytes=1_000_000, backupCount=5)；UI 日志在进入控件前再次脱敏。
- Cookie 更新限制格式、大小和记录数量，使用锁和原子写入；异常不会把粘贴内容回显给 UI。
- config.py 的编辑预览默认返回脱敏副本；extra 禁止凭据、Cookie、认证头、设备标识和签名文本。
- Qt 快照现在对关注项 URL 使用 redact_url()，避免旧数据绕过普通日志脱敏进入 Qt/QML。

### 6.2 扫描结果

- git ls-files 对 cookies.txt、followers.csv、settings.csv、sports/rooms.yaml、logs/ 和锁文件的路径扫描结果为 none。
- 运行时敏感文件未读取其内容；扫描只排除这些私有运行文件，并对代码、测试、文档和配置模板做路径/行号级规则扫描。
- 代码/测试中的敏感字段匹配主要是脱敏规则、解析器字段名和测试占位值；未发现需要写入报告的真实 Cookie、令牌、签名流 URL 或代理认证信息。
- git diff --check 通过；Git 只报告工作树 LF/CRLF 转换提醒，没有 whitespace error。

## 7. 架构评价与分阶段改进

当前架构已经具备清晰的功能边界，但还没有完全形式化成独立 Domain、Scheduler、Persistence 等包。考虑到工作树已有大量用户修改，本轮没有进行大规模重写。

| 边界 | 当前状态 | 建议 |
|---|---|---|
| Domain/Core | models.py、LiveInfo、FollowerStatus 和 PlatformHealth 已承担核心模型 | 后续把状态转移和事件命名集中到小型纯函数模块，先不迁移现有数据结构 |
| Scheduler | MonitorService 已集中 deadline、取消、退避和并发 | 保持现状；增加指标和故障注入测试即可 |
| Platform Adapters | 插件接口统一，平台差异封装在插件内 | 对插件错误类型做结构化分类，减少字符串匹配，但需兼容旧插件 |
| Playback | get_stream_info 与 stream_candidate_urls 已独立于监控状态；mpv 由 UI/desktop 适配 | 后续增加候选失败原因和播放器生命周期指标 |
| Persistence | CSV/YAML、锁、原子替换、revision 检查已有明确实现 | 为配置写入增加可选备份和恢复预览；不要直接改用户数据格式 |
| Secrets | Cookie 私有路径、输入校验和多层脱敏已存在 | 将凭据版本/来源抽象出来，便于凭据更新后失效缓存 |
| Observability | 有文件日志、UI 日志、状态历史和平台健康度 | 增加脱敏计数指标和诊断导出，不保存原始 URL/响应体 |
| UI Adapters | Textual、Qt Widgets、Qt Quick 均调用同一监控核心 | 继续共享 DTO/快照 schema，避免三套 UI 各自复制业务判断 |
| Update/Tool Runtime | 外部工具下载、校验、解压已有独立模块 | 对 pip 更新补充 hash/镜像策略；保持更新为显式用户操作 |

## 8. 未确认但值得验证的问题

以下项目不是本轮已确认的可利用漏洞，或需要真实外部环境才能给出结论：

1. **真实 7z/py7zr 行为。** 当前测试覆盖了成员枚举和恶意路径拒绝的模拟分支；没有使用外部发布资产执行真实 7z/py7zr 解压。建议在离线、可信、人工生成的测试归档中验证 Windows 7-Zip CLI 的 -slt 输出格式、符号链接、硬链接和 Unicode 路径行为。
2. **更新源的真实供应链。** GitHub/PyPI 的证书链、重定向、Release asset 实际 digest、PyPI 包内容和依赖树未在本轮联网验证。MPV/FFmpeg/uosc 的二进制更新已增加 digest 门槛，但 pip 更新仍需要 hash lock 或可信内部镜像策略。
3. **FS1 播放 API 命名空间。** 正常 API 已固定，播放端仍依赖命名空间 allow-list。需要运营侧确认该命名空间的注册、DNS、证书、轮换和服务端授权边界，再决定固定 host 或 pinning 方案。
4. **extra 的未来配置注入风险。** monitor.py:341-347 会把已校验的 Follower.extra 传入插件接口；plugins/yt_dlp_plugin.py:110-112 的通用 _base_ytdlp_opts(extra) 支持合并内部选项。目前调用链没有把用户 extra 直接传给这个函数，且编辑 API 已禁止认证头等字段；仍建议未来把“用户扩展字段”和“内部 yt-dlp options”改成不同类型，避免后续功能扩展时误合并。
5. **自定义 URL/代理的本地 SSRF 边界。** 关注项编辑允许普通 HTTP(S) 主机，代理测试允许用户输入主机和端口，随后由本机主动建立连接。这在单用户桌面工具中属于用户明确配置的网络测试，不是远程内容自动触发的 SSRF；若未来支持共享配置、远程导入或自动同步，应增加平台 host allow-list、私网地址策略和 DNS rebinding 防护。
6. **同步盘断电恢复。** fsync 已补强文件内容落盘，但 Windows 目录项和同步盘客户端的实际持久化顺序需要断电/强制终止测试。建议在副本目录做故障注入，不要对当前用户数据目录操作。

## 9. 功能建议排序

排序综合收益、风险、实现复杂度、资源成本和测试成本：

| 优先级 | 功能 | 收益 | 风险/成本 | 建议 |
|---|---|---|---|---|
| 1 | 自动脱敏诊断包 | 便于提交问题，能带出平台健康、最近状态、版本和配置 schema | 需要严格字段 allow-list，不能带 Cookie/签名 URL | 优先实现；默认只导出聚合和脱敏文本 |
| 2 | 离线模拟器与故障注入 | 可以测试超时、回退、平台退避、重复通知和退出回收，不依赖真实平台 | 需要维护 fake plugin/clock | 优先实现；对当前调度核心收益最大 |
| 3 | 长时间运行资源统计 | 发现任务、进程、连接、日志和内存退化 | 需要 Windows 进程/句柄采样适配 | 以聚合计数开始，不记录敏感网络内容 |
| 4 | 通知去重、恢复通知和安静时段 | 降低噪音，适合长期运行 | 需要定义状态转移和时间边界 | 在现有 was_live 通知逻辑上增量实现 |
| 5 | 单房间暂停、优先级和监控计划 | 降低请求量，适应不同房间重要性 | UI 和配置字段增加 | 先做内存态暂停，再设计持久化字段 |
| 6 | 配置备份、恢复和迁移预览 | 降低编辑错误和升级风险 | 需要保护备份中的敏感数据 | 使用加密/私有目录和脱敏预览，不能把原始 Cookie 放入报告 |
| 7 | HTTP client 连接池复用 | 降低握手、CPU 和短连接开销 | 生命周期、代理和 Cookie 变更复杂 | 作为性能优化，先在单插件试点 |
| 8 | 更新版本与 hash lock | 显著降低供应链风险 | 需要维护 lock、镜像和升级流程 | 安全价值高，但需要明确发布策略后实施 |
| 9 | 三套 UI 的统一事件/DTO 包 | 减少快照字段漂移 | 迁移范围大 | 只在后续功能迭代中逐步抽取，不做一次性重写 |

当前已完成的多 CDN 播放回退不建议再次扩展到监控判断；其正确边界是“播放阶段可以换候选，监控阶段不能用 CDN 可达性替代在线状态”。

## 10. 本轮修改与回归测试

### 10.1 代码和测试修改

本轮审计直接涉及的核心修改：

- monitor.py：平台退避 cap 和 FS1 专用认证熔断边界。
- plugins/fs1_plugin.py：普通授权 API host/path allow-list、TLS 强制验证、更新字段 allow-list 和运行时回退。
- qt_desktop/viewmodel.py：快照 URL 脱敏。
- tool_runtime.py：SHA-256 门槛、MPV digest 缺失时的 unknown 计划、ZIP/7z/py7zr 成员检查和解压后树检查。
- config.py：原子写入 fsync。
- tests/test_monitor_health.py：平台退避和 FS1 认证熔断回归覆盖。
- tests/test_fs1_config_update.py：FS1 endpoint、TLS、配置白名单和拒绝不可信 endpoint 覆盖。
- tests/test_qt_desktop.py：旧签名 URL 不进入快照覆盖。
- tests/test_tool_runtime.py：digest 缺失、下载前拒绝和 7z 路径穿越覆盖。
- tests/test_config_editing.py：fsync 调用覆盖。

工作树中还存在大量用户原有修改和未跟踪文件；本轮没有 reset、checkout、提交、推送或批量清理。

### 10.2 实际验证命令和结果

在隔离的可写临时目录、TEMP/TMP、ZHIBO_DATA_DIR 和 ZHIBO_LOG_FILE 下执行：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
~~~

结果：

~~~text
336 passed, 1 skipped, 5 deselected in 8.87s
~~~

局部修复相关测试结果：

~~~text
97 passed
~~~

Python 语法检查：

~~~powershell
.\.venv\Scripts\python.exe -m py_compile <全部 Python 文件>
~~~

结果：通过，无输出。

Diff 检查：

~~~powershell
git diff --check
~~~

结果：通过。只有 Git 关于 LF/CRLF 转换的提醒，没有 whitespace error。

集成测试：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -m integration
~~~

未执行。原因是该标记需要真实外部直播平台；执行会越过本轮明确的安全边界。

环境对照：使用默认系统临时目录和默认日志路径时曾出现 230 passed, 23 failed, 77 errors, 5 deselected；失败集中在系统临时目录/日志路径权限和本机残缺 uosc 目录污染。最终门禁第一次使用同步盘工作区临时目录时，只有 uosc 目录原子替换用例因 Windows WinError 5 失败；同一用例改用非同步盘的本线程可写临时目录后为 1 passed，全量测试随后为 336 passed, 1 skipped, 5 deselected。该结果按环境/同步盘目录替换限制记录，不作为当前代码回归失败。

## 11. 未解决事项与后续行动

### 必须在真实环境验证后再宣称完成的事项

- 真实 FS1 API 的证书、授权、普通 API 和播放 API 行为；不得把测试替代真实认证结论。
- 真实 MPV/FFmpeg/uosc 发行资产的 digest、下载重定向、解压和启动。
- Windows 下长时间运行 24 小时级别的任务、子进程、句柄、日志和内存采样。
- 同步盘环境中的强制终止、断电恢复和双进程编辑场景。

### 建议下一轮实施的事项

1. 先建立离线 fake-plugin/fault-injection 测试矩阵。
2. 再实现脱敏诊断包和资源计数。
3. 明确 PyPI/外部工具更新政策后，引入 hash lock、版本范围和镜像 allow-list。
4. 收集 FS1 播放 API 的正式固定 host 清单；若能确认稳定，替换命名空间正则。
5. 以一个插件为试点复用 HTTP client，并用请求数/耗时回归证明收益。

本报告不包含 Cookie、令牌、签名流 URL、代理认证信息或私有运行文件内容。
