"""飞速直播插件与配置更新工具。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from Crypto.Cipher import AES

try:
    import truststore
except ImportError:  # pragma: no cover - exercised by minimal installations
    truststore = None

from zhibo.private_data import ensure_private_parent, fs_config_path

from .base import LiveStreamPlugin, LiveInfo

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
ROOMS_CONFIG = fs_config_path()
DEFAULT_ROOMS_CONFIG = ROOMS_CONFIG
DEFAULT_SITE_URL = "https://www.fszb130.com"
DEFAULT_API_URL = "https://apc.xzood6veuybwkr.com/v1/room"
DEFAULT_API_PATH = "/v1/room"
DEFAULT_PLAY_API_URL = "https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url"
DEFAULT_PLAY_PATH = "/v230/play/url"
QUALITY_PRIORITY = ["lgzm", "gqzm", "bqzm"]
# FS1 has rotated between numeric ``fs`` and ``fszb`` site names. Keep the
# accepted family narrow because the browser export and authorization headers
# are only safe to use with an owned FS1 site origin.
FS_DOMAIN_RE = re.compile(r"(?:[a-z0-9-]+\.)*(?:fszb|fs)\d+\.com$", re.I)
API_HOSTS = frozenset({"apc.xzood6veuybwkr.com"})
# FS pages currently host their public bundles on this fixed CDN. It is used
# only for unauthenticated script discovery; authorization headers are never
# sent to it.
FS_PUBLIC_ASSET_HOSTS = frozenset({"rsfs.ypzzib.com"})
# The frontend currently publishes play endpoints in this namespace. Keep this
# deliberately narrow: a discovered endpoint is contacted with an authorization
# header, so accepting an arbitrary URL from a page script is not safe.
PLAY_API_HOST_RE = re.compile(r"openim-php-api\.[a-z0-9]{8,64}\.cc$", re.I)


def _fs1_verify_context() -> bool | ssl.SSLContext:
    """Use the Windows/native trust store while keeping TLS verification on."""
    if truststore is None:
        return True
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

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


def _parse_https_url(value: str):
    """Return a safe HTTPS URL parse result, or ``None`` for unsafe URLs."""
    try:
        parsed = urlparse((value or "").strip())
        port = parsed.port
    except ValueError:
        return None

    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        return None
    return parsed


def _is_trusted_fs_host(host: str) -> bool:
    return bool(host and FS_DOMAIN_RE.fullmatch(host.casefold()))


def _is_trusted_fs_site_url(value: str) -> bool:
    parsed = _parse_https_url(value)
    return bool(parsed and _is_trusted_fs_host(parsed.hostname or ""))


def _is_same_https_origin(candidate: str, origin: str) -> bool:
    candidate_parsed = _parse_https_url(candidate)
    origin_parsed = _parse_https_url(origin)
    if not candidate_parsed or not origin_parsed:
        return False
    return (
        candidate_parsed.hostname.casefold() == origin_parsed.hostname.casefold()
        and (candidate_parsed.port or 443) == (origin_parsed.port or 443)
    )


def _is_trusted_fs_asset_url(candidate: str, page_url: str) -> bool:
    parsed = _parse_https_url(candidate)
    page = _parse_https_url(page_url)
    if not parsed or not page:
        return False
    return _is_same_https_origin(candidate, page_url) or (
        (parsed.hostname or "").casefold() in FS_PUBLIC_ASSET_HOSTS
        and parsed.port in (None, 443)
    )


def is_trusted_api_url(value: str) -> bool:
    """Whether an endpoint may receive the FS1 authorization headers."""
    parsed = _parse_https_url(value)
    return bool(
        parsed
        and (parsed.hostname or "").casefold() in API_HOSTS
        and parsed.path == DEFAULT_API_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_trusted_play_api_url(value: str) -> bool:
    """Whether an endpoint may receive the FS1 authorization headers."""
    parsed = _parse_https_url(value)
    return bool(
        parsed
        and PLAY_API_HOST_RE.fullmatch(parsed.hostname or "")
        and parsed.path == DEFAULT_PLAY_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


async def fetch_play_url(
    client: httpx.AsyncClient,
    play_api_url: str,
    room_id: str,
    code_id: str,
    *,
    match_id: str | int | None = None,
    sport_id: str | int | None = None,
) -> tuple[str, int]:
    if not is_trusted_play_api_url(play_api_url):
        raise ValueError("untrusted FS1 play API endpoint")

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

    if not _parse_https_url(url):
        raise ValueError("curl request URL must be HTTPS")

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("curl 中的请求 URL 无效")

    api_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if not is_trusted_api_url(api_url):
        raise ValueError("curl request URL is not a trusted FS1 API endpoint")

    config: dict[str, str] = {"api_url": api_url}

    site_url = _site_url_from_headers(headers)
    if site_url:
        config["site_url"] = site_url

    field_map = {
        "api-version": "api_version",
        "authorization": "token",
        "cookie": "cookie",
        "imei": "imei",
        "dun-imei": "dun_imei",
        "user-agent": "user_agent",
        "version": "version",
        "client_version": "version",
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
        if _is_trusted_fs_site_url(value) and parsed.scheme and parsed.netloc:
            return f"https://{parsed.netloc}"
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
    parsed = _parse_https_url(value)
    if not parsed or not _is_trusted_fs_host(parsed.hostname or ""):
        return DEFAULT_SITE_URL
    return f"https://{parsed.netloc}".rstrip("/")


def get_fs_site_url(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else _load_fs_config()
    return _normalize_site_url(str((cfg or {}).get("site_url") or DEFAULT_SITE_URL))


def is_fs_site_url(url: str, cfg: dict | None = None) -> bool:
    # ``cfg`` is retained for compatibility. A local configuration value must
    # never expand the set of domains accepted as FS1 links.
    del cfg
    value = (url or "").strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    return _is_trusted_fs_host(parsed.hostname or "")


def _configured_proxy(cfg: dict | None = None) -> str | None:
    """FS1 is domestic traffic and must always connect directly."""
    return None


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
    """Discover a validated endpoint from same-origin FS frontend assets.

    A discovered endpoint is only useful if it can safely receive the local
    authorization header, so this accepts HTTPS FS pages and scripts that remain
    on the final page origin after redirects.
    """
    if not _is_trusted_fs_site_url(site_url):
        return ""

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
        verify=_fs1_verify_context(),
        proxy=proxy_url,
    ) as client:
        page_response = client.get(site_url.rstrip("/") + "/")
        page_response.raise_for_status()
        page_url = str(page_response.url)
        if not _is_trusted_fs_site_url(page_url):
            return ""

        js_urls = [
            js_url
            for js_url in _extract_script_urls(page_response.text, page_url)
            if _is_trusted_fs_asset_url(js_url, page_url)
        ]
        js_texts: list[str] = []
        for js_url in js_urls:
            try:
                response = client.get(js_url)
                response.raise_for_status()
            except Exception:
                continue
            # A public CDN script may still redirect to an untrusted host.
            if not _is_trusted_fs_asset_url(str(response.url), page_url):
                continue
            js_texts.append(response.text)

        # The bundle contains API mappings for every brand. Prefer the host
        # inside the active ``fszb-theme`` branch; a generic first-match search
        # can otherwise select another brand's openim host and fail signature
        # validation even when the signing parameters are correct.
        for text in js_texts:
            base_im_api = _extract_fszb_base_im_api(text)
            candidate = base_im_api.rstrip("/") + DEFAULT_PLAY_PATH if base_im_api else ""
            if is_trusted_play_api_url(candidate):
                return candidate

        for text in js_texts:
            play_api_url = _extract_play_api_url(text)
            if play_api_url:
                return play_api_url
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
            candidate = url.split(DEFAULT_PLAY_PATH, 1)[0].rstrip("/") + DEFAULT_PLAY_PATH
            if is_trusted_play_api_url(candidate):
                return candidate

    for match in re.finditer(r'https?://openim-php-api[^"\'`\\\s]+', js_text, flags=re.I):
        parsed = urlparse(match.group(0).rstrip("),;"))
        if parsed.scheme and parsed.netloc:
            candidate = f"{parsed.scheme}://{parsed.netloc}{DEFAULT_PLAY_PATH}"
            if is_trusted_play_api_url(candidate):
                return candidate

    return ""


def _extract_fszb_base_im_api(js_text: str) -> str:
    # ``ba85`` is a ternary mapping keyed by the active theme. In the current
    # public bundle its FSZB arm assigns the IM API to variable ``h``. Do not
    # search for the first openim URL globally because the same bundle embeds
    # all other brand mappings before the FSZB arm.
    marker = re.compile(r'["\']fszb-theme["\']\s*==\s*i')
    for marker_match in marker.finditer(js_text):
        snippet = js_text[marker_match.end() : marker_match.end() + 3000]
        match = re.search(
            r'\bh\s*=\s*["\'](https://openim-php-api\.[a-z0-9]{8,64}\.cc/?)',
            snippet,
            flags=re.I,
        )
        if match:
            return match.group(1)
    return ""


FS_CONFIG_UPDATE_FIELDS = frozenset(
    {
        "site_url",
        "api_url",
        "play_api_url",
        "api_version",
        "token",
        "cookie",
        "imei",
        "dun_imei",
        "user_agent",
        "version",
    }
)


def update_rooms_config(path: Path, updates: dict[str, str], *, discover_play_api: bool = True) -> dict[str, str]:
    """Persist explicit FS1 config updates atomically.

    ``discover_play_api`` is retained for callers of earlier releases. A value
    discovered from a remote frontend is never persisted automatically; users
    must explicitly provide a validated endpoint instead.
    """
    path = Path(path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    config = data.setdefault("config", {})
    unknown_fields = set(updates) - FS_CONFIG_UPDATE_FIELDS
    if unknown_fields:
        raise ValueError("updates 包含不支持的 FS1 配置字段")

    applied: dict[str, str] = {}
    for key, value in updates.items():
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            raise ValueError(f"{key} 必须是文本")
        normalized = value.strip()
        if "\r" in normalized or "\n" in normalized:
            raise ValueError(f"{key} 不能包含换行符")
        applied[key] = normalized

    if applied.get("site_url") and not _is_trusted_fs_site_url(applied["site_url"]):
        raise ValueError("site_url must be an HTTPS fszb domain")
    if applied.get("api_url") and not is_trusted_api_url(applied["api_url"]):
        raise ValueError("api_url is not a trusted FS1 endpoint")
    if applied.get("play_api_url") and not is_trusted_play_api_url(applied["play_api_url"]):
        raise ValueError("play_api_url is not a trusted FS1 endpoint")

    # Do not call or persist automatic discovery here. This configuration file
    # contains the authorization token used by ``fetch_play_url``.
    del discover_play_api

    config.update(applied)
    _write_yaml_atomically(path, data)
    return applied


def _write_yaml_atomically(path: Path, data: dict) -> None:
    """Write YAML through a sibling temporary file, preserving the old file on failure."""
    path = Path(path)
    ensure_private_parent(path)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def update_from_curl(raw: str, path: Path = DEFAULT_ROOMS_CONFIG) -> dict[str, str]:
    updates = parse_curl(raw)
    return update_rooms_config(path, updates)


FS1_EXPORT_FORMAT = "zhibo.fs1-auth"
FS1_EXPORT_VERSION = 2
FS1_EXPORT_VERSIONS = frozenset({1, 2})


def _json_object_from_text(raw: str) -> dict[str, Any] | None:
    """Decode the userscript handoff, allowing a pasted JSON code fence."""
    text = (raw or "").lstrip("\ufeff").strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    else:
        # Clipboard contents are sometimes prefixed by a short label or a
        # browser notice. Accept one surrounding text layer, but still decode
        # exactly one JSON object and reject arbitrary fragments.
        start = text.find("{")
        end = text.rfind("}")
        if start > 0 and end >= start:
            text = text[start : end + 1].strip()
    if not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("FS1 导出 JSON 格式无效") from exc
    if not isinstance(value, dict):
        raise ValueError("FS1 导出内容必须是 JSON 对象")
    return value


def _export_key(value: Any) -> str:
    """Normalize common JSON/header spellings without changing values."""
    key = str(value or "").strip()
    key = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", key)
    return re.sub(r"[\s_]+", "-", key).casefold()


def _export_scalar(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _export_maps(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return candidate request maps and their nested header maps.

    The userscript intentionally keeps a small, known set of nested objects.
    This makes the importer tolerant of v1/v2 layout changes without turning
    arbitrary pasted JSON into configuration input.
    """
    maps: list[dict[str, Any]] = [payload]
    for key in ("auth", "request", "room", "data", "latest_request", "latestRequest", "request_snapshot", "requestSnapshot"):
        value = payload.get(key)
        if isinstance(value, dict):
            maps.append(value)
            for child_key in ("auth", "request", "room", "play", "data"):
                child = value.get(child_key)
                if isinstance(child, dict):
                    maps.append(child)

    captured = payload.get("captured_requests") or payload.get("capturedRequests")
    if isinstance(captured, list):
        maps.extend(item for item in captured if isinstance(item, dict))

    header_maps: list[dict[str, Any]] = []
    for item in maps:
        headers = item.get("headers")
        if isinstance(headers, dict):
            header_maps.append(headers)
    return maps, header_maps


def _export_value(
    maps: list[dict[str, Any]],
    headers: list[dict[str, Any]],
    *names: str,
) -> str:
    wanted = {_export_key(name) for name in names}
    for source in (*maps, *headers):
        normalized = {_export_key(key): value for key, value in source.items()}
        for key in wanted:
            value = _export_scalar(normalized.get(key))
            if value:
                return value
    return ""


def _export_url_candidate(maps: list[dict[str, Any]], *names: str) -> str:
    return _export_value(maps, [], *names)


def _export_is_room_map(source: dict[str, Any]) -> bool:
    for key, value in source.items():
        if _export_key(key) not in {"api-url", "request-url", "url"}:
            continue
        parsed = _parse_https_url(_export_scalar(value))
        if (
            parsed
            and (parsed.hostname or "").casefold() in API_HOSTS
            and parsed.path.rstrip("/") == DEFAULT_API_PATH
        ):
            return True
    return False


def _export_api_base(candidate: str) -> str:
    """Normalize a room API URL and enforce the fixed trusted endpoint."""
    parsed = _parse_https_url(candidate)
    if not parsed or (parsed.hostname or "").casefold() not in API_HOSTS:
        return ""
    path = parsed.path.rstrip("/") or DEFAULT_API_PATH
    if path == "/":
        path = DEFAULT_API_PATH
    base = f"https://{parsed.netloc}{path}"
    return base if is_trusted_api_url(base) else ""


def _export_request_url(maps: list[dict[str, Any]]) -> str:
    for source in maps:
        candidate = _export_scalar(
            next(
                (value for key, value in source.items() if _export_key(key) in {"request-url", "url"}),
                "",
            )
        )
        if candidate:
            return candidate
    return ""


def parse_fs1_export(raw: str) -> dict[str, str]:
    """Convert a ``zhibo.fs1-auth`` userscript export into config fields."""
    payload = _json_object_from_text(raw)
    if payload is None:
        raise ValueError("不是 FS1 JSON 导出")
    if payload.get("format") != FS1_EXPORT_FORMAT:
        raise ValueError("FS1 导出格式不受支持")
    export_version = _export_scalar(payload.get("version")) or "1"
    if export_version not in {str(item) for item in FS1_EXPORT_VERSIONS}:
        raise ValueError("FS1 导出版本不受支持")

    maps, header_maps = _export_maps(payload)
    room_maps = [source for source in maps if _export_is_room_map(source)]
    api_maps = [payload, *room_maps]
    if not room_maps:
        api_maps = maps
    explicit_api = _export_url_candidate(api_maps, "api_url", "api-url")
    request_url = _export_request_url(room_maps or maps)
    api_url = _export_api_base(explicit_api or request_url) if (explicit_api or request_url) else DEFAULT_API_URL
    if not api_url:
        raise ValueError("FS1 导出的 API URL 不是受信任的 HTTPS /v1/room 接口")

    updates: dict[str, str] = {"api_url": api_url}
    site_url = _export_value(maps, header_maps, "site_url", "site-url", "origin")
    if not site_url:
        referer = _export_value(maps, header_maps, "referer", "referrer")
        parsed_referer = _parse_https_url(referer)
        site_url = f"https://{parsed_referer.netloc}" if parsed_referer else ""
    if site_url:
        parsed_site = _parse_https_url(site_url)
        if not parsed_site or not _is_trusted_fs_host(parsed_site.hostname or ""):
            raise ValueError("FS1 导出的站点 URL 不受支持")
        updates["site_url"] = f"https://{parsed_site.netloc}"

    configured_play_api = _export_url_candidate(maps, "play_api_url", "play-api-url")
    if configured_play_api:
        parsed_play = _parse_https_url(configured_play_api)
        if not parsed_play or not is_trusted_play_api_url(configured_play_api):
            raise ValueError("FS1 导出的播放 API URL 不受支持")
        updates["play_api_url"] = f"{parsed_play.scheme}://{parsed_play.netloc}{parsed_play.path}"

    field_map = {
        "api_version": ("api-version", "api_version", "apiVersion"),
        "token": ("authorization", "token", "auth", "access-token", "x-authorization"),
        "cookie": ("cookie", "cookies"),
        "imei": ("imei", "device-imei"),
        "dun_imei": ("dun-imei", "dun_imei", "dunImei"),
        "user_agent": ("user-agent", "user_agent", "userAgent"),
        "version": ("version", "client_version", "client-version", "clientVersion"),
    }
    for config_name, aliases in field_map.items():
        if config_name == "version":
            # ``version`` at the top level is the handoff format version;
            # only use it as a client version when it is inside headers.
            value = _export_value(maps, header_maps, "client_version", "client-version", "clientVersion")
            if not value:
                value = _export_value(header_maps, [], "version")
        else:
            value = _export_value(maps, header_maps, *aliases)
        if value:
            updates[config_name] = value

    if not updates.get("token") and not updates.get("cookie"):
        raise ValueError("FS1 导出中没有 authorization 或 Cookie")
    return updates


def update_from_text(
    raw: str,
    path: Path = DEFAULT_ROOMS_CONFIG,
    *,
    discover_play_api: bool = True,
) -> dict[str, str]:
    """Persist either a userscript JSON export or the legacy curl input."""
    if _json_object_from_text(raw) is not None:
        updates = parse_fs1_export(raw)
        if discover_play_api and "play_api_url" not in updates:
            # The discovery request contains only the public site URL and a
            # generic user agent. The returned value is still validated before
            # it can be persisted or receive any authorization header.
            try:
                discovered = discover_play_api_url(updates.get("site_url", ""))
            except Exception:
                discovered = ""
            # Always overwrite a previously persisted endpoint when the JSON
            # export does not provide one. Otherwise a transient discovery
            # failure leaves an old cross-brand host in place and the next
            # request reaches a server that reports a misleading signature
            # error. The fixed current FSZB endpoint is the safe fallback.
            updates["play_api_url"] = discovered or DEFAULT_PLAY_API_URL
        return update_rooms_config(path, updates, discover_play_api=False)
    # Keep the one-argument legacy helper easy to monkeypatch for embedders and
    # older UI integrations; custom destinations still use the explicit path.
    if Path(path) == Path(DEFAULT_ROOMS_CONFIG):
        return update_from_curl(raw)
        return update_from_curl(raw, path)


def update_stream_libraries(packages: tuple[str, ...] = ("streamlink", "streamget")) -> int:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--constraint",
        str(SCRIPT_DIR / "requirements.txt"),
        *packages,
    ]
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


def _display_config_value(key: str, value: object) -> str:
    """Render config output without leaking credential prefixes to the CLI."""
    sensitive_parts = ("token", "authorization", "cookie", "imei", "secret", "password")
    if any(part in key.casefold() for part in sensitive_parts):
        return "***"
    return str(value)


def _interactive_update_fs_curl(path: Path = DEFAULT_ROOMS_CONFIG, first_line: str = "") -> int:
    print("请粘贴 FS /v1/room curl 或 zhibo.fs1-auth JSON，完成后输入一个空行结束：")
    lines: list[str] = []
    while True:
        line = sys.stdin.readline()
        if line == "" or line.strip() == "":
            break
        lines.append(line)

    try:
        applied = update_from_text("".join(lines), path)
    except Exception as e:
        print(f"更新失败：{e}")
        return 1

    print("FS1 配置已更新：")
    for key in sorted(applied):
        value = _display_config_value(key, applied[key])
        print(f"- {key}: {value}")
    return 0


def _apply_curl_update(raw: str, path: Path = DEFAULT_ROOMS_CONFIG) -> int:
    try:
        applied = update_from_text(raw, path)
    except Exception as e:
        print(f"update failed: {e}")
        return 1

    print("FS1 config updated:")
    for key in sorted(applied):
        value = _display_config_value(key, applied[key])
        print(f"- {key}: {value}")
    return 0


def interactive_update(path: Path = DEFAULT_ROOMS_CONFIG) -> int:
    print("Update options:")
    print("1. Update FS config from /v1/room curl or zhibo.fs1-auth JSON")
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
    """Build headers for the frontend's base API (the room-info request).

    ``platform`` and ``device2`` are only added by the public frontend's
    ``baseImApi`` branch; they are supplied by :func:`_play_api_headers` for
    the play-url request. The page Cookie is retained in local configuration,
    but is not copied to this cross-origin API request because the frontend
    does not send that page cookie explicitly.
    """
    cfg = cfg or {}
    site_url = get_fs_site_url(cfg)
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
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
        "imei": cfg.get("imei", ""),
        "dun-imei": cfg.get("dun_imei", ""),
    }


def _play_api_headers(headers: dict[str, str]) -> dict[str, str]:
    """Match the browser's baseImApi headers for the play-url POST.

    The browser's axios defaults and request interceptor send the common
    ``device``/``version`` fields and the authorization/device headers to
    ``baseImApi``; its ``baseImApi`` branch additionally sends ``platform`` and
    ``device2``. Cookie is deliberately omitted because a
    browser cross-origin request does not copy the site's Cookie header into
    this API request, and the exported Cookie is only for the room-info call.
    """
    result = {key: value for key, value in headers.items() if key.casefold() != "cookie"}
    result.setdefault("device2", "3")
    result.setdefault("platform", "fszb")
    result.setdefault("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
    return result


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
        configured_api_url = str(self._cfg.get("api_url") or "")
        self._api_url = configured_api_url if is_trusted_api_url(configured_api_url) else DEFAULT_API_URL
        configured_play_api_url = str(self._cfg.get("play_api_url") or "")
        self._play_api_url = (
            configured_play_api_url
            if is_trusted_play_api_url(configured_play_api_url)
            else DEFAULT_PLAY_API_URL
        )
        # This client sends a reusable authorization header.  A local config
        # value must not be able to downgrade its TLS protection.
        self._verify_ssl = _fs1_verify_context()
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
                    headers=_play_api_headers(headers),
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
                "room_id": str(data.get("room_id") or url),
                "sport_id": str(data.get("sport_id") or match_info.get("sport_id") or sport_id),
                "match_id": str(match_info.get("match_id") or ""),
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
