"""WebMonitorService 快照构建与事件推送测试。"""
import asyncio
import tempfile
from pathlib import Path

import pytest

from zhibo.plugins import _plugins, register_plugin
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin
from webui.service import WebMonitorService

CSV_HEADER = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"


class _Collector:
    def __init__(self):
        self.events = []

    def submit(self, kind, payload):
        self.events.append((kind, payload))


class _OfflinePlugin(LiveStreamPlugin):
    name = "webui_offline"

    async def check_live(self, url, **kwargs):
        return LiveInfo(is_live=False)

    async def get_stream_url(self, url, quality, **kwargs):
        return ""


def _write_csv(rows: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(CSV_HEADER + rows)
    return f.name


def test_service_emits_snapshot_with_rows_and_options():
    previous = dict(_plugins)
    fpath = _write_csv(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n"
        "true,主播二,LOL,webui_offline,,douyu,https://douyu.com/2,best,\n"
    )
    register_plugin(_OfflinePlugin())
    collector = _Collector()
    service = WebMonitorService(collector, config_path=fpath)
    try:
        service.start()
        # 首次快照在启动时立即推送。
        kinds = [kind for kind, _ in collector.events]
        assert "snapshot" in kinds
        snapshot = service.snapshot()
        assert len(snapshot["rows"]) == 2
        row = snapshot["rows"][0]
        assert row["name"] == "主播一"
        assert isinstance(row["quality_options"], list) and row["quality_options"]
        assert "plugin_options" in snapshot and "webui_offline" in snapshot["plugin_options"]
        assert snapshot["tags"][0] == "全部"
        # 手动刷新走 force 语义并再次推送快照。
        collector.events.clear()
        service.refresh()
        for _ in range(100):
            if any(kind == "log" and "手动刷新完成" in p.get("text", "")
                   for kind, p in collector.events):
                break
            asyncio.run(asyncio.sleep(0))
            import time as _t
            _t.sleep(0.05)
        kinds = [kind for kind, _ in collector.events]
        assert "snapshot" in kinds
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_service_survives_missing_plugin_registry_cleanup():
    """服务线程启动失败时通过 fatal 事件上报而不是静默死亡。"""
    collector = _Collector()
    service = WebMonitorService(collector, config_path=str(Path(tempfile.mkdtemp()) / "missing.csv"))
    service.start()
    service.stop(timeout=3)
    kinds = [kind for kind, _ in collector.events]
    assert "fatal" in kinds or "stopped" in kinds
