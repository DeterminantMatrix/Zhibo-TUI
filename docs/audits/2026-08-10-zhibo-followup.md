# ZHIBO 监控故障注入与可靠性回归跟进报告

- 跟进日期：2026-08-10
- 项目目录：D:\WPS SyncDisk\2.Tool\1.VibeCoding\ZHIBO
- 当前分支：codex/initial-zhibo-tui
- 前置报告：docs/audits/2026-08-10-zhibo-audit.md
- 本轮性质：离线故障注入、监控状态机回归和低风险局部修复
- 外部操作边界：未访问真实直播平台、真实账号、真实 Cookie、真实 FS1 请求、真实签名流地址或外部工具更新

## 1. 执行摘要

本轮在第一轮安全和稳定性修复的基础上，新增了离线故障注入测试文件，并对监控调度、平台健康度、插件 fallback、deadline、状态保留、通知和多 CDN 边界进行了回归验证。

测试首先复现了一个真实的局部可靠性缺陷：插件直接抛出共享连接异常时，监控原先仍会继续调用同一房间的 fallback 插件。该行为会在平台已经不可达时重复发起不必要请求，并可能放大平台故障。问题已在 monitor.py 做最小修复，并增加了回归测试。

当前结论：本轮离线测试范围内没有确认的 P0/P1 问题；新增的 fallback 放大问题已修复。真实平台认证、长时间运行、外部工具和同步盘断电语义仍未在本轮验证。

## 2. 当前工作树边界

开始前重新检查了当前 Git 状态和 diff。工作树中存在大量第一轮及用户已有的修改和未跟踪文件，本轮没有使用 git reset、git checkout、批量清理、删除、提交或推送。

本轮实际新增或修改的目标文件为：

- monitor.py
- tests/test_monitor_fault_injection.py
- docs/audits/2026-08-10-zhibo-followup.md

其他既有修改和未跟踪文件均保留。

## 3. 新增故障注入模型和覆盖

新增文件：tests/test_monitor_fault_injection.py

测试使用临时 CSV、fake plugin、可控 asyncio.Event、插件调用记录、手工设置的平台 cooldown 和 fixture 级插件注册隔离；不调用真实网络。

新增 8 个用例：

| 测试位置 | 覆盖内容 |
|---|---|
| tests/test_monitor_fault_injection.py:43 | 单个 B站房间真正进入 timeout/cancel 路径时，其他房间仍继续检测；单房间故障不会留下 deadline task。 |
| tests/test_monitor_fault_injection.py:88 | 平台 cooldown 期间状态为 skipped，不调用插件、不把已确认在线状态改成 offline。 |
| tests/test_monitor_fault_injection.py:126 | 已确认 offline 后出现暂时性检测异常时保留最近确认状态，不触发虚假状态通知。 |
| tests/test_monitor_fault_injection.py:171 | fallback 顺序保持配置顺序，重复插件只调用一次，并正确记录实际使用插件。 |
| tests/test_monitor_fault_injection.py:208 | 插件直接抛出共享连接异常时停止同房间 fallback，避免重复请求。 |
| tests/test_monitor_fault_injection.py:247 | 状态历史相邻重复去重，历史长度保持 20 条上限。 |
| tests/test_monitor_fault_injection.py:272 | get_stream_info() 获取新结果，不复用之前保存的旧流地址。 |
| tests/test_monitor_fault_injection.py:301 | offline 结果中即使携带 CDN 候选，也仍按 offline 判断，候选不参与监控在线/离线判定。 |

既有测试继续覆盖以下场景：

- 两个不同 B站房间连接故障后才触发平台退避：tests/test_monitor_health.py:120；
- 单个 B站房间故障不影响其他房间：tests/test_monitor_health.py:79；
- B站非连接错误会打断连接故障候选序列：tests/test_monitor_health.py:162；
- FS1 认证失败专用长熔断：tests/test_monitor_health.py:205；
- 总轮询 deadline、排队任务 deferred/backoff 和单房间 timeout：tests/test_monitor_health.py:316、tests/test_monitor_health.py:401；
- 已确认在线状态在异常和 timeout 时保留：tests/test_monitor_stream_info.py:200、tests/test_monitor_health.py:366；
- 状态回调异常和慢 UI 回调隔离：tests/test_monitor_stream_info.py:144、tests/test_monitor_health.py:497；
- get_stream_info() deadline、平台 circuit 和任务回收：tests/test_monitor_health.py:542；
- shutdown 后 deadline task 回收：tests/test_monitor_health.py:583；
- bounded worker 的 timeout、取消、子进程树和重复 shutdown 回收：tests/test_bounded_executor.py。

## 4. 已确认问题和修复

### REL-01：共享异常分支仍继续调用同房间 fallback

- 级别：P2
- 状态：已修复
- 生产文件：monitor.py:701-727
- 回归测试：tests/test_monitor_fault_injection.py:208-245

触发链：

1. 主插件在 _check_with_fallbacks() 中直接抛出包含共享连接错误的异常；
2. 原实现只把异常追加到错误列表后无条件 continue；
3. 同一房间继续调用 fallback 插件；
4. 当平台本身不可达时，fallback 不能提供独立信息，却会增加重复请求和资源消耗。

修复：异常被压缩为日志安全文本后，如果 _is_platform_circuit_error() 判定为平台共享连接/认证错误，则立即停止该房间 fallback 链。普通的房间级或插件级错误仍保持原有 fallback 行为。

本修复是局部、可回滚的控制流修改，没有改变平台健康度阈值、用户数据格式、FS1 API 命名空间策略或 PyPI 更新策略。

## 5. 验证命令和实际结果

### 5.1 定向故障注入测试

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_monitor_fault_injection.py
~~~

结果：

~~~text
8 passed in 0.10s
~~~

第一次执行时隔离目录创建命令使用了错误的 PowerShell 参数，导致 pytest fixture 在创建 tmp_path 前失败；修正隔离目录创建后重新执行，8 个用例全部进入代码并通过。该第一次失败属于测试环境命令错误，不属于项目代码失败。

### 5.2 监控核心组合回归

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_monitor_fault_injection.py tests/test_monitor_health.py tests/test_monitor_loop.py tests/test_monitor_stream_info.py tests/test_bounded_executor.py
~~~

结果：

~~~text
44 passed in 4.12s
~~~

### 5.3 全量测试

使用项目虚拟环境，并把 TEMP、TMP、ZHIBO_DATA_DIR、ZHIBO_LOG_FILE 和 pytest 临时目录指向非 WPS 同步盘的隔离可写目录：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
~~~

结果：

~~~text
344 passed, 1 skipped, 5 deselected in 8.46s
~~~

本轮新增 8 个测试后，第一轮的 336 passed, 1 skipped, 5 deselected 基线保持通过。

### 5.4 其他门禁

Python 全文件语法检查使用项目虚拟环境执行，结果通过、无输出：

~~~powershell
$zhiboPyFiles = rg --files -g '*.py' -g '!.venv/**' -g '!__pycache__/**' -g '!logs/**'; .\.venv\Scripts\python.exe -m py_compile $zhiboPyFiles
~~~

~~~powershell
git diff --check
~~~

结果通过。Git 仅报告既有 LF/CRLF 转换提醒，没有 whitespace error。

本轮没有运行：

- pytest -m integration；
- 真实直播平台请求；
- 真实 FS1 请求；
- 真实 Cookie 导入；
- 外部工具下载或安装；
- pip 更新；
- MPV、FFmpeg、uosc、7z 或 py7zr 的真实外部更新。

## 6. 敏感信息和私有运行数据检查

- 生产代码、文档和报告的高风险模式扫描没有发现完整 Cookie、Authorization/Bearer 值、userinfo URL、代理认证信息或带实际长值的 token/signature URL。
- 对测试目录的宽松扫描命中了原有脱敏测试中的合成占位字符串；这些值位于测试 fixture，服务于验证日志、UI 和 URL 脱敏，不是真实账号或运行数据。本轮新增测试没有写入 Cookie、token、Authorization、Bearer、signature 或代理认证信息。
- git ls-files 对 cookies.txt、followers.csv、settings.csv、sports/rooms.yaml、logs/** 和锁文件的结果为 NO_TRACKED_SENSITIVE_PATHS。
- 本轮没有读取、输出或修改这些私有运行时文件的内容；测试配置使用临时 CSV，日志和数据目录使用隔离临时目录。
- 没有真实网络访问。所有新增场景均由 fake plugin、可控 coroutine、临时配置和本地状态对象模拟。

## 7. 风险状态

### P0/P1

本轮离线验证范围内没有确认的 P0/P1 问题。

### P2

仍保留第一轮报告中的边界：

1. PyPI 及外部 Python 工具更新目前没有项目级 hash lock、签名验证和固定镜像策略；
2. FS1 播放 API 仍是命名空间 allow-list，而不是正式固定 host 清单；
3. 真实 FS1 认证、证书、播放 API 和服务端行为未验证；
4. 真实 Windows 长时间运行中的第三方库、句柄、进程、内存和连接行为未验证；
5. 同步盘断电、强制终止和双进程编辑语义未验证。

本轮新增的共享异常 fallback 问题已修复，不再作为未解决 P2 记录。

### P3

可继续评估但本轮未自动实施：按插件复用 HTTP client、超大关注列表窗口化调度、请求/回退/超时聚合指标和不缓存签名流地址的播放操作去重。

## 8. 仍需用户决策或真实环境验证的项目

1. 是否为 PyPI/外部工具更新确定 hash lock、版本范围和镜像 allow-list 策略；
2. 是否能提供 FS1 播放 API 的正式固定 host 清单，以便替换命名空间 allow-list；
3. 是否安排真实平台和长时间运行验证，包括真实 FS1、MPV/FFmpeg/uosc、Windows 进程/句柄/内存，以及同步盘恢复语义。

在这些项目获得明确策略或安全运行窗口前，本轮不擅自扩大安全边界。

