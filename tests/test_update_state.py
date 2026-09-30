import json
from io import BytesIO

from zhibo import update_state
def test_update_items_report_installed_versions_and_no_guessed_time(tmp_path, monkeypatch):
    versions = {"streamlink": "9.1.2", "streamget": "5.0.0", "yt-dlp": "2026.7.1"}
    monkeypatch.setattr(update_state, "installed_version", lambda name: versions[name])
    monkeypatch.setattr(update_state, "tool_version", lambda name: "v0.41" if name == "mpv" else "未安装")
    monkeypatch.setattr(update_state, "managed_executable", lambda _name: None)
    monkeypatch.setattr(update_state, "find_tool", lambda name: name if name == "mpv" else None)
    # update_items 按需导入 mpv_ui.uosc_installed_version，必须一并隔离，
    # 否则在本机已安装 uosc 时该测试会误报。
    monkeypatch.setattr("zhibo.mpv_ui.uosc_installed_version", lambda: "未安装")

    items = update_state.update_items(tmp_path / "missing.json")

    by_target = {item["value"]: item for item in items}
    assert by_target["streamlink"]["version"] == "9.1.2"
    assert by_target["streamlink"]["lastUpdated"] == "无程序内更新记录"
    assert by_target["streamlink"]["actionLabel"] == "检查更新"
    assert by_target["streamlink"]["actionEnabled"] is True
    assert by_target["streamlink"]["updateStatus"] == "unchecked"
    assert by_target["mpv"]["actionLabel"] == "检查更新"
    assert by_target["mpv"]["actionEnabled"] is True
    assert by_target["ffmpeg"]["actionLabel"] == "未安装"
    assert by_target["ffmpeg"]["updateStatus"] == "unchecked"
    assert by_target["ffmpeg"]["installed"] is False
    assert by_target["uosc"]["actionLabel"] == "未安装"
    assert by_target["uosc"]["actionKind"] == "check"
    assert by_target["fs1"]["version"] == "内置配置适配器"
    assert by_target["bilibili_cookie"]["version"] == "本地凭据"


def test_package_update_check_compares_pypi_version(monkeypatch):
    monkeypatch.setattr(update_state, "installed_version", lambda _name: "8.4.0")
    monkeypatch.setattr(
        update_state.urllib.request,
        "urlopen",
        lambda _request, timeout: BytesIO(json.dumps({"info": {"version": "8.5.0"}}).encode()),
    )

    plan = update_state.check_package_update("streamlink")
    fields = update_state.package_plan_ui(plan)

    assert plan.status == "update"
    assert plan.remote_version == "8.5.0"
    assert fields["actionLabel"] == "更新"
    assert fields["actionKind"] == "execute"


def test_package_update_failure_becomes_safe_recheck_action(monkeypatch):
    monkeypatch.setattr(update_state, "installed_version", lambda _name: "8.4.0")
    monkeypatch.setattr(
        update_state.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    plan = update_state.check_package_update("streamlink")
    fields = update_state.package_plan_ui(plan)

    assert plan.status == "unknown"
    assert fields["actionLabel"] == "重新检查"
    assert fields["actionEnabled"] is True
    assert fields["actionKind"] == "recheck"


def test_successful_update_history_is_atomic_and_visible(tmp_path, monkeypatch):
    path = tmp_path / "update-history.json"
    monkeypatch.setattr(update_state, "installed_version", lambda _name: "8.5.0")
    monkeypatch.setattr(update_state, "tool_version", lambda _name: "未安装")
    monkeypatch.setattr(update_state, "managed_executable", lambda _name: None)
    monkeypatch.setattr(update_state, "find_tool", lambda _name: None)

    update_state.record_successful_update("streamlink", "8.5.0", path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["streamlink"]["version"] == "8.5.0"
    item = next(item for item in update_state.update_items(path) if item["value"] == "streamlink")
    assert item["lastVersion"] == "8.5.0"
    assert item["lastUpdated"] != "无程序内更新记录"


def test_runtime_environment_reports_player_download_and_plugins(monkeypatch):
    monkeypatch.setattr(update_state, "tool_version", lambda name: "v0.41.0" if name == "mpv" else "ffmpeg version 7.1")
    monkeypatch.setattr(update_state, "installed_version", lambda _name: "1.0")

    environment = {item["label"]: item for item in update_state.runtime_environment()}

    assert environment["MPV 播放器"]["tone"] == "ok"
    assert environment["FFmpeg"]["detail"] == "支持下载合并"
    assert environment["解析组件"]["value"] == "3/3 可用"
