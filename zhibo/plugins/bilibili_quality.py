from __future__ import annotations

from pathlib import Path

import httpx

from zhibo.private_data import cookie_file_path


BILIBILI_PLAYINFO_API = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
BILIBILI_ROOM_INFO_API = "https://api.live.bilibili.com/room/v1/Room/get_info"


def _cookie_file_path() -> Path:
    return cookie_file_path()


COOKIES_FILE = _cookie_file_path()
BILIBILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    ),
    "Referer": "https://live.bilibili.com/",
    "Origin": "https://live.bilibili.com",
}
BILIBILI_QN_LABELS = {
    30000: "杜比",
    20000: "4K",
    10000: "原画",
    400: "蓝光",
    250: "超清",
    150: "高清",
    80: "流畅",
}
BILIBILI_QUALITY_ALIASES = {
    "best": 10000,
    "od": 10000,
    "source": 10000,
    "原画": 10000,
    "最高": 10000,
    "最高画质": 10000,
    "4k": 20000,
    "蓝光": 400,
    "bd": 400,
    "超清": 250,
    "uhd": 250,
    "高清": 150,
    "hd": 150,
    "流畅": 80,
    "sd": 80,
}
BEST_EXPECTED_QN = 10000
CODEC_PRIORITY = {"av1": 3, "hevc": 2, "avc": 1}
FORMAT_PRIORITY = {"fmp4": 3, "flv": 2, "ts": 1}
PROTOCOL_PRIORITY = {"http_hls": 2, "http_stream": 1}


def wants_best_bilibili_quality(platform: str | None, quality: str | None) -> bool:
    if str(platform or "").strip().casefold() != "bilibili":
        return False
    requested = str(quality or "best").strip().casefold()
    return requested in {"best", "od", "source", "原画", "最高", "最高画质"}


def bilibili_quality_qn(platform: str | None, quality: str | None) -> int | None:
    if str(platform or "").strip().casefold() != "bilibili":
        return None
    return BILIBILI_QUALITY_ALIASES.get(str(quality or "best").strip().casefold())


def _room_id_from_url(url: str) -> str:
    return str(url).split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def _playinfo_params(url: str, qn: int = BEST_EXPECTED_QN) -> dict[str, str | int]:
    return {
        "room_id": _room_id_from_url(url),
        "no_playurl": 0,
        "mask": 1,
        "qn": qn,
        "platform": "web",
        "protocol": "0,1",
        "format": "0,1,2",
        "codec": "0,1,2",
        "dolby": 5,
        "panorama": 1,
    }


async def fetch_bilibili_live_status(url: str, timeout: float = 5) -> bool:
    """Return the room's live state without resolving or opening a CDN URL.

    Offline rooms are common and must not pay Streamlink's stream-validation
    cost.  ``no_playurl=1`` keeps this request small and also separates a room
    state check from a potentially unhealthy individual CDN endpoint.
    """
    params = _playinfo_params(url)
    params["no_playurl"] = 1
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=BILIBILI_HEADERS,
    ) as client:
        response = await client.get(BILIBILI_PLAYINFO_API, params=params)
        response.raise_for_status()
        payload = response.json()

    try:
        code = int(payload.get("code", 0))
    except (TypeError, ValueError):
        code = -1
    if code != 0:
        raise RuntimeError(f"B站状态接口返回错误（code={code}）")
    data = payload.get("data") or {}
    if "live_status" not in data:
        raise RuntimeError("B站状态接口缺少 live_status")
    try:
        return int(data.get("live_status") or 0) != 0
    except (TypeError, ValueError):
        raise RuntimeError("B站状态接口的 live_status 无效") from None


async def fetch_bilibili_room_metadata(url: str, timeout: float = 5) -> dict[str, str]:
    """Fetch display metadata without resolving or validating a media CDN."""
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=BILIBILI_HEADERS,
    ) as client:
        response = await client.get(
            BILIBILI_ROOM_INFO_API,
            params={"room_id": _room_id_from_url(url)},
        )
        response.raise_for_status()
        payload = response.json()

    try:
        code = int(payload.get("code", 0))
    except (TypeError, ValueError):
        code = -1
    if code != 0:
        raise RuntimeError(f"B站房间信息接口返回错误（code={code}）")
    data = payload.get("data") or {}
    return {"title": str(data.get("title") or "").strip()}


def _quality_label(qn: int, codec: str, stream_format: str) -> str:
    label = BILIBILI_QN_LABELS.get(qn, f"qn{qn}")
    suffix = "/".join(part for part in [codec.upper(), stream_format] if part)
    return f"{label}({qn})/{suffix}" if suffix else f"{label}({qn})"


def _bilibili_cookie_header(cookie_file: Path | None = None) -> str:
    cookie_file = cookie_file or _cookie_file_path()
    if not cookie_file.exists():
        return ""

    pairs: list[str] = []
    text = cookie_file.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if not line:
            continue
        # ``#HttpOnly_`` is a Netscape cookie-record prefix, rather than an
        # ordinary comment.  Ignoring it loses valid browser-exported login
        # cookies after a safe Cookie update.
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, name, value = parts[0].casefold(), parts[5], parts[6]
        bare_domain = domain.lstrip(".")
        if (bare_domain == "bilibili.com" or bare_domain.endswith(".bilibili.com")) and name and value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _is_cookie_auth_rejection(payload: dict) -> bool:
    """Return True only when Bilibili explicitly says the account is not logged in.

    ``accept_qn`` is deliberately not used here.  It describes quality values
    accepted by the endpoint and may contain tiers that the current live source
    does not actually provide, so comparing it with ``current_qn`` produces
    false Cookie errors.
    """
    try:
        return int(payload.get("code")) == -101
    except (TypeError, ValueError):
        return False


def _quality_warning(selected: dict, *, cookie_auth_rejected: bool = False) -> str:
    if not cookie_auth_rejected:
        return ""

    selected_qn = int(selected.get("qn") or 0)
    current_label = BILIBILI_QN_LABELS.get(selected_qn, f"qn{selected_qn}")
    return (
        "B站 Cookie 已被接口明确拒绝，"
        f"当前使用未登录可用画质 {current_label}({selected_qn})；请更新 Cookie"
    )


def _select_from_playinfo(payload: dict, requested_qn: int | None = None) -> dict | None:
    data = payload.get("data") or {}
    if data.get("live_status") == 0:
        return None

    playurl = ((data.get("playurl_info") or {}).get("playurl") or {})
    candidates: list[dict] = []
    for stream in playurl.get("stream") or []:
        protocol = stream.get("protocol_name") or ""
        for stream_format in stream.get("format") or []:
            format_name = stream_format.get("format_name") or ""
            for codec in stream_format.get("codec") or []:
                base_url = codec.get("base_url") or ""
                codec_name = codec.get("codec_name") or ""
                current_qn = int(codec.get("current_qn") or 0)
                if not base_url or current_qn <= 0:
                    continue
                for url_info in codec.get("url_info") or []:
                    host = url_info.get("host") or ""
                    extra = url_info.get("extra") or ""
                    if not host:
                        continue
                    url = f"{host}{base_url}{extra}"
                    candidates.append(
                        {
                            "url": url,
                            "qn": current_qn,
                            "codec": codec_name,
                            "format": format_name,
                            "protocol": protocol,
                            "label": _quality_label(current_qn, codec_name, format_name),
                        }
                    )

    if not candidates:
        return None

    def quality_rank(item: dict) -> tuple[int, int]:
        qn = int(item["qn"])
        if requested_qn is None:
            return (1, qn)
        # Prefer the best tier that does not exceed the user's request.  If
        # the API only returns a higher tier, use the nearest one instead of
        # incorrectly reporting the room as unplayable.
        return (1, qn) if qn <= requested_qn else (0, -qn)

    ranked = sorted(
        candidates,
        key=lambda item: (
            *quality_rank(item),
            CODEC_PRIORITY.get(item["codec"], 0),
            FORMAT_PRIORITY.get(item["format"], 0),
            PROTOCOL_PRIORITY.get(item["protocol"], 0),
        ),
        reverse=True,
    )
    selected = dict(ranked[0])
    selected["candidates"] = list(dict.fromkeys(item["url"] for item in ranked))
    return selected


def _select_best_from_playinfo(payload: dict) -> dict | None:
    """Backward-compatible highest-quality selector used by existing callers/tests."""
    return _select_from_playinfo(payload)


async def fetch_best_bilibili_stream(
    url: str,
    timeout: float = 8,
    *,
    quality: str = "best",
) -> dict | None:
    requested_qn = BILIBILI_QUALITY_ALIASES.get(str(quality or "best").strip().casefold(), BEST_EXPECTED_QN)
    cookie_header = _bilibili_cookie_header()
    attempts = [(cookie_header, True)] if cookie_header else []
    attempts.append(("", False))

    last_error: Exception | None = None
    cookie_auth_rejected = False
    for attempt_cookie, uses_cookie in attempts:
        headers = dict(BILIBILI_HEADERS)
        if attempt_cookie:
            headers["Cookie"] = attempt_cookie

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                response = await client.get(BILIBILI_PLAYINFO_API, params=_playinfo_params(url, requested_qn))
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as e:
            if uses_cookie and e.response.status_code == 401:
                cookie_auth_rejected = True
            last_error = e
            continue
        except Exception as e:
            last_error = e
            continue

        if uses_cookie and _is_cookie_auth_rejection(payload):
            cookie_auth_rejected = True
            continue

        selected = _select_from_playinfo(payload, requested_qn)
        if not selected:
            continue

        selected["used_cookie"] = uses_cookie
        selected["has_cookie"] = bool(cookie_header)
        selected["quality_warning"] = _quality_warning(
            selected,
            cookie_auth_rejected=cookie_auth_rejected,
        )
        return selected

    if last_error:
        raise last_error
    return None
