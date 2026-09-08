"""通知点击启动器 — zhibo:// 协议入口。

toast 点击后由系统拉起本脚本：把 play:<idx> 转发给运行中的主实例；
没有运行中的实例时正常启动应用（不自动播放）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webui.main import main

sys.exit(main(["--notify-play", *sys.argv[1:]]))
