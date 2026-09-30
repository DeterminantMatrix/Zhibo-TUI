# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：TUI 版，单文件夹模式（onedir）。

选 onedir 而非单文件：本程序用多进程隔离子进程做插件检测，
单文件模式下每个子进程都要重新解包（每次数秒），onedir 则共享
同一份文件；启动也更快、更不易被杀毒软件误报。

相比旧 Qt 版 spec：无 PySide6/QML（体积大头），console=True 保留
真实终端（Textual 渲染需要），数据锚定 exe 所在目录（app_root）。
"""
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

datas = [
    # 托盘图标按包内相对路径进入解包目录，tui/__main__ 从 resource_root 读取。
    ("tui/assets", "tui/assets"),
]
# 缺 dist-info 会让打包版更新中心把全部组件误报"未安装"。
for _meta in ("streamlink", "streamget", "yt-dlp", "httpx", "packaging", "textual"):
    datas += copy_metadata(_meta)

# Textual 的控件模块经 __getattr__ 惰性导入，静态分析看不见，必须整包收集。
_textual_submodules = collect_submodules("textual")

a = Analysis(
    ["run_tui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # 插件通过 importlib 按字符串加载，静态分析看不到。
        "zhibo.plugins.fs1_plugin",
        "zhibo.plugins.haixing_plugin",
        "zhibo.plugins.streamget_plugin",
        "zhibo.plugins.streamlink_plugin",
        "zhibo.plugins.yt_dlp_plugin",
        *_textual_submodules,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "pytest", "pytest_asyncio",
        # TUI 版不携带 Qt；qt_quick 是本地包，防止被意外扫入。
        "PySide6", "qt_quick",
    ],
    noarchive=False,
    optimize=0,
)

# streamlink 拖进来的 pycountry 自带全部语言的 gettext 翻译（约 20MB）；
# 国家/语言数据库仍在（缺翻译时回落英文名），只剪 locales 目录。
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith("pycountry/locales")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Zhibo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="tui/assets/tray.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Zhibo",
)
