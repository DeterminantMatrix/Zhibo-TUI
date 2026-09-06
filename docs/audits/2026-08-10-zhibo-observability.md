# ZHIBO 离线资源观测、长时间运行模拟与脱敏诊断报告

- 报告日期：2026-08-10
- 工作区：`D:\WPS SyncDisk\2.Tool\1.VibeCoding\ZHIBO`
- 分支：`codex/initial-zhibo-tui`
- 本轮性质：完全离线的模拟、资源回归、诊断快照和代码级验证
- 外部边界：未访问真实直播平台、未发送真实 FS1 请求、未读取真实 Cookie、未使用真实账号、未调用真实 MPV/FFmpeg/uosc/7z/py7zr 更新流程

## 1. 执行摘要

本轮在已有监控 deadline、取消、状态历史、平台退避、fallback 隔离和 bounded worker 回收基础上，增加了可重复的离线长时间运行模拟，以及只保留聚合字段的内部诊断快照。

本轮直接涉及的代码和测试包括：

- `monitor.py`：增加有限字段的 `MonitorDiagnostics`、插件调用/耗时聚合、任务峰值和诊断 snapshot；补充 timeout、取消、deferred、cooldown、fallback、回调异常和轮询耗时计数。
- `plugins/bounded_executor.py`：增加 `bounded_worker_snapshot()`，聚合 runner 数、pending worker 数和 active PID，不暴露命令、参数、URL 或 worker 数据。
- `tests/test_monitor_soak.py`：增加 11 个完全离线的 soak、故障注入、生命周期、回调、fallback、重复取流和敏感字段回归测试。
- 本报告：记录测试矩阵、实际结果、资源边界和未验证事项。

最终验证结果：

- soak 与核心回归：`55 passed in 4.77s`；
- 完整测试：`355 passed, 1 skipped, 5 deselected in 8.86s`；
- 72 个 Python 文件 AST 语法检查通过；
- `git diff --check` 通过；
- 本轮涉及文件没有尾随空白；
- Git 跟踪敏感运行路径扫描为空；
- 没有把 Cookie、Authorization、Bearer、签名 URL、代理认证信息、响应体或真实用户数据写入诊断 snapshot 或报告。

当前没有由本轮离线测试确认的 P0/P1 问题。测试确认了一个仍需关注的 P3 资源设计事实：`poll_all()` 会为选中的房间创建一层 O(N) 的协调任务，虽然每轮结束后能够回收，插件执行仍受并发 semaphore 限制。该问题本轮没有进行大规模调度重构。

## 2. 目标和验证边界

本轮目标来自持续 goal：建立“离线资源观测、长时间运行模拟和脱敏诊断”能力，而不是重新实施上一轮完整安全审计。

本轮严格遵守以下边界：

- 保留工作树中用户已有的修改和未跟踪文件；
- 不执行 reset、checkout、批量清理、删除、提交或推送；
- 不安装软件、不更新 pip、不下载外部工具；
- 不运行 `pytest -m integration`；
- 测试使用项目 `.venv` 解释器；
- 测试数据、房间 URL、插件和故障均为 fake/in-memory；
- 测试临时目录、数据目录和日志目标指向独立临时位置；
- 没有读取或输出 `cookies.txt`、`followers.csv`、`settings.csv`、`sports/rooms.yaml`、`logs/*` 或锁文件真实内容。

本轮结论只证明离线 fake 模型、监控调度代码和现有 worker 统计接口的行为。它不等价于真实平台、真实第三方库、真实网络、真实 Windows 句柄/内存或真实外部进程的长时间运行证明。

## 3. 诊断结构

### 3.1 MonitorDiagnostics

`monitor.py:38-72` 增加了 `PluginDiagnostics` 和 `MonitorDiagnostics`。聚合字段包括：

- 轮询轮数和选中房间数；
- online/offline/error/skipped/backoff/disabled 等状态事件计数；
- 插件总调用数；
- fallback 调用数和去重数；
- timeout、取消、deadline deferred 和 cooldown 计数；
- 状态通知数和回调异常数；
- 当前任务数与任务峰值；
- 当前 active attempt 数与峰值；
- 最近、累计和最大轮询耗时；
- 每个插件的调用数、成功数、失败数、累计耗时和平均耗时。

`monitor.py:324-423` 提供 `diagnostics_snapshot()`。snapshot 使用固定的顶层 allow-list，只返回聚合数据，不返回房间名称、房间 URL、异常原文、流地址、响应体、Cookie 或授权信息。

为了避免诊断结构自身长期膨胀：

- 状态键不在有限集合内时归入 `other`；
- 插件诊断键最多保留 32 个，超出部分归入 `other`；
- 不保存逐轮事件列表、不保存逐房间 URL、不保存原始错误列表。

### 3.2 Bounded worker snapshot

`plugins/bounded_executor.py:486-501` 增加 `bounded_worker_snapshot()`，只聚合：

- runner 数；
- pending worker 数；
- active PID 列表及数量。

它不读取或返回 worker 命令、参数、URL、Cookie、响应内容或子进程输出。原有 `BoundedExecutor.pending_count` 和 `active_pids` 属性保持不变，现有 worker 测试继续覆盖实际进程回收路径。

## 4. 离线 soak 测试矩阵

### 4.1 规模和轮询参数

`tests/test_monitor_soak.py:46-112` 覆盖：

| 房间数 | 最大并发配置 | 轮询间隔配置 | 连续轮询轮数 | 模型 |
|---:|---:|---:|---:|---|
| 10 | 1 | 5 秒 | 8 | 多平台稳定 offline |
| 50 | 4 | 7 秒 | 8 | 多平台稳定 offline |
| 100 | 8 | 11 秒 | 8 | 多平台稳定 offline |

稳定 offline 模型每次只返回确认的 offline 结果，并通过 `await asyncio.sleep(0)` 主动让出事件循环。测试证明：

- 轮次和房间计数等于预期；
- 插件调用数等于 `房间数 × 轮次`；
- 每轮结束后 `_deadline_tasks`、`_stream_deadline_tasks`、`_state_tasks` 和 `_active_attempts` 均回落为空；
- 每个房间的相邻重复 offline 状态被去重，8 轮后历史仍为 1 条；
- 所有历史长度均不超过 20；
- worker 聚合 snapshot 的 `pending_count` 为 0，`active_pids` 为空。

这里的并发语义需要明确：`poll_all()` 的外层协调任务按选中房间数创建，因而 `_active_attempts` 峰值不应误读为插件执行并发。测试观测到峰值不超过 N；真正进入插件检测的路径仍由全局 semaphore 和每个平台 semaphore 控制。

### 4.2 状态交替、慢回调和回调异常

`tests/test_monitor_soak.py:115-218` 覆盖：

- 两个房间连续 6 轮 online/offline 交替；
- 异步 UI 回调人为延迟；
- 回调主动抛出异常；
- 回调异常被转为内部错误通知；
- 状态通知任务最终回收；
- 后续轮询不被回调异常中断；
- 状态历史仍受 20 条上限约束。

`tests/test_monitor_soak.py:173-218` 进一步证明慢 UI 回调不会占用网络检测 deadline：网络结果在短 poll deadline 内完成，回调在后台任务中继续执行，回调完成后 state task 当前数回落为 0。

### 4.3 fallback、CDN candidates 和错误隔离

`tests/test_monitor_soak.py:220-299` 覆盖：

- 主插件失败后 fallback 成功；
- 所有 fallback 失败；
- fallback 列表重复项去重；
- 离线结果携带 CDN candidates 时仍保持 offline；
- 离线结果不会因为有候选 URL 而进入 fallback 或被判定为 online。

CDN candidates 仍然属于播放阶段数据，诊断 snapshot 不保存候选 URL。

### 4.4 timeout、cooldown、恢复和延迟取消

`tests/test_monitor_soak.py:302-375` 覆盖：

- 单房间 deadline timeout；
- 平台共享连接错误；
- 同平台其他房间被 cooldown 跳过；
- cooldown 结束后恢复探测；
- 模拟第三方 coroutine 延迟响应取消；
- timeout 和取消计数增加；
- 延迟取消任务最终回收；
- shutdown 后没有残留 deadline、stream deadline、state task 或 active attempt。

本轮没有使用永久挂死且无法安全结束的 coroutine 作为“成功”证据。永久不响应取消的第三方库仍然属于真实环境边界；当前生产代码会保留其任务引用并在 shutdown 中按上限等待，不会无限阻塞关闭流程。

### 4.5 重复取流和生命周期重启

`tests/test_monitor_soak.py:377-485` 覆盖：

- `get_stream_info()` 连续调用 20 次；
- 每次使用 fresh 结果，不复用旧流地址；
- stream deadline task 不随调用累积；
- `MonitorService.run()` 停止后重新启动；
- 两次生命周期结束后 deadline、stream deadline、state task 和 active attempt 均为空。

### 4.6 敏感字段诊断测试

`tests/test_monitor_soak.py:486-546` 使用合成的离线错误文本和签名形状字符串验证 allow-list。断言包括：

- snapshot 顶层字段集合固定；
- 合成的 Bearer/token 文本不出现在 snapshot；
- 合成的签名 query 不出现在 snapshot；
- `Authorization` 字段名不出现在 snapshot；
- snapshot 可被 JSON 序列化；
- plugin metrics 只包含计数和耗时聚合。

这些字符串是测试内的合成占位值，不是用户凭据、真实 Cookie、真实签名流地址或真实代理信息。

## 5. 资源观测结果

### 5.1 任务和状态历史

离线矩阵证明了以下不变量：

- 多轮轮询后当前 deadline task 数回到 0；
- 多轮轮询后当前 stream deadline task 数回到 0；
- 慢状态通知任务完成后当前 state task 数回到 0；
- active attempt token 不残留；
- 状态历史每房间最多 20 条，并对相邻完全相同事件去重；
- 诊断结构不保存逐轮或逐房间事件数组；
- 诊断状态键和插件键有固定边界。

### 5.2 worker

新测试在无活动 worker 时验证了重复 shutdown 的幂等性和空 snapshot。既有 `tests/test_bounded_executor.py` 继续覆盖真实本地 worker 的启动、忙状态、异常、取消、进度回调、shutdown、回收和 PID 清理路径；本轮完整测试通过，因此本轮新增指标没有破坏现有 worker 生命周期。

### 5.3 轮询任务的 O(N) 事实

当前 `poll_all()` 为选中的房间建立外层协调任务列表。该设计的实际含义是：

- 关注房间数增加时，调度协程对象和 token 的瞬时数量按 N 增加；
- 插件执行仍受到最大并发 semaphore 限制；
- 8 轮连续运行后任务不会按轮次累积；
- 对超大 N，窗口化调度仍可能降低瞬时对象和调度开销。

本轮不进行窗口化重构，因为它会改变现有调度、平台 semaphore、总 deadline 和结果排序语义。该项保留为 P3 资源优化建议。

### 5.4 未在本轮直接测量的资源

本轮没有读取真实日志文件，也没有启动真实外部工具，因此没有声称已经测量以下项目：

- Windows 进程句柄和句柄增长；
- MPV/FFmpeg/uosc/streamlink/streamget/yt-dlp 的真实 RSS/CPU；
- 实际 HTTP 连接池、TCP 连接复用和网络请求数；
- 同步盘上的真实日志磁盘增长、轮转和断电恢复；
- 第三方库内部重试和阻塞行为。

这些项目需要明确的真实环境运行窗口后再验证，不能由本轮 fake plugin 测试替代。

## 6. 安全和隐私检查

### 6.1 诊断边界

本轮新增 snapshot 没有保存以下内容：

- Cookie、SESSDATA、CSRF；
- Authorization、Bearer、token、signature；
- userinfo URL 或代理认证信息；
- 完整流地址或 CDN candidates；
- 响应体；
- 原始异常文本；
- 房间名称和用户私有数据。

插件名只作为经过 ASCII 字符过滤、长度截断且有数量上限的 metrics key。未知状态归入 `other`，不会把任意文本直接作为诊断键无限保存。

### 6.2 当前扫描证据

- `git ls-files` 对 `cookies.txt`、`followers.csv`、`settings.csv`、`sports/rooms.yaml`、`logs/` 和锁文件扫描为空；
- 新增测试中的敏感形状字符串均为 `offline.invalid` 合成场景，且测试明确断言它们不进入 snapshot；
- `git diff --check` 通过；
- 72 个 Python 文件 AST 解析通过；
- 新增和直接修改的三个文件没有尾随空白；
- 没有执行真实网络请求或读取私有运行时文件。

## 7. 已确认问题、修复和未确认事项

| 编号 | 级别 | 状态 | 事实和证据 | 处理 |
|---|---|---|---|---|
| OBS-01 | P3 | 已记录，未重构 | `poll_all()` 外层协调任务按 N 创建；100 房间 soak 中峰值不超过 N，8 轮后回落为 0。 | 暂不重写；后续可评估窗口化调度。 |
| OBS-02 | P2 | 未确认 | 真实第三方插件、HTTP、MPV/FFmpeg 和 Windows 进程/句柄/内存行为未在本轮运行。 | 需要单独的真实环境、真实账号/平台授权和明确安全窗口。 |
| OBS-03 | P2 | 未确认 | 真实日志轮转、同步盘断电、强制终止和双进程编辑语义未通过本轮验证。 | 保留为真实 Windows/同步盘验证项；本轮未读取日志内容。 |
| OBS-04 | P3 | 已缓解 | 诊断结构可能因动态插件名或状态名增长的风险已通过状态归一化、插件键上限和 `other` 聚合限制。 | 已增加回归覆盖并纳入 snapshot。 |

本轮没有发现新的可由离线证据确认的 P0/P1 问题。

## 8. 测试命令和实际结果

测试均通过项目虚拟环境执行，并把 `TEMP`、`TMP`、`ZHIBO_DATA_DIR`、`ZHIBO_LOG_FILE` 和 pytest basetemp 指向独立临时目录。

### 8.1 soak 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_monitor_soak.py
```

最终 soak 测试包含 11 个测试；在核心回归组合中全部通过。

### 8.2 核心回归

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_monitor_soak.py tests/test_monitor_fault_injection.py tests/test_monitor_health.py tests/test_monitor_loop.py tests/test_monitor_stream_info.py tests/test_bounded_executor.py
```

结果：`55 passed in 4.77s`。

### 8.3 完整回归

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：`355 passed, 1 skipped, 5 deselected in 8.86s`。

按照目标边界，没有运行：

```powershell
pytest -m integration
```

### 8.4 静态和敏感检查

- AST 语法检查：`72 Python files`，全部通过；
- `git diff --check`：通过；
- 尾随空白检查：通过；
- Git 跟踪敏感路径检查：`none`；
- 新 snapshot JSON 脱敏测试：通过；
- worker 空 snapshot 和幂等 shutdown：通过。

## 9. 本轮直接涉及的文件

- `monitor.py`
- `plugins/bounded_executor.py`
- `tests/test_monitor_soak.py`
- `docs/audits/2026-08-10-zhibo-observability.md`

工作树中其他大量已修改和未跟踪文件属于此前用户工作或上一阶段审计结果，本轮没有重置、清理、覆盖或删除它们。

## 10. 后续建议

建议按以下顺序处理，而不是立刻扩大本轮代码修改：

1. 如果关注房间数可能达到数百或更高，单独设计窗口化调度实验，保持现有平台 semaphore、总 deadline、取消和结果顺序契约不变。
2. 在不保存 URL/响应体的前提下，为真实运行增加独立的 CPU、RSS、句柄、HTTP 请求数和子进程采样方案；先做只读采样，再决定是否改代码。
3. 取得正式 FS1 播放 API 固定 host 清单后，再评估从命名空间 allow-list 收紧为固定 allow-list。
4. 为 PyPI 和外部工具更新流程确定 hash lock、版本范围和镜像策略后，再做供应链修改。
5. 只有在真实平台、真实授权、真实 Windows 外部工具和明确回滚窗口准备好后，才做长时间运行验证。

本轮离线 observability 目标已经有当前代码、测试和扫描结果支撑；真实平台和第三方运行时边界仍保持明确的“未验证”，没有被模拟测试结果掩盖。
