from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    return host.lower().endswith("pornxp.io") or host.lower().endswith("pornxp.hn")


from app.core import pool

async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = await pool.client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


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
    title_node = soup.select_one(".player_details h1")
    title = _text(title_node) or _text(soup.find("title")) or "Unknown Video"
    
    # Clean title
    for suffix in (" &ndash; PornXP", " - PornXP"):
        if title.endswith(suffix):
            title = title[:-len(suffix)]

    # Description/Details
    desc_node = soup.select_one("#desc")
    description = _text(desc_node)

    # Thumbnail
    video_el = soup.select_one("video#player")
    thumbnail = None
    if video_el:
        poster = video_el.get("poster")
        if poster:
            thumbnail = f"https:{poster}" if poster.startswith("//") else poster

    # Video Stream URL
    video_url = None
    streams = []
    if video_el:
        for source_el in video_el.select("source"):
            src = source_el.get("src")
            if src:
                s_url = f"https:{src}" if src.startswith("//") else src
                q_label = source_el.get("title") or source_el.get("label") or "360p"
                if q_label.isdigit():
                    q_label += "p"
                streams.append({
                    "url": s_url,
                    "quality": q_label
                })
        if streams:
            def _qval(s):
                try:
                    return int(s["quality"].replace("p", ""))
                except:
                    return 0
            streams.sort(key=_qval, reverse=True)
            video_url = streams[0]["url"]

    # Tags
    tags = []
    for a in soup.select(".tags a"):
        t = _text(a)
        if t:
            tags.append(t)

    # Related Videos
    related_videos = []
    for item in soup.select(".item_cont"):
        try:
            link = item.select_one("a[href^='/videos/']")
            if not link:
                continue
            
            r_url = link.get("href")
            r_title = _text(item.select_one(".item_title"))
            r_dur = _text(item.select_one(".item_dur"))
            
            img = item.select_one(".item_img")
            r_thumb = _best_image_url(img)

            related_videos.append({
                "url": f"https://pornxp.io{r_url}" if r_url.startswith("/") else r_url,
                "title": r_title,
                "thumbnail_url": r_thumb,
                "duration": r_dur
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
        "related_videos": related_videos,
        "video": {
            "default": video_url,
            "has_video": video_url is not None,
            "streams": streams
        }
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)



def get_categories() -> list[dict[str, Any]]:
    import os
    file_path = os.path.join(os.path.dirname(__file__), "categories.json")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # PornXP uses 1-based page index in URLs or similar?
    # Based on search exploration: https://pornxp.io/search?q=QUERY
    # Based on menu: https://pornxp.io/best/
    
    target_url = base_url
    if page > 1:
        sep = "&" if "?" in base_url else "?"
        target_url = f"{base_url}{sep}p={page}"

    html = await fetch_html(target_url)
    soup = BeautifulSoup(html, "lxml")

    items: list[dict[str, Any]] = []
    for cont in soup.select(".item_cont"):
        try:
            item = cont.select_one(".item")
            if not item: continue
            
            link = cont.select_one("a[href^='/videos/']")
            if not link: continue
            
            href = link.get("href")
            abs_url = f"https://pornxp.io{href}" if href.startswith("/") else href
            
            title = _text(cont.select_one(".item_title"))
            duration = _text(cont.select_one(".item_dur"))
            
            img = cont.select_one(".item_img")
            thumb = _best_image_url(img)
            
            # Preview video (if available)
            preview_url = item.get("data-preview")
            if preview_url and preview_url.startswith("//"):
                preview_url = f"https:{preview_url}"

            items.append({
                "url": abs_url,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "preview_url": preview_url
            })
        except Exception:
            continue
            
    return items
