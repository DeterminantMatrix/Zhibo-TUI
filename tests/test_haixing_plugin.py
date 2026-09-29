"""海星体育插件测试 — 域名池、降级、提取、画质与配置链路。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from zhibo.plugins import get_plugin
from zhibo.plugins.haixing_plugin import (
    DEFAULT_DOMAINS,
    HaixingPlugin,
    add_domain_to_pool,
    configured_domains,
    extract_play_addr,
    extract_room_detail,
    extract_room_list,
    parse_domain_input,
    parse_room_ref,
    update_domains_from_text,
)


def _room_page(detail: dict | None = None) -> str:
    detail = detail if detail is not None else {
        "status": 1,
        "nickName": "鬼哥侃球",
        "liveTitle": "非洲杯 布隆迪 VS 阿尔及利亚",
        "sportId": 1,
        "matchId": 14866644,
        "leagueId": 309,
        "playAddr": {
            "ori_m3u8": "https://pull.example.top/live/100_ori.m3u8?auth_info=a",
            "ori_flv": "https://pull.example.top/live/100_ori.flv?auth_info=b",
            "ld_m3u8": "https://pull.example.top/live/100_lld.m3u8?auth_info=c",
            "hd_m3u8": "https://pull.example.top/live/100_lhd.m3u8?auth_info=d",
            "hd_flv": "https://pull.example.top/live/100_lhd.flv?auth_info=e",
        },
    }
    payload = {"props": {"pageProps": {"liveDetailProp": detail, "canPlay": True}}}
    return (
        "<html><head>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</head><body>room</body></html>"
    )


def _list_page() -> str:
    payload = {
        "props": {
            "pageProps": {
                "liveAnchorList": {
                    "records": [
                        {
                            "roomId": 1733,
                            "anchorId": 1733,
                            "title": "非洲杯 布隆迪 VS 阿尔及利亚",
                            "nickName": "鬼哥侃球",
                            "liveTypeName": "足球",
                            "anchorHot": 248900,
                            "roomImg": "https://pic.example/a.png",
                            "matchId": 14866644,
                            "leagueId": 309,
                        },
                        {
                            "roomId": 2001,
                            "title": "NBA 湖人 VS 勇士",
                            "nickName": "解说小王",
                            "liveTypeName": "篮球",
                            "anchorHot": 10,
                            "matchId": 999,
                        },
                    ]
                }
            }
        }
    }
    return (
        "<html><head>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</head><body>list</body></html>"
    )


class Router(httpx.MockTransport):
    """按域名路由的假传输层：模拟镜像池里的活/死域名。"""

    def __init__(self, hosts: dict[str, str | Exception]):
        self.hosts = hosts
        self.hits: list[str] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.hits.append(host)
        target = self.hosts.get(host)
        if isinstance(target, Exception):
            raise target
        if target is None:
            return httpx.Response(404)
        return httpx.Response(200, text=target)


def _plugin(tmp_path: Path, router: Router, domains: list[str] | None = None) -> HaixingPlugin:
    config_path = tmp_path / "haixing" / "domains.yaml"
    if domains is not None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump({"config": {"domains": domains}}), encoding="utf-8"
        )
    return HaixingPlugin(config_path=config_path, transport=router)


# ---- 输入解析 ----


def test_parse_room_ref_accepts_id_and_url():
    assert parse_room_ref("1733") == ("1733", "")
    assert parse_room_ref("https://hxty4.com/live/1733") == ("1733", "hxty4.com")
    assert parse_room_ref("https://www.hxty5.com/live/42?x=1") == ("42", "hxty5.com")


def test_parse_room_ref_rejects_foreign_domains_and_junk():
    with pytest.raises(ValueError):
        parse_room_ref("https://evil.example.com/live/1733")
    with pytest.raises(ValueError):
        parse_room_ref("https://hxty4.com/other/1733")
    with pytest.raises(ValueError):
        parse_room_ref("not a room")


def test_parse_domain_input_filters_to_family_and_normalizes():
    raw = "hxty5.com, https://www.haixing4.com/live/1; 337a78.hxty16.com junk.com ftp://x"
    assert parse_domain_input(raw) == ["hxty5.com", "haixing4.com", "hxty16.com"]


# ---- 页面提取 ----


def test_extract_room_detail_and_play_addr_from_next_data():
    html = _room_page()
    detail = extract_room_detail(html)
    assert detail["nickName"] == "鬼哥侃球"
    play_addr = extract_play_addr(html)
    assert play_addr["hd_m3u8"].startswith("https://pull.example.top/live/100_lhd")


def test_extract_play_addr_falls_back_to_brace_matching():
    # NEXT_DATA 缺失/变形时，退回 playAddr 花括号配平提取。
    html = '<html><body><script>var x = {"playAddr": {"hd_m3u8": "https://c/hd.m3u8?x={weird}&y=1", "ld_flv": "https://c/ld.flv"}}</script></body></html>'
    play_addr = extract_play_addr(html)
    assert play_addr["hd_m3u8"] == "https://c/hd.m3u8?x={weird}&y=1"
    assert play_addr["ld_flv"] == "https://c/ld.flv"


def test_extract_room_list_normalizes_records():
    rooms = extract_room_list(_list_page())
    assert [room["room_id"] for room in rooms] == ["1733", "2001"]
    assert rooms[0]["anchor"] == "鬼哥侃球"
    assert rooms[1]["sport"] == "篮球"


# ---- 域名池与降级 ----


def test_check_live_fails_over_to_next_mirror(tmp_path: Path):
    router = Router({
        "hxty4.com": httpx.ConnectTimeout("down"),
        "hxty5.com": _room_page(),
        "haixing4.com": _room_page(),
    })
    plugin = _plugin(tmp_path, router)
    info = asyncio.run(plugin.check_live("1733", quality="hd"))
    assert info.is_live
    assert info.extra["domain"] == "hxty5.com"
    # 失败镜像进入冷却，后续解析直接从健康镜像开始。
    health = plugin.domain_health()
    assert health["hxty4.com"]["cooldown_until"] > 0


def test_cooldown_domain_is_skipped_until_exhausted(tmp_path: Path):
    router = Router({
        "hxty4.com": httpx.ConnectTimeout("down"),
        "hxty5.com": httpx.ConnectTimeout("down"),
        "haixing4.com": _room_page(),
    })
    plugin = _plugin(tmp_path, router)
    asyncio.run(plugin.check_live("1733"))
    router.hits.clear()
    asyncio.run(plugin.check_live("1733"))
    # 两个冷却域名不应再被先尝试。
    assert router.hits[0] == "haixing4.com"


def test_all_mirrors_down_reports_each_domain(tmp_path: Path):
    router = Router({
        "hxty4.com": httpx.ConnectTimeout("a"),
        "hxty5.com": httpx.ConnectTimeout("b"),
        "haixing4.com": httpx.ConnectTimeout("c"),
    })
    plugin = _plugin(tmp_path, router)
    info = asyncio.run(plugin.check_live("1733"))
    assert not info.is_live
    error = info.extra["error"]
    for domain in DEFAULT_DOMAINS:
        assert domain in error


def test_not_live_room_has_no_stream(tmp_path: Path):
    router = Router({"hxty4.com": _room_page(detail={"status": 0, "nickName": "x", "playAddr": {}})})
    plugin = _plugin(tmp_path, router)
    info = asyncio.run(plugin.check_live("1733"))
    assert not info.is_live
    assert not info.stream_url


def test_list_rooms_uses_healthy_mirror(tmp_path: Path):
    router = Router({"hxty4.com": _list_page(), "hxty5.com": _list_page()})
    plugin = _plugin(tmp_path, router, domains=["hxty4.com", "hxty5.com"])
    rooms = asyncio.run(plugin.list_rooms())
    assert rooms[0]["room_id"] == "1733"


# ---- 画质选择 ----


def test_quality_selection_prefers_requested_tier(tmp_path: Path):
    router = Router({"hxty4.com": _room_page()})
    plugin = _plugin(tmp_path, router)
    info = asyncio.run(plugin.check_live("1733", quality="hd"))
    assert info.stream_url.endswith("100_lhd.m3u8?auth_info=d")
    assert info.quality_name == "高清"


def test_quality_selection_falls_back_to_ori_for_best(tmp_path: Path):
    router = Router({"hxty4.com": _room_page()})
    plugin = _plugin(tmp_path, router)
    info = asyncio.run(plugin.check_live("1733", quality="best"))
    assert info.stream_url.endswith("100_ori.m3u8?auth_info=a")
    assert info.quality_name == "原画"


def test_stream_candidates_cover_all_tiers(tmp_path: Path):
    router = Router({"hxty4.com": _room_page()})
    plugin = _plugin(tmp_path, router)
    info = asyncio.run(plugin.check_live("1733", quality="best"))
    candidates = info.extra["stream_candidates"]
    assert candidates[0].endswith("100_ori.m3u8?auth_info=a")
    assert any(c.endswith("100_lld.m3u8?auth_info=c") for c in candidates)
    assert candidates[-1].endswith(".flv?auth_info=b")


def test_flv_fallback_when_tier_m3u8_missing(tmp_path: Path):
    detail = {
        "status": 1,
        "playAddr": {"sd_flv": "https://pull.example.top/live/100_lsd.flv?auth_info=z"},
    }
    router = Router({"hxty4.com": _room_page(detail=detail)})
    plugin = _plugin(tmp_path, router)
    info = asyncio.run(plugin.check_live("1733", quality="sd"))
    assert info.is_live
    assert info.stream_url.endswith("100_lsd.flv?auth_info=z")


def test_get_stream_url_requires_live(tmp_path: Path):
    router = Router({"hxty4.com": _room_page(detail={"status": 0, "playAddr": {}})})
    plugin = _plugin(tmp_path, router)
    with pytest.raises(RuntimeError):
        asyncio.run(plugin.get_stream_url("1733", "best"))


# ---- 配置文件与更新链路 ----


def test_update_domains_from_text_replaces_pool(tmp_path: Path):
    config_path = tmp_path / "haixing" / "domains.yaml"
    applied = update_domains_from_text("hxty9.com https://www.haixing10.com/live/5", config_path)
    assert applied == {"domains": "hxty9.com, haixing10.com"}
    assert configured_domains(config_path) == ["hxty9.com", "haixing10.com"]


def test_update_domains_rejects_non_family_input(tmp_path: Path):
    config_path = tmp_path / "haixing" / "domains.yaml"
    with pytest.raises(ValueError):
        update_domains_from_text("evil.example.com", config_path)
    assert not config_path.exists()


def test_add_domain_to_pool_preserves_existing(tmp_path: Path):
    config_path = tmp_path / "haixing" / "domains.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump({"config": {"domains": ["hxty4.com"]}}), encoding="utf-8"
    )
    add_domain_to_pool("www.hxty7.com", config_path)
    assert configured_domains(config_path) == ["hxty4.com", "hxty7.com"]


def test_check_live_extends_pool_from_url_hint(tmp_path: Path):
    # 关注项里存了完整 URL 时，插件应把该镜像并入域名池。
    router = Router({"hxty7.com": _room_page()})
    plugin = _plugin(tmp_path, router, domains=["hxty4.com"])
    asyncio.run(plugin.check_live("https://hxty7.com/live/1733"))
    assert "hxty7.com" in configured_domains(plugin._config_path)


def test_defaults_when_unconfigured(tmp_path: Path):
    assert configured_domains(tmp_path / "missing.yaml") == list(DEFAULT_DOMAINS)


# ---- 注册与集成 ----


def test_plugin_registered():
    plugin = get_plugin("haixing")
    assert plugin is not None
    assert plugin.name == "haixing"


def test_haixing_quality_options():
    from zhibo.quality_options import quality_options

    options = quality_options("haixing", "haixing")
    values = [item["value"] for item in options]
    assert values == ["best", "ori", "ud", "hd", "sd", "ld"]


def test_quality_for_plugin_translates_to_haixing():
    from zhibo.quality_options import quality_for_plugin

    assert quality_for_plugin("1080p", source_plugin="streamlink", target_plugin="haixing", platform="") == "hd"
    assert quality_for_plugin("OD", source_plugin="streamget", target_plugin="haixing", platform="") == "ori"
    assert quality_for_plugin("ori", source_plugin="haixing", target_plugin="streamlink", platform="") == "best"


def test_config_edit_normalises_haixing_url_to_room_id():
    from zhibo.config import Follower, validate_follower_edit

    follower = Follower(name="海星测试", plugin="haixing", url="https://hxty4.com/live/1733")
    result = validate_follower_edit(
        {"url": "https://www.hxty5.com/live/42"}, current=follower
    )
    assert result.url == "42"
    assert result.platform == "haixing"
