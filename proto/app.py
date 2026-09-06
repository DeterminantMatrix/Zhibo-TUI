"""pywebview + WebView2 界面原型：验证"现代 Web 风格 + 小体积"路线。

真实读取项目根目录的 followers.csv；表格渲染、搜索、标签筛选在
前端完成；点击行打开详情抽屉，"打开直播间"按钮通过 pywebview
桥接调用 Python 的 webbrowser —— 演示前后端交互链路。

运行：.venv\\Scripts\\python.exe proto\\app.py
自检：.venv\\Scripts\\python.exe proto\\app.py --selftest
"""
from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_followers() -> list[dict]:
    from zhibo.config import ConfigManager

    cfg = ConfigManager(str(PROJECT_ROOT / "followers.csv")).load_config()
    return [
        {
            "name": f.name,
            "platform": f.platform or "未知",
            "tags": list(f.tags),
            "quality": f.quality or "best",
            "enabled": bool(f.enabled),
            "url": f.url,
        }
        for f in cfg.followers
    ]


class Api:
    """暴露给前端 JS 的 Python 接口（pywebview 自动转为 pywebview.api.*）。"""

    def __init__(self, followers: list[dict]):
        self._followers = followers

    def getFollowers(self) -> list[dict]:
        return self._followers

    def openWeb(self, url: str) -> bool:
        webbrowser.open(url)
        return True

    def appInfo(self) -> dict:
        return {
            "engine": "pywebview 6 + 系统 WebView2",
            "count": len(self._followers),
        }


def main() -> int:
    followers = load_followers()

    if "--selftest" in sys.argv:
        platforms = sorted({item["platform"] for item in followers})
        print(f"selftest ok: {len(followers)} 个关注项，平台 {platforms}")
        return 0

    import webview

    html = (Path(__file__).with_name("index.html")).read_text(encoding="utf-8")
    webview.create_window(
        "ZHIBO · Web 界面原型",
        html=html,
        js_api=Api(followers),
        width=1280,
        height=820,
        min_size=(960, 600),
        background_color="#0b0f16",
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
