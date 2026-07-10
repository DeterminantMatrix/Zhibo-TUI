from ui.app import COL_WIDTHS, ProxySettingsScreen, ZhiboApp, _display_platform, _split_widths, _update_fs1_script_path, _web_url_for_follower
from models import Follower

def test_fs1_platform_displays_as_feisu():
    assert _display_platform("fs1") == "飞速"
    assert _display_platform("huya") == "huya"


def test_error_column_is_rightmost_and_platform_width_is_narrower():
    columns = list(COL_WIDTHS)

    assert COL_WIDTHS["平台"] == 9
    assert columns[-1] == "错误"


def test_fs1_room_id_opens_broadcast_detail_url(monkeypatch):
    monkeypatch.setattr("ui.app.get_fs_site_url", lambda: "https://www.fszb130.com")

    assert _web_url_for_follower(
        Follower(
            name="test",
            plugin="fs1",
            platform="fs1",
            url="380348943",
            extra={"sport_id": "1"},
        )
    ) == "https://www.fszb130.com/broadcast/details?room_id=380348943&sport_id=1"


def test_update_fs1_shortcut_target_exists():
    keys = {binding.key for binding in ZhiboApp.BINDINGS}

    assert "c" in keys
    assert "u" in keys
    assert "p" in keys
    assert _update_fs1_script_path().exists()


def test_proxy_settings_supports_twitch_and_youtube():
    assert "twitch" in ProxySettingsScreen.PLATFORM_LABELS
    assert "youtube" in ProxySettingsScreen.PLATFORM_LABELS


def test_split_widths_preserves_minimum_width_for_both_panes():
    assert _split_widths(100, 0.75) == (75, 25)
    assert _split_widths(100, 0.99) == (76, 24)
    assert _split_widths(100, 0.01) == (24, 76)
