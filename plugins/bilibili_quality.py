from __future__ import annotations

from pathlib import Path

import httpx


BILIBILI_PLAYINFO_API = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
COOKIES_FILE = Path(__file__).resolve().parent.parent / "cookies.txt"
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
BEST_EXPECTED_QN = 10000
CODEC_PRIORITY = {"av1": 3, "hevc": 2, "avc": 1}
FORMAT_PRIORITY = {"fmp4": 3, "flv": 2, "ts": 1}
PROTOCOL_PRIORITY = {"http_hls": 2, "http_stream": 1}


def wants_best_bilibili_quality(platform: str | None, quality: str | None) -> bool:
    if str(platform or "").strip().casefold() != "bilibili":
        return False
    requested = str(quality or "best").strip().casefold()
    return requested in {"best", "od", "source", "原画", "最高", "蓝光"}


def _room_id_from_url(url: str) -> str:
    return str(url).split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def _playinfo_params(url: str) -> dict[str, str | int]:
    return {
        "room_id": _room_id_from_url(url),
        "no_playurl": 0,
        "mask": 1,
        "qn": 10000,
        "platform": "web",
        "protocol": "0,1",
        "format": "0,1,2",
        "codec": "0,1,2",
        "dolby": 5,
        "panorama": 1,
    }


def _quality_label(qn: int, codec: str, stream_format: str) -> str:
    label = BILIBILI_QN_LABELS.get(qn, f"qn{qn}")
    suffix = "/".join(part for part in [codec.upper(), stream_format] if part)
    return f"{label}({qn})/{suffix}" if suffix else f"{label}({qn})"


def _bilibili_cookie_header(cookie_file: Path | None = None) -> str:
    cookie_file = cookie_file or COOKIES_FILE
    if not cookie_file.exists():
        return ""

    pairs: list[str] = []
    text = cookie_file.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, name, value = parts[0].casefold(), parts[5], parts[6]
        if "bilibili" in domain and name and value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _max_accept_qn(payload: dict) -> int:
    data = payload.get("data") or {}
    playurl = ((data.get("playurl_info") or {}).get("playurl") or {})
    max_qn = 0
    for stream in playurl.get("stream") or []:
        for stream_format in stream.get("format") or []:
            for codec in stream_format.get("codec") or []:
                for qn in codec.get("accept_qn") or []:
                    try:
                        max_qn = max(max_qn, int(qn))
                    except (TypeError, ValueError):
                        pass
    return max_qn


def _quality_warning(selected: dict, payload: dict, used_cookie: bool, has_cookie: bool) -> str:
    max_accept = _max_accept_qn(payload)
    selected_qn = int(selected.get("qn") or 0)
    if max_accept <= selected_qn:
        return ""

    wanted_label = BILIBILI_QN_LABELS.get(max_accept, f"qn{max_accept}")
    current_label = BILIBILI_QN_LABELS.get(selected_qn, f"qn{selected_qn}")
    if not has_cookie:
        return f"B站缺少 cookie，无法获取最高画质 {wanted_label}({max_accept})，当前为 {current_label}({selected_qn})"
    if not used_cookie:
        return f"B站 cookie 请求失败或未生效，无法获取最高画质 {wanted_label}({max_accept})，当前为 {current_label}({selected_qn})"
    return f"B站 cookie 未生效或账号无权限，无法获取最高画质 {wanted_label}({max_accept})，当前为 {current_label}({selected_qn})"


def _select_best_from_playinfo(payload: dict) -> dict | None:
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

    return max(
        candidates,
        key=lambda item: (
            item["qn"],
            CODEC_PRIORITY.get(item["codec"], 0),
            FORMAT_PRIORITY.get(item["format"], 0),
            PROTOCOL_PRIORITY.get(item["protocol"], 0),
        ),
    )


async def fetch_best_bilibili_stream(url: str, timeout: float = 8) -> dict | None:
    cookie_header = _bilibili_cookie_header()
    attempts = [(cookie_header, True)] if cookie_header else []
    attempts.append(("", False))

    last_error: Exception | None = None
    for attempt_cookie, uses_cookie in attempts:
        headers = dict(BILIBILI_HEADERS)
        if attempt_cookie:
            headers["Cookie"] = attempt_cookie

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                response = await client.get(BILIBILI_PLAYINFO_API, params=_playinfo_params(url))
                response.raise_for_status()
                payload = response.json()
        except Exception as e:
            last_error = e
            continue

        selected = _select_best_from_playinfo(payload)
        if not selected:
            continue

        selected["used_cookie"] = uses_cookie
        selected["has_cookie"] = bool(cookie_header)
        selected["quality_warning"] = _quality_warning(
            selected,
            payload,
            used_cookie=uses_cookie,
            has_cookie=bool(cookie_header),
        )
        return selected

    if last_error:
        raise last_error
    return None
