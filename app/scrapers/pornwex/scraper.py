from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core import pool


def can_handle(host: str) -> bool:
    return "pornwex.tv" in host.lower()


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = await pool.client.get(url, headers=headers, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    t = getattr(el, "get_text", None)
    if callable(t):
        return t(strip=True) or None
    return None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for k in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(k)
        if v and str(v).strip():
            url = str(v).strip()
            if url.startswith("//"):
                return f"https:{url}"
            return url
    return None


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    # Title
    title_node = soup.select_one("h1")
    title = _text(title_node) or _text(soup.find("title")) or "Unknown Video"

    # Clean title
    for suffix in (" | PornWex", " - PornWex"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]

    # Description
    desc_node = soup.find("meta", property="og:description")
    description = desc_node.get("content") if desc_node else None

    # Thumbnail (og:image)
    og_img = soup.find("meta", property="og:image")
    thumbnail = og_img.get("content") if og_img else None
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    # Video Stream URL
    video_url = None
    streams = []

    # Check for video element sources
    video_el = soup.select_one("video")
    if video_el:
        for source_el in video_el.select("source"):
            src = source_el.get("src")
            if src:
                s_url = f"https:{src}" if src.startswith("//") else src
                q_label = source_el.get("label") or source_el.get("title") or source_el.get("res") or "default"
                if str(q_label).isdigit():
                    q_label += "p"
                streams.append({
                    "url": s_url,
                    "quality": q_label,
                    "format": "hls" if ".m3u8" in s_url else "mp4",
                })

    # Check for embedded player iframes
    for iframe in soup.select("iframe[src]"):
        src = iframe.get("src", "")
        if src and ("http" in src or src.startswith("//")):
            if src.startswith("//"):
                src = f"https:{src}"
            # Treat iframe as a stream source
            streams.append({
                "url": src,
                "quality": "embed",
                "format": "embed",
            })

    # Check for JS player config patterns
    # Pattern 1: flashvars / video_url
    for pattern in [
        r'video_url\s*[:=]\s*["\']([^"\']+)["\']',
        r'file\s*[:=]\s*["\']([^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
        r'source\s*[:=]\s*["\']([^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
        r'video_alt_url\s*[:=]\s*["\']([^"\']+)["\']',
        r'video_alt_url2\s*[:=]\s*["\']([^"\']+)["\']',
    ]:
        matches = re.findall(pattern, html)
        for match in matches:
            s_url = match
            if s_url.startswith("//"):
                s_url = f"https:{s_url}"
            if s_url not in [s["url"] for s in streams]:
                fmt = "hls" if ".m3u8" in s_url else "mp4"
                streams.append({
                    "url": s_url,
                    "quality": "default",
                    "format": fmt,
                })

    # Sort by quality
    def _qval(s):
        q = s["quality"].replace("p", "")
        try:
            return int(q)
        except (ValueError, TypeError):
            return 0

    streams.sort(key=_qval, reverse=True)

    if streams:
        # Prefer non-embed streams
        non_embed = [s for s in streams if s.get("format") != "embed"]
        if non_embed:
            video_url = non_embed[0]["url"]
        else:
            video_url = streams[0]["url"]

    # Tags
    tags = []
    for a in soup.select('a[href*="/tags/"]'):
        t = _text(a)
        if t and t != "-":
            tags.append(t)

    # Duration
    duration = None
    dur_el = soup.select_one(".duration, .video-duration")
    if dur_el:
        duration = _text(dur_el)

    # Views
    views = None
    views_match = re.search(r'(\d[\d,]*)\s*(?:views|Views)', html)
    if views_match:
        views = views_match.group(1)

    # Related Videos
    related_videos = []
    for item in soup.select(".thumb-list .thumb-item, .related-videos .item, .video-item"):
        try:
            link = item.select_one("a[href*='/video/']")
            if not link:
                continue

            r_url = link.get("href", "")
            if r_url.startswith("/"):
                r_url = f"https://www.pornwex.tv{r_url}"

            r_title = link.get("title") or _text(link)
            img = item.select_one("img")
            r_thumb = _best_image_url(img)

            r_dur = None
            dur_tag = item.select_one(".duration, .thumb-duration")
            if dur_tag:
                r_dur = _text(dur_tag)

            related_videos.append({
                "url": r_url,
                "title": r_title,
                "thumbnail_url": r_thumb,
                "duration": r_dur,
            })
            if len(related_videos) >= 10:
                break
        except Exception:
            continue

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "tags": tags,
        "duration": duration,
        "views": views,
        "related_videos": related_videos,
        "video": {
            "default": video_url,
            "has_video": video_url is not None,
            "streams": streams,
        },
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)


def get_categories() -> list[dict[str, Any]]:
    file_path = os.path.join(os.path.dirname(__file__), "categories.json")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    target_url = base_url.rstrip("/")

    if page > 1:
        # PornWex pagination: /{N}/ appended directly (e.g., /latest-updates/2/)
        # Homepage doesn't support pagination, redirect to /latest-updates/ for page > 1
        if target_url.rstrip("/") == "https://www.pornwex.tv" or target_url.rstrip("/") == "https://pornwex.tv":
            target_url = f"https://www.pornwex.tv/latest-updates/{page}/"
        else:
            target_url = f"{target_url}/{page}/"
    elif not target_url.endswith("/"):
        target_url += "/"

    html = await fetch_html(target_url)
    soup = BeautifulSoup(html, "lxml")

    items: list[dict[str, Any]] = []

    # PornWex uses .ml-item containers (WP-Movie theme, same as XXXParodyHD)
    video_cards = soup.select(".ml-item")

    # Fallback: try other common selectors
    if not video_cards:
        for sel in [".videos-list .video-item", ".thumb-list .thumb-item",
                    ".list-videos .item", ".video-list .video-item"]:
            video_cards = soup.select(sel)
            if video_cards:
                break

    # Last fallback: find any container with video links
    if not video_cards:
        for link in soup.select('a[href*="/video/"]'):
            parent = link.find_parent(["div", "li", "article"])
            if parent and parent not in video_cards:
                video_cards.append(parent)

    for card in video_cards:
        try:
            # Primary: a.ml-mask link
            link = card.select_one("a.ml-mask")
            if not link:
                link = card.select_one('a[href*="/video/"]')
            if not link:
                link = card.select_one("a[href]")
            if not link:
                continue

            href = link.get("href", "") or link.get("data-href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = f"https://www.pornwex.tv{href}"

            # Title: .mli-info h2, or link title/oldtitle
            title_el = card.select_one(".mli-info h2")
            title = _text(title_el) if title_el else (
                link.get("oldtitle") or link.get("title") or _text(link)
            )
            if not title:
                title_el = card.select_one("strong, .title, .video-title")
                title = _text(title_el) if title_el else None

            # Thumbnail
            img = card.select_one("img")
            thumb = _best_image_url(img)

            # Duration: .mli-info1 or .duration
            dur_el = card.select_one(".mli-info1, .duration, .thumb-duration, .video-duration")
            duration = _text(dur_el) if dur_el else None

            # Views
            views = None
            views_el = card.select_one(".views, .video-views")
            if views_el:
                views = _text(views_el)

            # Upload time
            time_el = card.select_one(".added, .video-added, .date, time, em")
            upload_time = _text(time_el) if time_el else None

            items.append({
                "url": href,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "upload_time": upload_time,
            })
        except Exception:
            continue

    return items

