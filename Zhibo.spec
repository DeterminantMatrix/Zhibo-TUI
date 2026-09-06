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
