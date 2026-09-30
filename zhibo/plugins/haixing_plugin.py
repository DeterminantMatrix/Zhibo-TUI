"""海星体育插件 — 多镜像域名池 + SSR 页面数据提取。

上游是白牌体育直播平台的"海星体育"皮，域名按编号家族轮换
（hxty4.com / hxty5.com / haixing4.com …），但同一时刻总有多个镜像
存活且房间号互通。因此本插件不做单域名配置，而是维护一个带健康
状态机的镜像池：解析请求按序尝试，失败自动降级到下一镜像并进入
指数退避冷却，成功即复位。

数据通路只有一条原语：``GET https://{domain}/live/{room_id}``，房间
信息（含 5 档画质的 m3u8/flv 直链）在服务端渲染的 ``__NEXT_DATA__``
JSON 里，无需登录、无需 Cookie、无需执行 JS。为对页面结构微调保持
容错，``__NEXT_DATA__`` 解析失败时回退到花括号配平的 ``playAddr``
正则提取（与社区已验证的 video-proxy 方案同型）。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

try:
    import truststore
except ImportError:  # pragma: no cover - exercised by minimal installations
    truststore = None

from zhibo.private_data import ensure_private_parent, haixing_config_path

from .base import LiveStreamPlugin, LiveInfo

CONFIG_PATH = haixing_config_path()

# 已验证存活的 SSR 镜像（2026-09 实测：房间号互通、结构一致）。
DEFAULT_DOMAINS = ("hxty4.com", "hxty5.com", "haixing4.com")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 域名家族正则：hxty{N}.com / haixing{N}.com（可带子域前缀，写入配置
# 时会归一化掉前缀）。接受范围保持窄：房间页请求虽不携带任何凭据，
# 也不允许把插件指向任意第三方主机。
DOMAIN_FAMILY_RE = re.compile(r"(?:[a-z0-9-]+\.)*(?:hxty|haixing)\d+\.com$", re.I)
ROOM_URL_RE = re.compile(r"^https?://([^/?#]+)/live/(\d{1,12})(?:[/?#].*)?$", re.I)
ROOM_ID_RE = re.compile(r"^\d{1,12}$")
NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)

# 画质档位：ori 原画 / ud 超清 / hd 高清 / sd 标清 / ld 流畅。
QUALITY_TIERS = ("ori", "ud", "hd", "sd", "ld")

PAGE_TIMEOUT_SECONDS = 12.0
# 单次解析最多尝试的镜像数：3 个 × 12s 超时恰好落在监控 45s 预算内。
MAX_DOMAIN_ATTEMPTS = 3
FAIL_COOLDOWN_BASE_SECONDS = 60.0
FAIL_COOLDOWN_MAX_SECONDS = 1800.0


def _verify_context() -> bool | ssl.SSLContext:
    """Use the Windows/native trust store while keeping TLS verification on.

    镜像站与拉流域名的证书链在部分系统上只有 OS 信任库能补全（certifi
    会报 unable to get local issuer certificate）；truststore 沿用系统库
    且不降低校验强度。
    """
    if truststore is None:
        return True
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def registrable_domain(host: str) -> str:
    """Strip subdomain prefixes: ``337a78.hxty16.com`` → ``hxty16.com``."""
    labels = [p for p in str(host or "").strip().strip(".").split(".") if p]
    if len(labels) < 2:
        return ""
    return ".".join(labels[-2:])


def is_trusted_domain(value: str) -> bool:
    host = str(value or "").strip()
    if not host:
        return False
    try:
        parsed = urlsplit(f"https://{host}")
    except ValueError:
        return False
    candidate = parsed.hostname or ""
    return bool(DOMAIN_FAMILY_RE.fullmatch(candidate))


def is_haixing_site_url(value: str) -> bool:
    """True when ``value`` is an HTTPS URL on a known Haixing mirror family."""
    text = str(value or "").strip()
    if not text.startswith(("https://", "http://")):
        return False
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return False
    return is_trusted_domain(host)


def parse_domain_input(raw: str) -> list[str]:
    """Extract validated mirror domains from free-form text.

    Accepts bare domains or full URLs (one per token, whitespace/comma
    separated). Subdomain prefixes are normalized away; anything outside
    the hxty/haixing numbered families is ignored.
    """
    results: list[str] = []
    seen: set[str] = set()
    tokens = re.split(r"[\s,;，；]+", str(raw or ""))
    for token in tokens:
        token = token.strip().strip("<>()\"'")
        if not token:
            continue
        if "://" in token:
            try:
                host = urlsplit(token).hostname or ""
            except ValueError:
                continue
        else:
            host = token.split("/")[0].split("?")[0]
        domain = registrable_domain(host)
        if domain and domain not in seen and is_trusted_domain(domain):
            seen.add(domain)
            results.append(domain)
    return results


def parse_room_ref(value: str) -> tuple[str, str]:
    """Normalize a room reference to ``(room_id, domain_hint)``.

    Accepts a bare numeric room id or a full ``/live/{id}`` URL. The domain
    from a URL is only a hint: it must still pass family validation before
    being used, and room ids are portable across mirrors anyway.
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("海星体育房间号不能为空")
    if ROOM_ID_RE.fullmatch(text):
        return text, ""
    match = ROOM_URL_RE.match(text)
    if match:
        domain = registrable_domain(match.group(1))
        if not is_trusted_domain(domain):
            raise ValueError(f"不是海星体育域名: {domain}")
        return match.group(2), domain
    raise ValueError("无法识别的海星体育房间地址（应为房间号或 /live/房间号 链接）")


def _extract_balanced_object(text: str, start: int) -> str:
    """Return the JSON object source starting at ``text[start] == '{'``.

    Brace matching ignores braces inside JSON string literals, so embedded
    URLs or titles can never unbalance the scan.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def extract_play_addr(html: str) -> dict[str, Any]:
    """Pull the ``playAddr`` mapping out of a rendered room page.

    Primary path: parse ``__NEXT_DATA__`` and walk to
    ``props.pageProps.liveDetailProp.playAddr``. Fallback path: locate the
    ``"playAddr"`` key and brace-match its object — survives changes to the
    surrounding Next.js payload shape.
    """
    match = NEXT_DATA_RE.search(html)
    if match:
        try:
            data = json.loads(match.group(1))
            detail = (
                ((data.get("props") or {}).get("pageProps") or {})
                .get("liveDetailProp")
            )
            if isinstance(detail, dict):
                play_addr = detail.get("playAddr")
                if isinstance(play_addr, dict) and play_addr:
                    return play_addr
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    for key_match in re.finditer(r'"playAddr"\s*:\s*', html):
        brace_at = html.find("{", key_match.end())
        if brace_at < 0:
            continue
        source = _extract_balanced_object(html, brace_at)
        if not source:
            continue
        try:
            parsed = json.loads(source)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return {}


def extract_room_detail(html: str) -> dict[str, Any]:
    """Return ``liveDetailProp`` (may be empty) from a room page."""
    match = NEXT_DATA_RE.search(html)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    detail = (
        ((data.get("props") or {}).get("pageProps") or {}).get("liveDetailProp")
    )
    return detail if isinstance(detail, dict) else {}


def extract_room_list(html: str) -> list[dict[str, Any]]:
    """Return normalized anchor rooms from the ``/live`` list page."""
    match = NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    page_props = (data.get("props") or {}).get("pageProps") or {}
    anchor_list = page_props.get("liveAnchorList") or {}
    records = anchor_list.get("records") if isinstance(anchor_list, dict) else None
    rooms: list[dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        room_id = str(record.get("roomId") or record.get("anchorId") or "").strip()
        if not room_id:
            continue
        rooms.append(
            {
                "room_id": room_id,
                "title": str(record.get("title") or ""),
                "anchor": str(record.get("nickName") or ""),
                "sport": str(record.get("liveTypeName") or ""),
                "hot": int(record.get("anchorHot") or 0),
                "image": str(record.get("roomImg") or ""),
                "match_id": str(record.get("matchId") or ""),
                "league_id": int(record.get("leagueId") or 0),
            }
        )
    return rooms


def _load_config(path: Path | None = None) -> dict:
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:
        return {}
    config = data.get("config")
    return config if isinstance(config, dict) else {}


def _write_config_atomically(path: Path, config: dict) -> None:
    ensure_private_parent(path)
    payload = {"config": config}
    directory = path.parent
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(directory)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _sanitize_domains(values: Any) -> list[str]:
    if isinstance(values, str):
        candidates = [values]
    elif isinstance(values, (list, tuple)):
        candidates = [str(item) for item in values]
    else:
        candidates = []
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        domain = registrable_domain(candidate)
        if domain and domain not in seen and is_trusted_domain(domain):
            seen.add(domain)
            result.append(domain)
    return result


def update_domains_from_text(raw: str, path: Path | None = None) -> dict[str, str]:
    """Replace the mirror pool from pasted text; returns applied values."""
    domains = parse_domain_input(raw)
    if not domains:
        raise ValueError("没有识别到有效的海星体育域名（应为 hxty数字.com / haixing数字.com）")
    config_path = path or CONFIG_PATH
    config = dict(_load_config(config_path))
    config["domains"] = domains
    _write_config_atomically(config_path, config)
    return {"domains": ", ".join(domains)}


def add_domain_to_pool(domain: str, path: Path | None = None) -> dict[str, str]:
    """Append one validated domain to the pool (keeps existing order)."""
    normalized = registrable_domain(domain)
    if not is_trusted_domain(normalized):
        raise ValueError(f"不是海星体育域名: {normalized}")
    config_path = path or CONFIG_PATH
    config = dict(_load_config(config_path))
    domains = _sanitize_domains(config.get("domains")) or list(DEFAULT_DOMAINS)
    if normalized not in domains:
        domains.append(normalized)
    config["domains"] = domains
    _write_config_atomically(config_path, config)
    return {"domains": ", ".join(domains)}


def configured_domains(path: Path | None = None) -> list[str]:
    """Return the effective mirror pool (defaults when unconfigured)."""
    config = _load_config(path)
    domains = _sanitize_domains(config.get("domains"))
    return domains or list(DEFAULT_DOMAINS)


class HaixingPlugin(LiveStreamPlugin):
    name = "haixing"

    def __init__(self, config_path: Path | None = None, transport: Any = None):
        self._config_path = config_path or CONFIG_PATH
        # transport 仅测试注入（httpx.MockTransport）。
        self._transport = transport
        self._health: dict[str, dict[str, float]] = {}
        self._health_lock = threading.Lock()
        self.reload_config()

    def reload_config(self) -> None:
        """Reload the local domain pool without restarting the app."""
        config = _load_config(self._config_path)
        self._cfg = config
        self._domains = _sanitize_domains(config.get("domains")) or list(DEFAULT_DOMAINS)
        self._user_agent = str(config.get("user_agent") or DEFAULT_USER_AGENT).strip()
        self._proxy_url = (str(config.get("proxy") or "").strip() or None)
        # 本地配置不能降低 TLS 校验强度；仅切换到系统信任库。
        self._verify_ssl = _verify_context()

    # ---- 域名池健康状态机 ----

    def _ordered_domains(self, hint_domain: str = "") -> list[str]:
        """Mirrors ordered for attempting: hint first, then fresh, cooled last."""
        now = time.monotonic()
        with self._health_lock:
            cooled = {
                domain for domain, state in self._health.items()
                if state.get("cooldown_until", 0.0) > now
            }
        ordered: list[str] = []
        if hint_domain and hint_domain in self._domains:
            ordered.append(hint_domain)
        ordered.extend(d for d in self._domains if d not in ordered and d not in cooled)
        ordered.extend(d for d in self._domains if d not in ordered)
        return ordered[:MAX_DOMAIN_ATTEMPTS]

    def _mark_success(self, domain: str) -> None:
        with self._health_lock:
            self._health.pop(domain, None)

    def _mark_failure(self, domain: str) -> None:
        with self._health_lock:
            state = self._health.setdefault(domain, {"fails": 0.0, "cooldown_until": 0.0})
            state["fails"] = state.get("fails", 0.0) + 1
            cooldown = min(
                FAIL_COOLDOWN_BASE_SECONDS * (2 ** max(0, int(state["fails"]) - 1)),
                FAIL_COOLDOWN_MAX_SECONDS,
            )
            state["cooldown_until"] = time.monotonic() + cooldown

    def domain_health(self) -> dict[str, dict[str, float]]:
        with self._health_lock:
            return {domain: dict(state) for domain, state in self._health.items()}

    # ---- HTTP ----

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={
                "User-Agent": self._user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=PAGE_TIMEOUT_SECONDS,
            follow_redirects=True,
            verify=self._verify_ssl,
            proxy=self._proxy_url,
            transport=self._transport,
        )

    async def _fetch_room_html(self, domain: str, room_id: str) -> str:
        url = f"https://{domain}/live/{room_id}"
        async with self._client() as client:
            resp = await client.get(url, headers={"Referer": f"https://{domain}/live"})
            resp.raise_for_status()
            return resp.text

    # ---- 解析 ----

    async def _resolve_room(self, room_id: str, hint_domain: str = "") -> tuple[str, dict[str, Any]]:
        errors: list[str] = []
        for domain in self._ordered_domains(hint_domain):
            try:
                html = await self._fetch_room_html(domain, room_id)
            except Exception as exc:
                self._mark_failure(domain)
                errors.append(f"{domain}: {type(exc).__name__} {exc}".strip())
                continue
            detail = extract_room_detail(html)
            play_addr = extract_play_addr(html)
            if not detail and not play_addr:
                # 页面返回了但不是房间数据（域名家族指向了 H5 壳等）。
                self._mark_failure(domain)
                errors.append(f"{domain}: 页面没有房间数据")
                continue
            if not play_addr:
                play_addr = detail.get("playAddr") or {}
            self._mark_success(domain)
            return domain, {**detail, "playAddr": play_addr}
        raise RuntimeError("海星体育全部镜像域名失败: " + "; ".join(errors))

    def _select_stream(self, play_addr: dict[str, Any], quality: str) -> tuple[str, str, str]:
        """Pick ``(m3u8, flv, tier)`` for the requested quality tier."""
        priority = [str(quality or "").strip().casefold()] + list(QUALITY_TIERS)
        seen: set[str] = set()
        for tier in priority:
            if not tier or tier in seen:
                continue
            seen.add(tier)
            m3u8 = str(play_addr.get(f"{tier}_m3u8") or "").strip()
            flv = str(play_addr.get(f"{tier}_flv") or "").strip()
            if m3u8 or flv:
                return m3u8, flv, tier
        return "", "", ""

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        try:
            room_id, hint_domain = parse_room_ref(url)
        except ValueError as exc:
            return LiveInfo(is_live=False, extra={"error": str(exc)})

        # 关注项里存的可能是完整 URL；提取出的镜像域名顺手扩池。
        if hint_domain and hint_domain not in self._domains:
            try:
                add_domain_to_pool(hint_domain, self._config_path)
                self.reload_config()
            except (ValueError, OSError):
                pass

        quality = str(kwargs.get("quality") or "best")
        try:
            domain, detail = await self._resolve_room(room_id, hint_domain)
        except Exception as exc:
            return LiveInfo(is_live=False, extra={"error": str(exc)})

        play_addr = detail.get("playAddr") or {}
        tier_quality = quality if quality in QUALITY_TIERS else ""
        m3u8, flv, tier = self._select_stream(play_addr, tier_quality or "ori")
        # status==1 为开播中；回退提取拿不到 status 时以是否有流为准，
        # 避免页面结构微调把在播房间误判成未开播。
        status = detail.get("status")
        status_unknown = status is None or status == ""
        is_live = bool(m3u8 or flv) and (status == 1 or status_unknown)

        candidates: list[str] = []
        for candidate_tier in QUALITY_TIERS:
            value = str(play_addr.get(f"{candidate_tier}_m3u8") or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        if flv and flv not in candidates:
            candidates.append(flv)

        anchor_name = str(detail.get("nickName") or detail.get("nickname") or "")
        title = str(detail.get("liveTitle") or detail.get("title") or "")
        return LiveInfo(
            is_live=is_live,
            anchor_name=anchor_name,
            title=title,
            stream_url=m3u8 or flv,
            m3u8_url=m3u8,
            flv_url=flv,
            quality_name={"ori": "原画", "ud": "超清", "hd": "高清", "sd": "标清", "ld": "流畅"}.get(tier, tier),
            extra={
                "room_id": room_id,
                "domain": domain,
                "sport_id": str(detail.get("sportId") or ""),
                "match_id": str(detail.get("matchId") or ""),
                "league_id": detail.get("leagueId") or 0,
                "stream_candidates": candidates,
                "headers": {
                    "User-Agent": self._user_agent,
                    "Referer": f"https://{domain}/",
                },
            },
        )

    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str:
        info = await self.check_live(url, quality=quality, **kwargs)
        if not info.is_live:
            raise RuntimeError("主播未开播" if not info.extra.get("error") else info.extra["error"])
        if not info.stream_url:
            raise RuntimeError("无法获取流地址")
        return info.stream_url

    # ---- 列表浏览（浏览页 / M3U 桥接用） ----

    async def list_rooms(self, live_type: int = 0, hint_domain: str = "") -> list[dict[str, Any]]:
        """List live anchor rooms; ``live_type`` 1=足球 2=篮球 5=网球 7=电竞."""
        query = f"?type={int(live_type)}" if live_type else ""
        errors: list[str] = []
        for domain in self._ordered_domains(hint_domain):
            url = f"https://{domain}/live{query}"
            try:
                async with self._client() as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    html = resp.text
            except Exception as exc:
                self._mark_failure(domain)
                errors.append(f"{domain}: {type(exc).__name__} {exc}".strip())
                continue
            rooms = extract_room_list(html)
            if not rooms:
                self._mark_failure(domain)
                errors.append(f"{domain}: 列表页没有数据")
                continue
            self._mark_success(domain)
            return rooms
        raise RuntimeError("海星体育全部镜像域名失败: " + "; ".join(errors))


from . import register_plugin  # noqa: E402

register_plugin(HaixingPlugin())
