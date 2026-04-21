from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

from app.core.pool import fetch_json as pool_fetch_json


API_BASE = "https://api.hentaiser.app/v1/videos"
MEDIA_HOST = "https://media2.hentaiser.com"


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return (
        h == "app.hentaiser.app"
        or h == "api.hentaiser.app"
        or h.endswith(".hentaiser.app")
        or h == "media2.hentaiser.com"
        or h.endswith(".hentaiser.com")
    )


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _coerce_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "results", "items", "videos"):
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        if all(k in data for k in ("id", "title")):
            return [data]
    return []


def _ensure_absolute_media(url_or_path: Optional[str]) -> Optional[str]:
    if not url_or_path:
        return None
    raw = str(url_or_path).strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if not raw.startswith("/"):
        raw = "/" + raw
    return f"{MEDIA_HOST}{raw}"


def _extract_media_path(url_or_path: Optional[str]) -> Optional[str]:
    if not url_or_path:
        return None
    raw = str(url_or_path).strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        p = urlparse(raw)
        return p.path if p.path else None
    return raw if raw.startswith("/") else f"/{raw}"


def _build_video_stream(item: dict[str, Any]) -> dict[str, Any]:
    video_url = _ensure_absolute_media(
        _first_non_empty(
            item.get("video"),
            item.get("video_url"),
            item.get("mp4"),
            item.get("file"),
            item.get("src"),
        )
    )
    streams: list[dict[str, str]] = []
    if video_url:
        streams.append({"quality": "source", "url": video_url, "format": "mp4"})
    return {
        "streams": streams,
        "hls": None,
        "default": video_url,
        "has_video": bool(video_url),
    }


def _to_list_item(item: dict[str, Any]) -> dict[str, Any]:
    thumbnail_url = _ensure_absolute_media(
        _first_non_empty(
            item.get("thumbnail"),
            item.get("thumbnail_url"),
            item.get("thumb"),
            item.get("preview"),
            item.get("poster"),
            item.get("image"),
        )
    )
    page_url = _first_non_empty(
        item.get("url"),
        item.get("page_url"),
        item.get("link"),
        item.get("permalink"),
    )
    if not page_url:
        item_id = _first_non_empty(item.get("id"), item.get("slug"))
        if item_id:
            page_url = f"https://app.hentaiser.app/video/{item_id}"
        else:
            page_url = "https://app.hentaiser.app/"

    return {
        "url": page_url,
        "title": _first_non_empty(item.get("title"), item.get("name"), "Unknown Video"),
        "thumbnail_url": thumbnail_url,
        "duration": _first_non_empty(item.get("duration"), item.get("length")),
        "views": _first_non_empty(item.get("views"), item.get("view_count")),
        "uploader_name": _first_non_empty(item.get("uploader"), item.get("author"), item.get("username")),
    }


def _to_scrape_item(item: dict[str, Any], url: str) -> dict[str, Any]:
    base = _to_list_item(item)
    thumbnail_url = base.get("thumbnail_url")
    video = _build_video_stream(item)
    return {
        "url": url,
        "title": base.get("title"),
        "description": _first_non_empty(item.get("description"), item.get("desc")),
        "thumbnail_url": thumbnail_url,
        "duration": base.get("duration"),
        "views": base.get("views"),
        "uploader_name": base.get("uploader_name"),
        "category": _first_non_empty(item.get("category"), item.get("genre")),
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        "video": video,
        "related_videos": [],
        "preview_url": None,
        "thumbnail_id": _extract_media_path(thumbnail_url),
        "video_id": _extract_media_path(video.get("default")),
        "media_host": MEDIA_HOST,
    }


async def _fetch_videos(params: dict[str, Any]) -> list[dict[str, Any]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://app.hentaiser.app/",
    }
    data = await pool_fetch_json(API_BASE, headers=headers, params=params)
    return _coerce_list(data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    safe_page = max(1, int(page))
    safe_limit = min(max(1, int(limit)), 200)

    query_params: dict[str, Any] = {"limit": safe_limit}
    try:
        parsed = urlparse(base_url)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False):
            if k and v:
                query_params[k] = v
    except Exception:
        pass

    query_params.setdefault("sort", "comments")
    query_params.setdefault("top", "1")

    # Try common pagination styles used by JSON APIs.
    if safe_page > 1:
        query_params.setdefault("page", safe_page)
        query_params.setdefault("offset", (safe_page - 1) * safe_limit)

    try:
        rows = await _fetch_videos(query_params)
    except Exception:
        if safe_page > 1 and "offset" in query_params:
            query_params.pop("offset", None)
            try:
                rows = await _fetch_videos(query_params)
            except Exception:
                return []
        else:
            return []

    out: list[dict[str, Any]] = []
    for row in rows[:safe_limit]:
        out.append(_to_list_item(row))
    return out


async def scrape(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()

    # Direct media URL (mp4/jpg) path support.
    if host.endswith("hentaiser.com"):
        media_path = parsed.path or ""
        if media_path.lower().endswith(".mp4"):
            stream_url = _ensure_absolute_media(media_path)
            return {
                "url": url,
                "title": "Hentaiser Video",
                "description": None,
                "thumbnail_url": None,
                "duration": None,
                "views": None,
                "uploader_name": None,
                "category": None,
                "tags": [],
                "video": {
                    "streams": [{"quality": "source", "url": stream_url, "format": "mp4"}],
                    "hls": None,
                    "default": stream_url,
                    "has_video": True,
                },
                "related_videos": [],
                "preview_url": None,
                "thumbnail_id": None,
                "video_id": _extract_media_path(stream_url),
                "media_host": MEDIA_HOST,
            }

    # API-first scrape: take best first item from "top comments" feed.
    rows = await _fetch_videos({"sort": "comments", "top": 1, "limit": 1})
    if not rows:
        return {
            "url": url,
            "title": "Hentaiser",
            "description": None,
            "thumbnail_url": None,
            "duration": None,
            "views": None,
            "uploader_name": None,
            "category": None,
            "tags": [],
            "video": {"streams": [], "hls": None, "default": None, "has_video": False},
            "related_videos": [],
            "preview_url": None,
            "thumbnail_id": None,
            "video_id": None,
            "media_host": MEDIA_HOST,
        }
    return _to_scrape_item(rows[0], url)
