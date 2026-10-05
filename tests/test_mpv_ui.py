import json
import zipfile

from zhibo import mpv_ui
def _release(version="5.12.0"):
    return {
        "tag_name": version,
        "assets": [
            {
                "name": "uosc.conf",
                "size": 100,
                "digest": "sha256:" + "a" * 64,
                "browser_download_url": "https://example.test/uosc.conf",
            },
            {
                "name": "uosc.zip",
                "size": 1024,
                "digest": "sha256:" + "b" * 64,
                "browser_download_url": "https://example.test/uosc.zip",
            },
        ],
    }


def test_uosc_check_reports_install_and_small_download(monkeypatch, tmp_path):
    monkeypatch.setattr(mpv_ui, "uosc_config_dir", lambda: tmp_path / "uosc")
    monkeypatch.setattr(mpv_ui, "_read_json", lambda _url: _release())

    plan = mpv_ui.check_uosc_update()
    fields = mpv_ui.uosc_plan_ui(plan)

    assert plan.status == "install"
    assert plan.remote_version == "5.12.0"
    assert plan.download_size == 1124
    assert fields["actionLabel"] == "安装"
    assert fields["actionEnabled"] is True


def test_uosc_install_is_atomic_and_creates_complete_private_config(monkeypatch, tmp_path):
    target = tmp_path / "mpv-ui" / "uosc"
    monkeypatch.setattr(mpv_ui, "uosc_config_dir", lambda: target)
    plan = mpv_ui.UoscUpdatePlan(
        "未安装",
        "5.12.0",
        "install",
        tuple(_release()["assets"]),
    )

    def fake_download(asset, destination, progress, *, max_size):
        if asset["name"] == "uosc.conf":
            destination.write_text("controls=menu,play-pause\n", encoding="utf-8")
        else:
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("scripts/uosc/main.lua", "-- uosc")
                archive.writestr("scripts/uosc/bin/ziggy-windows.exe", b"exe")
                archive.writestr("fonts/uosc_icons.otf", b"font")
                archive.writestr("fonts/uosc_textures.ttf", b"font")
        progress(50, f"downloaded {asset['name']}")

    monkeypatch.setattr(mpv_ui, "_download", fake_download)
    messages = []

    version = mpv_ui.install_uosc(
        lambda value, message: messages.append((value, message)),
        plan=plan,
    )

    assert version == "5.12.0"
    assert mpv_ui.active_uosc_config_dir() == target
    assert (target / "scripts/uosc/main.lua").is_file()
    assert (target / "script-opts/uosc.conf").read_text(encoding="utf-8").startswith("controls=")
    assert "osc=no" in (target / "mpv.conf").read_text(encoding="utf-8")
    assert "uosc/menu" in (target / "input.conf").read_text(encoding="utf-8")
    assert json.loads((target / mpv_ui.MARKER_NAME).read_text(encoding="utf-8"))["version"] == "5.12.0"
    assert messages[-1][0] == 100


def test_incomplete_uosc_install_is_repairable(monkeypatch, tmp_path):
    target = tmp_path / "uosc"
    target.mkdir()
    monkeypatch.setattr(mpv_ui, "uosc_config_dir", lambda: target)
    monkeypatch.setattr(mpv_ui, "_read_json", lambda _url: _release())

    plan = mpv_ui.check_uosc_update()

    assert plan.installed_version == "安装不完整"
    assert plan.status == "repair"
    assert plan.action_allowed is True


def test_sweep_uosc_leftovers_recovers_and_cleans(monkeypatch, tmp_path):
    monkeypatch.setattr(mpv_ui, "uosc_config_dir", lambda: tmp_path / "uosc")
    backup = tmp_path / ".uosc-previous"
    backup.mkdir()
    (backup / "script-opts").mkdir()
    (tmp_path / ".uosc-install-x").mkdir()

    cleaned = mpv_ui.sweep_uosc_leftovers()

    assert (tmp_path / "uosc" / "script-opts").exists()
    assert not backup.exists()
    assert not (tmp_path / ".uosc-install-x").exists()
    assert cleaned
