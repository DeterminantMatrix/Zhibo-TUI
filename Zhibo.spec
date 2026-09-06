# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件夹模式（onedir）。

选 onedir 而非单文件：本程序用多进程隔离子进程做插件检测，
单文件模式下每个子进程都要重新解包（每次数秒），onedir 则共享
同一份文件；启动也更快、更不易被杀毒软件误报。
"""

a = Analysis(
    ["run_zhibo.py"],
    pathex=[],
    binaries=[],
    datas=[
        # QML 界面与图标必须按 qt_quick 包内的相对路径进入解包目录，
    # qt_quick/main.py 会从 _MEIPASS/qt_quick/... 读取它们。
        ("qt_quick/qml", "qt_quick/qml"),
        ("qt_quick/assets", "qt_quick/assets"),
    ],
    hiddenimports=[
        # 插件通过 importlib 按字符串加载，静态分析看不到。
        "zhibo.plugins.fs1_plugin",
        "zhibo.plugins.streamget_plugin",
        "zhibo.plugins.streamlink_plugin",
        "zhibo.plugins.yt_dlp_plugin",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "pytest_asyncio"],
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------------------
# PyInstaller 的 PySide6 QML 扫描会把 WebEngine（Chromium，196MB）、Quick3D、
# 软件渲染回退等本程序用不到的 Qt 成分一并拖进来；模块 excludes 拦不住
# 二进制级收集，只能在 Analysis 之后直接过滤。
# 注意：opengl32sw 被剪掉后，无可用 GPU 的机器上可能无法渲染。
_BIN_EXCLUDE_SUBSTRINGS = (
    "Qt6WebEngine", "Qt6Pdf", "Qt6Charts", "Qt6DataVisualization",
    "Qt6Quick3D", "Qt63D", "Qt6RemoteObjects", "Qt6NetworkAuth",
    "Qt6TextToSpeech", "Qt6SerialPort", "Qt6Bluetooth", "Qt6Nfc",
    "opengl32sw", "d3dcompiler",
)
a.binaries = [b for b in a.binaries if not any(x in b[0] for x in _BIN_EXCLUDE_SUBSTRINGS)]
a.datas = [
    d for d in a.datas
    if "translations" not in d[0]
    and not any(part in d[0] for part in ("QtWebEngine", "QtQuick3D", "Qt3D", "QtCharts"))
]
a.binaries += [
    # 解压/网络等可能被 QML 间接引用的二进制若被误剪，可在此加回。
]

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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="qt_quick/assets/tray.ico",
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
