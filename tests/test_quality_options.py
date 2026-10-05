from zhibo.quality_options import quality_for_plugin, quality_options


def test_quality_options_are_plugin_specific_and_default_to_best():
    streamlink = quality_options("streamlink", "twitch")
    streamget = quality_options("streamget", "twitch")

    assert streamlink[0] == {"label": "最优画质", "value": "best"}
    assert streamget[0] == {"label": "最优画质", "value": "best"}
    assert {item["value"] for item in streamlink} == {"best", "1080p", "720p", "worst"}
    assert "UHD" in {item["value"] for item in streamget}
    assert {item["value"] for item in streamget} == {"best", "UHD", "HD", "LD"}


def test_bilibili_and_fs1_expose_platform_native_choices():
    bilibili = quality_options("streamlink", "bilibili")
    fs1 = quality_options("fs1", "fs1")

    assert {item["value"] for item in bilibili} == {"best", "蓝光", "高清", "流畅"}
    assert {item["value"] for item in fs1} == {"best", "lgzm", "gqzm", "bqzm"}


def test_quality_translation_keeps_streamlink_and_streamget_fallbacks_compatible():
    assert quality_for_plugin(
        "720p60", source_plugin="streamlink", target_plugin="streamget", platform="twitch"
    ) == "HD"
    assert quality_for_plugin(
        "UHD", source_plugin="streamget", target_plugin="streamlink", platform="twitch"
    ) == "1080p"
    assert quality_for_plugin(
        "AD", source_plugin="streamget", target_plugin="streamlink", platform="twitch"
    ) == "audio_only"
    assert quality_for_plugin(
        "蓝光", source_plugin="streamget", target_plugin="streamlink", platform="bilibili"
    ) == "蓝光"


def test_existing_custom_quality_is_preserved_in_dropdown():
    options = quality_options("custom_plugin", "example", "custom-tier")
    assert options[1] == {"label": "当前选择（custom-tier）", "value": "custom-tier"}
    assert len(options) <= 4


def test_streamlink_plugin_native_current_value_does_not_flood_compact_menu():
    options = quality_options("streamlink", "twitch", "720p_alt")
    assert len(options) == 4
    assert "720p_alt" in {item["value"] for item in options}
