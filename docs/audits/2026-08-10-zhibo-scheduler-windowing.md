# ZHIBO 监控调度窗口化与有界资源验证报告

- 报告日期：2026-08-10
- 工作区：`D:\WPS SyncDisk\2.Tool\1.VibeCoding\ZHIBO`
- 分支：`codex/initial-zhibo-tui`
- 本轮性质：完全离线的窗口化调度实现、回归测试、规模基准和静态门禁
- 外部边界：未访问真实直播平台、未发送真实 FS1 请求、未读取真实 Cookie、未使用真实账号、未调用真实 MPV/FFmpeg/uosc/7z/py7zr 更新流程

## 1. 执行摘要

本轮针对上一阶段观测到的 `MonitorService.poll_all()` 外层 O(N) 协调任务问题，完成了局部窗口化调度，并保持现有监控行为和错误隔离语义。

当前 `poll_all()` 的调度方式是：

- 以 `max(1, int(self.max_concurrent_checks))` 作为窗口大小；
- 只为当前窗口创建 `_run_check_with_deadline()` 协调对象；
- 当前窗口通过 `asyncio.gather()` 完成并回落后，才创建下一窗口；
- 所有窗口复用同一个整轮 poll deadline、全局 semaphore、平台 semaphore、平台 cooldown 状态和 `failed_recovery_probes`；
- 结果按原房间顺序写回；
- 取消和 shutdown 路径继续使用现有 deadline task、active attempt token 和任务回收机制。

本轮最终验证结果：

- 窗口化定向测试：`10 passed in 1.81s`；
- 核心回归组合：`65 passed in 6.67s`；
- 完整测试：`365 passed, 1 skipped, 5 deselected in 10.69s`；
- Python AST 检查：最终覆盖 73 个 Python 文件；
- `git diff --check`：通过；
- 直接涉及文件尾随空白检查：通过；
- Git 跟踪敏感路径扫描：无命中；
- 离线规模基准：100/500/1000 房间均在 16 的协调窗口内完成，多轮后任务和 active attempt 均回落为 0。

本轮没有通过真实平台或第三方运行时确认新的 P0/P1 问题。真实网络、真实插件、Windows 外部进程、句柄、内存和同步盘恢复边界仍未验证。

## 2. 目标与工作树边界

本轮目标来自前序 observability 报告中的 P3 事实：`poll_all()` 会为每个选中的房间一次性建立外层协调任务，虽然任务不会按轮次累积，峰值仍与关注房间数 N 相关。

本轮保持以下边界：

- 保留工作树中全部既有修改和未跟踪文件；
- 没有执行 `git reset`、`git checkout`、批量清理、删除、提交或推送；
- 没有安装软件、更新 pip 或下载外部工具；
- 没有运行 `pytest -m integration`；
- 所有新增调度和规模测试使用 fake plugin、合成 `offline.invalid` URL、临时配置和本地 asyncio 对象；
- 测试使用项目 `.venv` 解释器，并将 TEMP、TMP、ZHIBO_DATA_DIR、ZHIBO_LOG_FILE 和 pytest basetemp 指向隔离临时目录；
- 没有读取、输出或修改 `cookies.txt`、`followers.csv`、`settings.csv`、`sports/rooms.yaml`、`logs/*` 或锁文件真实内容。

当前工作树仍包含大量前序用户修改和未跟踪文件。本轮没有把它们重新归因于窗口化实现，也没有覆盖或清理它们。

## 3. 当前实现

### 3.1 调度窗口

实现位置：`monitor.py:1173` 附近的 `poll_all()`。

窗口化前，代码为所有待处理房间一次性构造外层协调任务列表。当前实现改为：

```text
items -> batch[0:window_size] -> gather -> 写回结果
      -> batch[window_size:2*window_size] -> gather -> 写回结果
      -> ...
```

窗口大小受 `max_concurrent_checks` 限制，因此在 N 增大而最大并发保持不变时，协调对象和 active attempt token 的峰值不再按 N 增长。

### 3.2 Deadline、取消和平台状态复用

窗口之间没有重置以下状态：

- 整轮 `poll_deadline`；
- 全局检测 semaphore；
- 每个平台的 semaphore；
- 平台 cooldown 和恢复探测状态；
- `failed_recovery_probes`；
- `_deadline_tasks`、`_active_attempts` 和状态回调任务集合。

`monitor.py:1043` 的 `_run_started_check_with_deadline()` 仍只在真正启动插件检测后计算单房间 deadline。`monitor.py:1111` 的 `_run_check_with_deadline()` 仍区分已启动超时和尚未启动的排队任务：前者记录 timeout 并保留现有取消/token 追踪，后者记录 deferred/backoff。

`monitor.py:793` 的 `shutdown()` 仍取消并有限等待 deadline task、stream deadline task 和状态回调 task，不因第三方 coroutine 无限阻塞关闭流程。

### 3.3 诊断字段

`monitor.py:52` 附近的 `MonitorDiagnostics` 和 `monitor.py:380` 附近的 `diagnostics_snapshot()` 保留原有聚合诊断结构，并增加：

- `coordination_tasks_current`；
- `coordination_tasks_peak`；
- snapshot 中 `tasks.coordination_current`；
- snapshot 中 `tasks.coordination_peak`。

这些字段只保存数量，不保存房间 URL、流地址、响应体、Cookie、授权信息或逐房间事件。

`plugins/bounded_executor.py:486` 的 `bounded_worker_snapshot()` 只返回 runner 数、pending 数和 PID 聚合；它不暴露命令、参数、URL 或 worker 输出。

## 4. 优化前后资源基准

### 4.1 优化前基准

窗口化实现前，使用离线 fake plugin 的基准记录如下。该表是本轮实施前的基准证据，来自当前 Goal 交接中已完成的离线运行；本轮没有通过回滚或覆盖生产代码来重放旧实现。

| 房间数 | 外层协调/active attempt 峰值 | 轮次结束后的当前 active attempt |
|---:|---:|---:|
| 100 | 100 | 0 |
| 500 | 500 | 0 |
| 1000 | 1000 | 0 |

这证明旧结构的任务不会跨轮累积，但瞬时对象峰值仍随 N 线性增加。

### 4.2 当前窗口化基准

使用 `max_concurrent_checks = 16`、三个合成离线平台（`huya`、`twitch`、`bilibili`）和 fake plugin，重新运行：100 房间 5 轮，500 房间 3 轮，1000 房间 3 轮。

| 房间数 | 轮数 | 协调任务峰值 | active attempt 峰值 | fake plugin 实际最大并发 | 单平台最大并发 | 平均单轮耗时 | plugin 调用数 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 5 | 16 | 16 | 3 | 1 | 0.025962 秒 | 500 |
| 500 | 3 | 16 | 16 | 3 | 1 | 0.129486 秒 | 1500 |
| 1000 | 3 | 16 | 16 | 3 | 1 | 0.248926 秒 | 3000 |

详细单轮耗时：

- 100 房间：0.026495、0.026903、0.025327、0.025208、0.025877 秒；
- 500 房间：0.124664、0.129300、0.134495 秒；
- 1000 房间：0.253183、0.248371、0.245224 秒。

由于基准使用三个既有平台键，而每个平台 semaphore 保持串行，fake plugin 实际最大并发为 3；这不是全局最大并发配置被降低，而是平台级串行约束仍然生效。窗口化协调峰值和 active attempt 峰值均稳定为 16，不随 N 增加。

基准结束后，所有规模均满足：

- `coordination_current == 0`；
- `active_attempts_current == 0`；
- `deadline_current == 0`；
- `state_current == 0`；
- 最大状态历史长度为 1；
- `deferred_count == 0`；
- 未启动 bounded worker，因而 worker pending 和 active PID 均为空。

## 5. 测试覆盖和语义回归

### 5.1 规模、多轮和资源边界

`tests/test_monitor_scheduler_window.py:47` 的参数化测试覆盖：

- 10 房间、5 轮、并发 1；
- 50 房间、5 轮、并发 4；
- 100 房间、5 轮、并发 8；
- 500 房间、3 轮、并发 16；
- 1000 房间、3 轮、并发 16。

该测试断言结果顺序、plugin 调用数、fake plugin 实际并发、每个平台并发、协调峰值、active attempt 峰值，以及每轮结束后的任务回落。

### 5.2 筛选、结果顺序和排队 deadline

`tests/test_monitor_scheduler_window.py:118` 覆盖：

- tag 筛选；
- disabled 房间；
- 空匹配；
- 全量 enabled 房间；
- 结果按输入索引顺序返回。

`tests/test_monitor_scheduler_window.py:159` 覆盖整轮 deadline 到期时的排队行为。使用 12 个房间、并发 2 和挂起 fake plugin，实际插件调用不超过 2；已启动项目走 timeout/cancel，未启动项目标记为 backoff/deferred，而不是伪装成网络失败。

### 5.3 整轮取消和 shutdown

`tests/test_monitor_scheduler_window.py:206` 是本轮补充的取消回归：在窗口内插件已启动后取消整个 `poll_all()`，fake plugin 延迟响应取消；随后执行 `shutdown()`，验证：

- 协调任务当前数回落为 0；
- active attempt 当前数回落为 0；
- deadline、stream deadline 和 state task 当前数回落为 0；
- 协调峰值仍不超过窗口大小；
- 实际启动的插件调用不超过全局并发。

### 5.4 平台 cooldown、恢复和 fallback

`tests/test_monitor_scheduler_window.py:257` 覆盖：

- 同平台串行限制；
- 平台共享错误后的 cooldown；
- cooldown 中跳过后续检测；
- 恢复探测；
- fallback 成功；
- fallback 不重复调用；
- 多轮结果顺序和 active attempt 回收。

### 5.5 既有 soak 和故障注入回归

`tests/test_monitor_soak.py` 继续覆盖：

- `50`、`116`：10/50/100 房间多轮 offline soak、任务和状态历史不增长；
- `116`：online/offline 交替、慢状态回调和回调异常；
- `174`：慢 UI 回调不延长网络 deadline；
- `221`：fallback 成功、全部失败、去重和 offline + CDN candidates；
- `303`：timeout、平台错误、cooldown、恢复和 delayed cancellation；
- `378`：重复 `get_stream_info()` 使用 fresh 结果且 stream task 不增长；
- `427`：stop 后重新 run；
- `487`：diagnostics snapshot 固定字段和敏感字段隔离；
- `548`：bounded worker 重复 shutdown 后 pending 为 0、active PID 为空。

已有核心测试继续覆盖：

- `tests/test_monitor_health.py`：平台健康度、单房间隔离、总 deadline、deferred/backoff、超时、shutdown；
- `tests/test_monitor_fault_injection.py`：共享连接异常停止同房间 fallback、状态保留、CDN 边界和任务回收；
- `tests/test_monitor_stream_info.py`：重叠轮询、慢回调、状态保留、独立取流 deadline；
- `tests/test_monitor_loop.py`：run/stop/restart 轮询循环；
- `tests/test_bounded_executor.py`：真实本地 bounded worker 的启动、取消、超时、进程树和回收。

## 6. 测试命令和实际结果

所有命令使用：

```powershell
D:\WPS SyncDisk\2.Tool\1.VibeCoding\ZHIBO\.venv\Scripts\python.exe
```

测试时使用独立的 TEMP、TMP、ZHIBO_DATA_DIR、ZHIBO_LOG_FILE 和 pytest basetemp。没有运行 `pytest -m integration`。

### 6.1 窗口化定向测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_monitor_scheduler_window.py
```

结果：

```text
10 passed in 1.81s
```

### 6.2 核心回归

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_monitor_scheduler_window.py tests/test_monitor_soak.py tests/test_monitor_fault_injection.py tests/test_monitor_health.py tests/test_monitor_loop.py tests/test_monitor_stream_info.py tests/test_bounded_executor.py
```

结果：

```text
65 passed in 6.67s
```

### 6.3 完整测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：

```text
365 passed, 1 skipped, 5 deselected in 10.69s
```

旧的 `355 passed, 1 skipped, 5 deselected` 是窗口化修改前的基线；加入本轮窗口化和取消测试后，最终完整结果为 365 passed，不能混用旧数字。

## 7. 静态、格式和敏感信息门禁

### 7.1 AST

使用项目虚拟环境对仓库 Python 文件进行 `py_compile`，排除 `.venv`、`.git`、`__pycache__` 和 `logs`。最终覆盖 73 个 Python 文件，全部通过。

### 7.2 diff 和尾随空白

```powershell
git diff --check
```

通过。Git 输出的 LF/CRLF 转换提示不是 whitespace error。

以下直接涉及文件的尾随空白检查通过：

- `monitor.py`；
- `plugins/bounded_executor.py`；
- `tests/test_monitor_soak.py`；
- `tests/test_monitor_scheduler_window.py`。

### 7.3 Git 跟踪敏感路径

`git ls-files` 对以下路径模式扫描无命中：

- `cookies.txt`；
- `followers.csv`；
- `settings.csv`；
- `sports/rooms.yaml`；
- `logs/`；
- `*.lock`。

### 7.4 安全内容扫描

对 `monitor.py`、`plugins/**/*.py`、`tests/**/*.py` 和 `docs/audits/*.md` 做了不输出原文的敏感模式扫描。

扫描命中仅位于测试中的合成脱敏占位符，涉及：

- `tests/test_app_logging.py`；
- `tests/test_config_editing.py`；
- `tests/test_monitor_soak.py`。

这些测试字符串用于验证日志、配置和诊断脱敏，不是真实凭据；生产代码和本轮新增调度实现没有发现完整 Cookie、Authorization/Bearer 值、真实签名流 URL 或代理认证信息。扫描结果没有输出任何敏感值。

## 8. 修改文件和既有文件边界

本轮窗口化相关的当前工作树文件包括：

- `monitor.py`：窗口化 `poll_all()` 和协调任务诊断字段；
- `tests/test_monitor_scheduler_window.py`：10/50/100/500/1000 房间窗口化测试、deadline、筛选、fallback、cooldown、恢复、diagnostics 和整轮取消测试；
- `tests/test_monitor_soak.py`：窗口化后的 active attempt/coordination 峰值断言；
- `plugins/bounded_executor.py`：沿用上一阶段的聚合 worker snapshot 作为回收验证接口；
- `docs/audits/2026-08-10-zhibo-scheduler-windowing.md`：本报告。

工作树中的其他大量修改和未跟踪文件属于前序工作或用户已有状态。本轮没有将其清理、重置、覆盖、提交或推送。

## 9. 风险状态和未验证边界

### 已由离线证据支持

- 外层协调任务峰值受窗口大小约束；
- active attempt 峰值受窗口大小约束；
- 多轮轮询后任务、token、状态回调和诊断结构不累积；
- 结果顺序、tag、disabled、空匹配保持兼容；
- 平台 semaphore、cooldown、恢复探测和 fallback 行为保持兼容；
- timeout、deferred/backoff、取消和 shutdown 路径有回归证据；
- diagnostics snapshot 保持固定字段和聚合数据边界；
- bounded worker snapshot 在测试结束后 pending 为 0、active PID 为空。

### 未由本轮确认

- 真实直播平台、真实认证、真实 Cookie 和真实 FS1 行为；
- 第三方插件内部 HTTP 连接池、重试和阻塞行为；
- Windows 真实 MPV/FFmpeg/streamlink/streamget/yt-dlp 进程的 RSS、CPU、句柄和进程树长期变化；
- 同步盘上的日志轮转、断电恢复、强制终止和双进程编辑语义；
- 真实网络异常下的连接复用和请求数量；
- 关注房间规模超过本轮 1000 房间时的实际内存和调度延迟上限。

这些边界不能由 fake plugin、合成 URL 或本地单元测试替代，后续如需验证必须安排明确的真实环境和回滚窗口。

## 10. 后续建议

1. 如果真实关注列表可能长期达到数千房间，再安排一次只读的 Windows 运行时采样，记录 CPU、RSS、句柄、HTTP 连接和外部子进程指标。
2. 在获得真实平台和授权后，单独验证平台级 cooldown、第三方插件取消响应和 shutdown 时限；不要把本轮离线结果当成真实平台证明。
3. HTTP client 复用仍是独立架构议题，本轮没有扩大到连接池重构。
4. 继续保留当前窗口大小、整轮 deadline、结果顺序和平台 semaphore 作为调度契约；后续任何进一步优化都应先加入对照基准。

本轮调度窗口化目标已经由当前代码、离线规模基准、65 项核心回归、365 项完整测试、AST、diff、尾随空白和敏感扫描共同支持。真实平台和第三方运行时边界已明确列为未验证事项，没有被离线测试结果掩盖。
