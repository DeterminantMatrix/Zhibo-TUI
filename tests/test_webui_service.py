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


# ---- P2：事务对话框 ------------------------------------------------------


def _wait_for(collector, predicate, timeout=6.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(collector.events):
            return True
        time.sleep(0.05)
    return False


def _last_dialog(collector, kind):
    matches = [p for k, p in collector.events if k == "dialog" and p["kind"] == kind]
    return matches[-1]["payload"] if matches else None


def _started_service(rows: str, collector):
    from zhibo.plugins import _plugins, register_plugin

    previous = dict(_plugins)
    register_plugin(_OfflinePlugin())
    fpath = _write_csv(rows)
    service = WebMonitorService(collector, config_path=fpath)
    service.start()
    return service, fpath, previous


def test_details_and_edit_transaction_roundtrip():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        # 等首轮检测结束，避免编辑撞上 is_checking 保护。
        assert _wait_for(collector, lambda evs: any(
            k == "polling" and not p.get("active") for k, p in evs
        ))
        service.load_details(0)
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "edit" for k, p in evs
        ))
        payload = _last_dialog(collector, "edit")
        assert payload["form"]["name"] == "主播一"
        assert payload["detailRows"] and payload["pluginOptions"]

        collector.events.clear()
        service.preview_edit(0, {"name": "主播一改"})
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "edit" and p["payload"].get("stage") == "confirm"
            for k, p in evs
        ))
        confirm = _last_dialog(collector, "edit")
        assert "名称" in confirm["previewText"] and "主播一改" in confirm["previewText"]

        collector.events.clear()
        service.confirm_dialog("edit")
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "edit" and p["ok"]
            and p["payload"].get("close") for k, p in evs
        ))
        assert service._service.cfg.followers[0].name == "主播一改"
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_settings_preview_and_confirm_roundtrip():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        service.load_settings()
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "settings" for k, p in evs
        ))
        form = _last_dialog(collector, "settings")
        assert form["stage"] == "form" and "poll_interval" in form
        current = int(form["poll_interval"])
        target = current + 1 if current < 3600 else current - 1

        collector.events.clear()
        service.preview_settings({"poll_interval": str(target)})
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "settings" and p["payload"].get("stage") == "confirm"
            for k, p in evs
        ))
        confirm = _last_dialog(collector, "settings")
        assert "轮询间隔" in confirm["previewText"]

        collector.events.clear()
        service.confirm_dialog("settings")
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "settings" and p["ok"]
            and p["payload"].get("close") for k, p in evs
        ))
        assert service._service.config_manager.read_monitoring_settings()["poll_interval"] == target
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)
        Path(fpath).with_name("settings.csv").unlink(missing_ok=True)


def test_proxy_load_test_and_save(monkeypatch):
    import webui.service as service_module
    from zhibo.proxy_config import PROXY_PLATFORMS, set_platform_proxies

    # 隔离机器默认代理（settings.csv 里的 127.0.0.1:7890），保证测试确定性。
    monkeypatch.setattr(service_module, "proxy_for_platform", lambda platform: None)

    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        service.load_proxy()
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "proxy" for k, p in evs
        ))
        form = _last_dialog(collector, "proxy")
        assert form["stage"] == "form"
        for platform in PROXY_PLATFORMS:
            assert platform in form

        collector.events.clear()
        values = {platform: "" for platform in PROXY_PLATFORMS}
        values["twitch"] = "127.0.0.1:1"  # 保留端口上不会有服务 → 确定性失败
        service.test_proxy(values)
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "proxy" and p["payload"].get("health")
            for k, p in evs
        ))
        tested = _last_dialog(collector, "proxy")
        assert tested["healthOk"] is False
        by_platform = {item["platform"]: item for item in tested["health"]}
        assert by_platform["youtube"]["status"] == "direct"
        assert by_platform["twitch"]["status"] == "error"

        collector.events.clear()
        service.save_proxy({"twitch": "127.0.0.1:1080"})
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "proxy" and p["ok"]
            and p["payload"].get("close") for k, p in evs
        ))
        assert service._service.cfg.platform_proxies == {"twitch": "127.0.0.1:1080"}
    finally:
        service.stop(timeout=3)
        set_platform_proxies({})
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_import_preview_and_confirm_roundtrip():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        service.preview_import("https://www.twitch.tv/example", "测试标签")
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "import" and p["payload"].get("stage") == "confirm"
            for k, p in evs
        ))
        confirm = _last_dialog(collector, "import")
        assert confirm["canConfirm"] is True
        assert "example" in confirm["previewText"]

        collector.events.clear()
        service.confirm_dialog("import")
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "import" and p["ok"]
            and p["payload"].get("close") for k, p in evs
        ))
        assert len(service._service.cfg.followers) == 2
        assert service._service.cfg.followers[1].url == "https://www.twitch.tv/example"
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


# ---- P3：播放与行操作 ------------------------------------------------------


def _wait_idle(collector):
    return _wait_for(collector, lambda evs: any(
        k == "polling" and not p.get("active") for k, p in evs
    ))


def test_toggle_enabled_roundtrip():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        assert _wait_idle(collector)
        service.toggle_enabled(0)
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "toggle_enabled" and p["ok"]
            for k, p in evs
        ))
        assert service._service.cfg.followers[0].enabled is False

        collector.events.clear()
        service.toggle_enabled(0)
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "toggle_enabled" and p["ok"]
            for k, p in evs
        ))
        assert service._service.cfg.followers[0].enabled is True
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_delete_transaction_roundtrip():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n"
        "true,主播二,LOL,webui_offline,,douyu,https://douyu.com/2,best,\n", collector
    )
    try:
        assert _wait_idle(collector)
        service.preview_delete(0)
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "delete" and p["payload"].get("stage") == "confirm"
            for k, p in evs
        ))
        confirm = _last_dialog(collector, "delete")
        assert "主播一" in confirm["previewText"]

        collector.events.clear()
        service.confirm_dialog("delete")
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "delete" and p["ok"]
            and p["payload"].get("close") for k, p in evs
        ))
        assert len(service._service.cfg.followers) == 1
        assert service._service.cfg.followers[0].name == "主播二"
        # 运行时下标重排：剩余行占用 0 号。
        assert 0 in service._service.followers and 1 not in service._service.followers
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_play_offline_plugin_reports_failure():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        assert _wait_idle(collector)
        collector.events.clear()
        service.play(0)
        assert _wait_for(collector, lambda evs: any(
            k == "log" and "获取直播流失败" in p.get("text", "") for k, p in evs
        ))
        # 失败路径不得进入播放状态。
        assert not any(
            k == "playerState" and p.get("playing") for k, p in collector.events
        )
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_open_web_and_player_control(monkeypatch):
    import webui.service as service_module

    opened: list[str] = []
    monkeypatch.setattr(service_module.webbrowser, "open", lambda url: opened.append(url) or True)

    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        assert _wait_idle(collector)
        service.open_web(0)
        assert _wait_for(collector, lambda evs: any(
            k == "log" and "已在浏览器打开" in p.get("text", "") for k, p in evs
        ))
        assert opened and opened[0].startswith("https://")

        collector.events.clear()
        service.player_control("volume_up")
        assert _wait_for(collector, lambda evs: any(
            k == "log" and "mpv 当前没有正在播放" in p.get("text", "") for k, p in evs
        ))
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_notify_uri_and_dry_run_notifier():
    from webui.notify import Notifier, notify_uri_for, parse_notify_uri

    assert notify_uri_for(7) == "zhibo://play/7"
    assert parse_notify_uri("zhibo://play/12") == 12
    assert parse_notify_uri("zhibo://play/abc") == -1
    assert parse_notify_uri("zhibo://play/") == -1
    assert parse_notify_uri("https://elsewhere/3") == -1

    notifier = Notifier(dry_run=True)
    notifier.notify_live(3, "老苗", "布冬日 vs 维拉")
    notifier.notify_live(4, "某人", "")
    assert notifier.sent[0] == {
        "idx": 3, "name": "老苗", "message": "布冬日 vs 维拉", "launch": "zhibo://play/3",
    }
    assert notifier.sent[1]["message"] == "正在直播"


# ---- P4：更新中心与下载 ------------------------------------------------------


def test_update_center_lists_all_items():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        service.load_update_center()
        assert _wait_for(collector, lambda evs: any(
            k == "dialog" and p["kind"] == "update" and p["payload"].get("stage") == "form"
            for k, p in evs
        ))
        # 打开后自动全检的 checked 补丁会随后到达；条目清单取第一条 form 事件。
        forms = [
            p for k, p in collector.events
            if k == "dialog" and p["kind"] == "update" and p["payload"].get("stage") == "form"
        ]
        payload = forms[0]["payload"]
        values = [item["value"] for item in payload["items"]]
        # python/mpv/ffmpeg/uosc + 三个 PyPI 包 + fs1 + bilibili_cookie
        for expected in (
            "python", "mpv", "ffmpeg", "uosc",
            "streamlink", "streamget", "yt-dlp", "fs1", "bilibili_cookie",
        ):
            assert expected in values
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_run_update_rejects_invalid_target_and_empty_content():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        service.run_update("not-a-target")
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "update" and not p["ok"]
            and "更新目标无效" in p["message"] for k, p in evs
        ))

        collector.events.clear()
        service.run_update("bilibili_cookie", "   ")
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "update" and not p["ok"]
            and "cookies.txt" in p["message"] for k, p in evs
        ))

        collector.events.clear()
        service.run_update("fs1", "")
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "update" and not p["ok"]
            and "curl" in p["message"] for k, p in evs
        ))
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)


def test_start_download_without_formats_fails_cleanly():
    collector = _Collector()
    service, fpath, previous = _started_service(
        "true,主播一,游戏,webui_offline,,douyu,https://douyu.com/1,best,\n", collector
    )
    try:
        service.start_download(0)
        assert _wait_for(collector, lambda evs: any(
            k == "operationFinished" and p["kind"] == "download" and not p["ok"]
            and "已失效" in p["message"] for k, p in evs
        ))
    finally:
        service.stop(timeout=3)
        _plugins.clear()
        _plugins.update(previous)
        Path(fpath).unlink(missing_ok=True)
