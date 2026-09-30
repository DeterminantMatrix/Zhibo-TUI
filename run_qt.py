"""打包入口 — PyInstaller 的多进程子进程会重新执行本文件。

``freeze_support`` 必须在任何业务导入之前调用，否则打包后的
隔离检测子进程会陷入无限自启动。
"""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from qt_quick.main import main

    raise SystemExit(main())
