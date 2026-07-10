"""飞速直播插件与配置更新工具。"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from Crypto.Cipher import AES

from .base import LiveStreamPlugin, LiveInfo

SCRIPT_DIR = Path(__file__).parent.parent
ROOMS_CONFIG = SCRIPT_DIR / "sports" / "rooms.yaml"
DEFAULT_ROOMS_CONFIG = ROOMS_CONFIG
DEFAULT_SITE_URL = "https://www.fszb130.com"
DEFAULT_PLAY_API_URL = "https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url"
DEFAULT_PLAY_PATH = "/v230/play/url"
QUALITY_PRIORITY = ["lgzm", "gqzm", "bqzm"]
FS_DOMAIN_RE = re.compile(r"(?:[a-z0-9-]+\.)*fszb\d+\.com$", re.I)

_SIGN_SECRET_MASK = [
    35, 17, 24, 55, 106, 42, 17, 22, 62, 12, 57, 29, 56, 52, 47, 110,
    2, 29, 53, 52, 107, 105, 14, 41, 35, 24, 62, 31, 41, 48, 48, 105,
    13, 12, 27, 32, 41, 32, 42, 53, 43, 48, 52, 105, 24, 20, 55, 53,
    44, 22, 61, 32, 44, 57, 8, 14, 34, 30, 107, 13, 63, 35, 109, 11,
    11, 107, 106, 49, 57, 53, 44, 106, 56, 98, 63, 99, 53, 24, 51,
    109, 48, 27, 15, 8,
]
_AES_KEY_MASK = [
    48, 105, 11, 42, 43, 105, 24, 13, 41, 108, 43, 15, 25, 57, 46, 55,
    18, 10, 22, 20, 25, 24, 28, 55, 50, 15, 3, 15, 15, 46, 31, 0,
    47, 47, 43, 10, 3, 46, 46, 13, 35, 42, 55, 40, 8, 48, 46, 41,
    45, 10, 49, 61, 43, 29, 24, 51, 111, 11, 27, 59, 50, 17, 22, 22,
]
_AES_IV_MASK = [56, 104, 55, 62, 31, 31, 3, 56, 13, 107, 43, 42, 40, 28, 41, 61]


def _unmask(values: list[int]) -> str:
    return "".join(chr(90 ^ value) for value in values)


def sign_play_params(params: dict[str, Any]) -> str:
    secret = _unmask(_SIGN_SECRET_MASK)
    payload = "".join(key + str(params[key]) for key in sorted(params)) + secret
    return hashlib.md5(payload.encode()).hexdigest()


def decode_play_response(body: Any) -> dict:
    if isinstance(body, dict):
        return body
    if not isinstance(body, str):
        raise ValueError("unexpected play-url response")

    key = _unmask(_AES_KEY_MASK)[:16].encode("latin1")
    iv = _unmask(_AES_IV_MASK).encode("latin1")
    decrypted = AES.new(key, AES.MODE_CBC, iv).decrypt(base64.b64decode(body))
    pad = decrypted[-1]
    if pad < 1 or pad > 16:
        raise ValueError("invalid play-url padding")
    return json.loads(decrypted[:-pad].decode("utf-8"))


async def fetch_play_url(
    client: httpx.AsyncClient,
    play_api_url: str,
    room_id: str,
    code_id: str,
    *,
    match_id: str | int | None = None,
    sport_id: str | int | None = None,
) -> tuple[str, int]:
    params: dict[str, Any] = {
        "room_id": room_id,
        "code_id": code_id,
        "time": int(time.time()),
    }
    if str(room_id) == "888888888":
        if match_id:
            params["match_id"] = match_id
        if sport_id:
            params["sport_id"] = sport_id

    params["signature"] = sign_play_params(params)
    resp = await client.post(play_api_url, data=params)
    resp.raise_for_status()
    body = decode_play_response(resp.json())
    if body.get("code") != 200:
        raise ValueError(body.get("message") or f"play-url API error: {body.get('code')}")

    data = body.get("data") or {}
    return data.get("play_url", "") or "", int(data.get("expire_ts") or 0)


def _normalize_curl(raw: str) -> str:
    return raw.replace("\\\r\n", " ").replace("\\\n", " ").replace("`\r\n", " ").replace("`\n", " ").strip()


def parse_curl(raw: str) -> dict:
    """Extract FS config fields from a copied curl command."""
    parts = shlex.split(_normalize_curl(raw), posix=True)
    if not parts or parts[0].casefold() != "curl":
        raise ValueError("请输入以 curl 开头的请求")

    url = ""
    headers: dict[str, str] = {}
    i = 1
    while i < len(parts):
        part = parts[i]
        if part in {"-H", "--header"} and i + 1 < len(parts):
            key, _, value = parts[i + 1].partition(":")
            if key and _:
                headers[key.strip().casefold()] = value.strip()
            i += 2
            continue
        if not part.startswith("-") and not url:
            url = part
        i += 1

    if not url:
        raise ValueError("curl 中没有找到请求 URL")

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("curl 中的请求 URL 无效")

    config: dict[str, str] = {"api_url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}"}

    site_url = _site_url_from_headers(headers)
    if site_url:
        config["site_url"] = site_url

    field_map = {
        "api-version": "api_version",
        "authorization": "token",
        "imei": "imei",
        "dun-imei": "dun_imei",
        "user-agent": "user_agent",
        "version": "version",
    }
    for header_name, config_name in field_map.items():
        value = headers.get(header_name, "")
        if value:
            config[config_name] = value

    return config


def _site_url_from_headers(headers: dict[str, str]) -> str:
    for key in ("origin", "referer"):
        value = headers.get(key, "").rstrip("/")
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _host_from_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0].casefold()
    return host


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _normalize_site_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        value = DEFAULT_SITE_URL
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.netloc:
        return DEFAULT_SITE_URL
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def get_fs_site_url(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else _load_fs_config()
    return _normalize_site_url(str((cfg or {}).get("site_url") or DEFAULT_SITE_URL))


def is_fs_site_url(url: str, cfg: dict | None = None) -> bool:
    host = _host_from_url(url)
    if not host:
        return False

    configured_host = _host_from_url(get_fs_site_url(cfg))
    if configured_host:
        host_root = _strip_www(host)
        configured_root = _strip_www(configured_host)
        if host_root == configured_root or host.endswith("." + configured_root):
            return True

    return bool(FS_DOMAIN_RE.fullmatch(host))


def _configured_proxy(cfg: dict | None = None) -> str | None:
    cfg = cfg or {}
    proxy_url = (cfg.get("fs_proxy_url") or "").strip()
    return proxy_url or None


def _as_bool(value, default: bool = True) -> bool:
    """Parse optional YAML values without silently disabling TLS."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def discover_play_api_url(site_url: str, timeout: float = 20, proxy_url: str | None = None) -> str:
    """Best-effort discovery of the current baseImApi play endpoint from FS frontend assets."""
    if not site_url:
        return ""

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        verify=False,
        headers={"User-Agent": "Mozilla/5.0"},
        proxy=proxy_url,
    ) as client:
        html = client.get(site_url.rstrip("/") + "/").text
        js_urls = _extract_script_urls(html, site_url)
        for js_url in js_urls:
            try:
                text = client.get(js_url).text
            except Exception:
                continue
            play_api_url = _extract_play_api_url(text)
            if play_api_url:
                return play_api_url
            base_im_api = _extract_fszb_base_im_api(text)
            if base_im_api and DEFAULT_PLAY_PATH in text:
                return base_im_api.rstrip("/") + DEFAULT_PLAY_PATH
        for js_url in js_urls:
            try:
                text = client.get(js_url).text
            except Exception:
                continue
            base_im_api = _extract_fszb_base_im_api(text)
            if base_im_api:
                return base_im_api.rstrip("/") + DEFAULT_PLAY_PATH
    return ""


def _extract_script_urls(html: str, site_url: str) -> list[str]:
    urls = re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", html, flags=re.I)
    result: list[str] = []
    for url in urls:
        if url.startswith("//"):
            result.append("https:" + url)
        elif url.startswith(("http://", "https://")):
            result.append(url)
        else:
            result.append(urljoin(site_url.rstrip("/") + "/", url))
    return result


def _extract_play_api_url(js_text: str) -> str:
    for match in re.finditer(r'https?://[^"\'`\\\s]+', js_text):
        url = match.group(0).rstrip("),;")
        if DEFAULT_PLAY_PATH in url:
            return url.split(DEFAULT_PLAY_PATH, 1)[0].rstrip("/") + DEFAULT_PLAY_PATH

    for match in re.finditer(r'https?://openim-php-api[^"\'`\\\s]+', js_text, flags=re.I):
        parsed = urlparse(match.group(0).rstrip("),;"))
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{DEFAULT_PLAY_PATH}"

    return ""


def _extract_fszb_base_im_api(js_text: str) -> str:
    marker = '"fszb-theme"'
    idx = js_text.find(marker)
    while idx >= 0:
        snippet = js_text[idx : idx + 500]
        match = re.search(r'n\s*=\s*"([^"]+)"', snippet)
        if match:
            return match.group(1)
        idx = js_text.find(marker, idx + len(marker))
    return ""


def update_rooms_config(path: Path, updates: dict[str, str], *, discover_play_api: bool = True) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    config = data.setdefault("config", {})
    applied = {key: value for key, value in updates.items() if value}

    if discover_play_api and not applied.get("play_api_url"):
        try:
            play_api_url = discover_play_api_url(
                applied.get("site_url") or config.get("site_url", ""),
                proxy_url=_configured_proxy({**config, **applied}),
            )
            if play_api_url:
                applied["play_api_url"] = play_api_url
        except Exception:
            pass

    config.update(applied)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return applied


def update_from_curl(raw: str, path: Path = DEFAULT_ROOMS_CONFIG) -> dict[str, str]:
    updates = parse_curl(raw)
    return update_rooms_config(path, updates)


def update_stream_libraries(packages: tuple[str, ...] = ("streamlink", "streamget")) -> int:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]
    return subprocess.run(cmd).returncode


def _read_curl_lines(first_line: str = "") -> str:
    lines: list[str] = []
    if first_line:
        lines.append(first_line)
    while True:
        line = sys.stdin.readline()
        if line == "" or line.strip() == "":
            break
        lines.append(line)
    return "".join(lines)


def _interactive_update_fs_curl(path: Path = DEFAULT_ROOMS_CONFIG, first_line: str = "") -> int:
    print("请粘贴最新 FS /v1/room 请求 curl，粘贴完成后输入一个空行结束：")
    lines: list[str] = []
    while True:
        line = sys.stdin.readline()
        if line == "" or line.strip() == "":
            break
        lines.append(line)

    try:
        applied = update_from_curl("".join(lines), path)
    except Exception as e:
        print(f"更新失败：{e}")
        return 1

    print("FS1 配置已更新：")
    for key in sorted(applied):
        value = applied[key]
        if key == "token":
            value = value[:24] + "..." if len(value) > 24 else "***"
        print(f"- {key}: {value}")
    return 0


def _apply_curl_update(raw: str, path: Path = DEFAULT_ROOMS_CONFIG) -> int:
    try:
        applied = update_from_curl(raw, path)
    except Exception as e:
        print(f"update failed: {e}")
        return 1

    print("FS1 config updated:")
    for key in sorted(applied):
        value = applied[key]
        if key == "token":
            value = value[:24] + "..." if len(value) > 24 else "***"
        print(f"- {key}: {value}")
    return 0


def interactive_update(path: Path = DEFAULT_ROOMS_CONFIG) -> int:
    print("Update options:")
    print("1. Update FS config from /v1/room curl")
    print("2. Upgrade streamlink and streamget")
    print("Paste curl directly to keep the old flow.")
    print("Choose 1/2, or paste curl:")
    first_line = sys.stdin.readline()
    choice = first_line.strip()

    if choice == "2":
        print("Upgrading streamlink and streamget...")
        code = update_stream_libraries()
        if code == 0:
            print("streamlink and streamget upgraded successfully.")
        else:
            print(f"upgrade failed with exit code: {code}")
        return code

    if choice == "1" or not choice:
        return _interactive_update_fs_curl(path)

    return _apply_curl_update(_read_curl_lines(first_line), path)


def _load_fs_config() -> dict:
    if not ROOMS_CONFIG.exists():
        return {}

    try:
        with open(ROOMS_CONFIG, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}

    return data.get("config", {}) or {}


def _build_headers(cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    site_url = get_fs_site_url(cfg)
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Pragma": "no-cache",
        "Origin": site_url,
        "Referer": site_url + "/",
        "User-Agent": (
            cfg.get("user_agent")
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "api-version": cfg.get("api_version", "8"),
        "version": cfg.get("version", "1.8.4"),
        "authorization": cfg.get("token", ""),
        "device": "3",
        "device2": "3",
        "platform": "fszb",
        "imei": cfg.get("imei", ""),
        "dun-imei": cfg.get("dun_imei", ""),
        "sec-ch-ua": '"Tabbit";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def _media_headers(headers: dict) -> dict:
    return {
        key: headers[key]
        for key in ("User-Agent", "Referer", "Origin", "Accept")
        if headers.get(key)
    }


class Fs1Plugin(LiveStreamPlugin):
    name = "fs1"

    def __init__(self):
        self.reload_config()

    def reload_config(self) -> None:
        """Reload local FS1 settings without exposing implementation fields."""
        self._cfg = _load_fs_config()
        self._api_url = self._cfg.get("api_url", "https://apc.xzood6veuybwkr.com/v1/room")
        self._play_api_url = self._cfg.get("play_api_url", DEFAULT_PLAY_API_URL)
        self._verify_ssl = _as_bool(self._cfg.get("verify_ssl"), default=True)
        self._proxy_url = _configured_proxy(self._cfg)

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        extra_dict = kwargs.get("extra", {})
        sport_id = extra_dict.get("sport_id", "1") if isinstance(extra_dict, dict) else kwargs.get("sport_id", "1")
        quality = kwargs.get("quality", "best")
        headers = _build_headers(self._cfg)

        try:
            async with httpx.AsyncClient(
                headers=headers,
                timeout=15,
                verify=self._verify_ssl,
                proxy=self._proxy_url,
            ) as client:
                resp = await client.get(self._api_url, params={"room_id": url, "sport_id": sport_id})
                resp.raise_for_status()
                body = resp.json()
        except Exception as e:
            return LiveInfo(is_live=False, extra={"error": str(e)})

        if body.get("code") != 200:
            return LiveInfo(is_live=False, extra={"error": body.get("message", "API错误")})

        data = body["data"]
        is_live = data.get("room_status") == 2
        play_flow = {q["code_id"]: q for q in data.get("play_flow", [])}

        selected_url = selected_name = selected_code_id = ""
        q_priority = [quality] + QUALITY_PRIORITY if quality != "best" else QUALITY_PRIORITY
        for code_id in q_priority:
            if code_id in play_flow:
                q = play_flow[code_id]
                selected_url = q.get("play_url", "")
                selected_name = q.get("name", "")
                selected_code_id = code_id
                break

        if not selected_url:
            selected_url = data.get("pull_url") or data.get("pull_flv_url", "")
            selected_name = selected_name or "原画"

        expire_ts = 0
        if is_live and not selected_url and selected_code_id:
            try:
                async with httpx.AsyncClient(
                    headers=headers,
                    timeout=15,
                    verify=self._verify_ssl,
                    proxy=self._proxy_url,
                ) as client:
                    selected_url, expire_ts = await fetch_play_url(
                        client,
                        self._play_api_url,
                        url,
                        selected_code_id,
                        match_id=(data.get("match_info") or {}).get("match_id"),
                        sport_id=sport_id,
                    )
            except Exception as e:
                return LiveInfo(is_live=False, extra={"error": str(e)})

        anchor = data.get("anchor_info") or {}
        match_info = data.get("match_info") or {}

        return LiveInfo(
            is_live=is_live,
            anchor_name=anchor.get("nickname", ""),
            title=data.get("room_title", ""),
            stream_url=selected_url,
            m3u8_url=data.get("pull_url", ""),
            flv_url=data.get("pull_flv_url", ""),
            quality_name=selected_name,
            extra={
                "home": match_info.get("home_name", ""),
                "away": match_info.get("away_name", ""),
                "home_score": match_info.get("home_score", 0),
                "away_score": match_info.get("away_score", 0),
                "expire_ts": expire_ts,
                "headers": _media_headers(headers),
            },
        )

    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str:
        info = await self.check_live(url, quality=quality, **kwargs)
        if not info.is_live:
            raise RuntimeError("主播未开播")
        if not info.stream_url:
            raise RuntimeError("无法获取流地址")
        return info.stream_url


from . import register_plugin

register_plugin(Fs1Plugin())
