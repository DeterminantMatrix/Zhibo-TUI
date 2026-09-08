# ZHIBO 重构设计方案：pywebview + 98.css（Windows 98 复古风）

> 状态：待确认。确认后按阶段实施，qt_quick 版本全程保留可用，随时可切回。

## 1. 目标与动机

- **视觉**：Windows 98 复古风格（凹凸立体边框、经典蓝标题栏、分段式进度条、宋体像素感）
- **架构**：界面层换为 HTML/CSS/JS（pywebview + WebView2），**后端 `zhibo/` 包 100% 不动**
- **瘦身**：移除 PySide6（venv -640MB），磁盘占用预计从 ~780MB 降到 ~120MB
- **稳定**：播放维持外部 mpv（当前验证过的最稳形态），不引入内嵌播放

## 2. 技术栈

| 层 | 技术 | 说明 |
| --- | --- | --- |
| 窗口 | pywebview 6 | Win10/11 自带 WebView2，零体积运行时；frameless + 98.css 自绘标题栏 |
| 样式 | 98.css（本地 vendored） | 窗口/按钮/输入框/进度条/菜单全套 Win98 控件；不连 CDN |
| 字体 | Pixelated MS Sans Serif + **SimSun 回退** | 像素字体无中文字形，宋体正是 Win98 时代的中文默认，风格天然吻合 |
| 逻辑 | 原生 JS（无框架） | 44 个房间的表格量级，无需框架与虚拟滚动 |
| 托盘 | pystray | 菜单：显示/隐藏/退出；双击唤起 |
| 通知 | winotify | Windows 原生 toast；点击 = 唤起窗口并播放该主播 |
| 后端 | `zhibo/` 包原样复用 | monitor/config/plugins/single_instance/desktop(detail) 等 |

## 3. 架构

```
┌───────────────────── WebView2 窗口（98.css）─────────────────────┐
│  index.html + app.js（表格/对话框/菜单/状态栏，纯前端状态）       │
│      ↕ window.pywebview.api.*（JS→Py 调用）                      │
│      ↕ window.zhibo.onEvent(batch)（Py→JS 推送，10Hz 合并）      │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                    webui/api.py（ZhiboApi）
                               │
        ┌──────────────────────┴───────────────────────┐
        │ 复用 qt_quick/worker.py 的业务编排（迁为      │
        │ webui/service.py：asyncio 线程 + 操作表）     │
        └──────┬──────────┬─────────┬────────┬────────┘
          zhibo.monitor  config  plugins  desktop(play_url+IPC)
```

- **纯函数直接搬家**：`qt_quick/viewmodel.py`（快照/筛选/拼音排序）不依赖 Qt，迁到 `zhibo/viewmodel.py` 继续用
- **推送通道** `webui/events.py`：任意线程投递事件到线程安全队列，主循环 100ms 批量 `evaluate_js('window.zhibo.onEvent([...])')`；JSON 序列化一次完成
- **单实例**：沿用 `zhibo.single_instance`，key 为 `::webui`，与旧版可并行运行

## 4. 界面设计（Win98 风格映射）

**主窗口**（frameless，98.css 自绘 chrome，`--pywebview-drag` 拖动区）：

```
┌─ 直播监控工具 - [全部] ────────────────────────[_][□][X]─┐
│ 文件(F)  查看(V)  操作(O)  工具(T)  帮助(H)              │  ← 经典菜单栏
│ [▶播放] [■停止] [↻刷新] [详情] [编辑]     🔊通知  ┌搜索┐ │  ← 工具栏
│ ┌─[全部][游戏][LOL][ASMR]…──────────────────────┐      │  ← 98 标签
│ │ ┌─状态─┬─主播─┬─平台─┬─标题─────┬画质▾┬插件▾─┐ │      │
│ │ │ ●   │德云色│ B站  │ …       │原画▾│sl▾  │ │      │  ← 表格：
│ │ │ ○   │ …    │      │         │     │      │ │      │    行悬停/选中
│ │ └─────┴──────┴──────┴──────────┴─────┴──────┘ │      │    表头点击排序▲▼
│ └───────────────────────────────────────────────┘      │    双击=播放
│ ┌─运行日志（滚动，等宽字体）────────────────────┐       │
│ └───────────────────────────────────────────────┘       │
│ [导入][更新][下载][代理][设置]   在线 3/44 │ 第2轮 │ 32s │  ← 状态栏（sunken 格）
└──────────────────────────────────────────────────────────┘
```

- **画质/插件列** = 表格内嵌 98.css `<select>`（沿用现有事务保存）
- **右键菜单** = 98 样式弹出菜单（详情与修改/停用恢复/播放/复制流/打开网页/下载/删除）
- **详情+修改** = 子窗口（98.css `.window`，窗口内可拖动）：左侧状态行（tree view 风格），右侧 98 表单，保存→98 确认对话框展示脱敏差异
- **设置/代理/导入/更新中心** = 各自一个 98 模态窗口；更新中心的进度条用 98.css **分段蓝块进度条**（复古感最强的地方）
- **正在播放行** = 状态列 ▶ 前缀 + 选中高亮（沿用现有语义）
- 窗口位置/列宽/排序：`localStorage` 持久化

## 5. 桥接协议

**JS → Python（js_api，~25 个方法）**：

`getSnapshot / refresh / toggleNotifications / play(idx) / stopPlayer / copyStream / openWeb / loadDetails / previewEdit / confirmEdit / toggleEnabled / setPlugin / setQuality / loadSettings / previewSettings / confirmSettings / loadProxy / saveProxy / testProxy / previewImport / confirmImport / loadUpdateCenter / checkAllUpdates / checkUpdate / runUpdate(target, content) / listFormats / startDownload / getConfigPath`

**Python → JS（事件推送）**：

`snapshot {rows, tags, health, nextPoll} / log [lines] / liveEvent {idx, name, title} / dialogData {kind, payload} / operationFinished {kind, ok, msg, payload} / progress {kind, pct, text} / playerState {playing, idx} / fatal {msg} / restart {attempt}`

## 6. 迁移阶段（每阶段独立可验证，旧版始终可用）

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P0 | 依赖安装（pywebview/pystray/winotify）、98.css vendored、空窗口+托盘+单实例 | 双击 `start_web.vbs` 出 98 窗口 |
| P1 | 表格+快照推送+标签/筛选/搜索/拼音排序（复用 viewmodel） | 数据/交互与旧版一致 |
| P2 | 详情修改/设置/代理/导入 全套事务对话框 | 保存流程含差异确认 |
| P3 | 播放/停止/复制流/右键菜单/快捷键/通知点击播放 | 外部 mpv + Ctrl+P/音量 |
| P4 | 更新中心（自动全检+分段进度条）/下载/Cookie/FS1 | 与旧版功能对齐 |
| P5 | 日志面板/窗口几何记忆/收尾 | 全量回归 + 删 qt_quick（单独提交） |

启动方式：新增 `start_web.vbs`（与 `start_native.vbs` 并存，单实例 key 不同可同时跑）；确认新版稳定后再移除旧版。

## 7. 风险与对策

| 风险 | 对策 |
| --- | --- |
| WebView2 缺失（老系统） | 启动检测，缺失时提示安装链接（Win10 1809+ 一般自带） |
| 像素字体无中文 | SimSun 回退（本身就是 98 时代字体） |
| evaluate_js 跨线程稳定性 | 事件队列 + 100ms 批量合并；异常时降级为 JS 250ms 心跳拉取 |
| 98.css 无表格组件 | 自定义 ~30 行 CSS（inset 边框 + 表头按钮化） |
| 丢失功能 | 以 P1–P4 清单逐项对齐验收；qt_quick 保留至 P5 后 |

## 8. 明确不做（本期）

- 内嵌播放器（上次验证不稳定，外部 mpv 保留；日志显示断流诊断基建仍在）
- exe 打包（先 .vbs 启动；pywebview 版若将来打包，onedir 预计仅 60–90MB）
