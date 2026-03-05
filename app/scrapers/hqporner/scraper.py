from __future__ import annotations

import json
import re
import os
from typing import Any

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    return "hqporner.com" in host.lower()


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_html(url: str) -> str:
    """Fetch HTML with curl_cffi (browser impersonation) and httpx fallback."""
    impersonations = ["chrome120", "chrome110", "safari15_3"]
    last_error = None

    for imp in impersonations:
        try:
            async with AsyncSession(
                impersonate=imp,
                headers={
                    "Referer": "https://hqporner.com/",
                },
                timeout=20.0
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 403:
                    last_error = f"403 Forbidden with {imp}"
                    continue
                resp.raise_for_status()
                return resp.text
        except Exception as e:
            last_error = f"{imp} error: {e}"
            continue

    print(f"⚠️ HQPorner all curl_cffi attempts failed. Last error: {last_error}. Falling back to httpx...")
    from app.core import pool
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    resp = await pool.client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text


async def _extract_video_from_iframe(iframe_src: str) -> dict[str, Any]:
    """Follow the iframe src (usually mydaddy.cc) to extract actual video streams."""
    streams = []
    default_url = None

    try:
        html = await fetch_html(iframe_src)

        # Look for direct video source URLs in the embed page
        # mydaddy.cc typically has video sources in script or source tags
        soup = BeautifulSoup(html, "lxml")

        # 1. Check <source> tags
        for source in soup.select("video source, source"):
            src = source.get("src") or source.get("data-src")
            if src and (src.startswith("http") or src.startswith("//")):
                if src.startswith("//"): src = "https:" + src
                quality = source.get("label") or source.get("size") or source.get("res") or "default"
                fmt = "hls" if ".m3u8" in src else "mp4"
                streams.append({"quality": str(quality), "url": src, "format": fmt})

        # 2. Check for HLS master playlist in scripts
        m3u8_matches = re.findall(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', html)
        for m3u8_url in m3u8_matches:
            m3u8_url = m3u8_url.replace('\\/', '/')
            if m3u8_url not in [s["url"] for s in streams]:
                streams.append({"quality": "auto", "url": m3u8_url, "format": "hls"})

        # 3. Check for MP4 direct links in scripts
        mp4_matches = re.findall(r'(https?://[^\s"\']+\.mp4[^\s"\']*)', html)
        for mp4_url in mp4_matches:
            mp4_url = mp4_url.replace('\\/', '/')
            if mp4_url not in [s["url"] for s in streams]:
                # Try to extract quality from URL
                q_match = re.search(r'(\d{3,4})p', mp4_url)
                quality = q_match.group(1) if q_match else "default"
                streams.append({"quality": quality, "url": mp4_url, "format": "mp4"})

    except Exception as e:
        print(f"⚠️ HQPorner iframe extraction error: {e}")

    # Sort by quality (highest first)
    def quality_rank(s):
        q = s['quality']
        if q.isdigit(): return int(q)
        if q == 'auto': return 5000
        return 0

    streams.sort(key=quality_rank, reverse=True)

    if streams:
        # Prefer HLS, then highest quality MP4
        hls_stream = next((s for s in streams if s.get("format") == "hls"), None)
        if hls_stream:
            default_url = hls_stream["url"]
        else:
            default_url = streams[0]["url"]

    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }


def parse_page(html: str, url: str) -> dict[str, Any]:
    """Parse a single HQPorner video page."""
    soup = BeautifulSoup(html, "lxml")

    # Title
    title = None
    t_tag = soup.select_one("h1.main-h1, h1")
    if t_tag:
        title = t_tag.get_text(strip=True)

    # Thumbnail from og:image
    thumbnail = None
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb:
        thumbnail = meta_thumb.get("content")

    # Duration - from li with clock icon
    duration = "0:00"
    dur_el = soup.select_one("li.icon.fa-clock-o")
    if dur_el:
        duration = dur_el.get_text(strip=True)

    # Actors/Pornstars
    uploader = "HQPorner"
    actors = []
    for actor_el in soup.select("li.icon.fa-star-o a"):
        actor_name = actor_el.get_text(strip=True)
        if actor_name:
            actors.append(actor_name)
    if actors:
        uploader = ", ".join(actors)

    # Tags / Categories
    tags = []
    for tag_el in soup.select("a.tag-link.click-trigger, a.tag-link"):
        txt = tag_el.get_text(strip=True)
        if txt:
            tags.append(txt)

    # Video: Extract iframe src
    video_data = {"streams": [], "default": None, "has_video": False}
    iframe = soup.select_one("iframe")
    iframe_src = None
    if iframe:
        iframe_src = iframe.get("src")
        if iframe_src and iframe_src.startswith("//"):
            iframe_src = "https:" + iframe_src

    return {
        "url": url,
        "title": title or "Unknown",
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": "0",
        "uploader_name": uploader,
        "category": "HQPorner",
        "tags": tags,
        "video": video_data,
        "related_videos": [],
        "preview_url": None,
        "_iframe_src": iframe_src,  # internal: will be used to fetch video
    }


async def scrape(url: str) -> dict[str, Any]:
    """Scrape a single HQPorner video page."""
    html = await fetch_html(url)
    result = parse_page(html, url)

    # Now fetch the actual video from the iframe
    iframe_src = result.pop("_iframe_src", None)
    if iframe_src:
        video_data = await _extract_video_from_iframe(iframe_src)
        result["video"] = video_data

    return result


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    """List videos from an HQPorner listing/category page."""
    url = base_url.rstrip("/")

    # Pagination: handle different URL patterns
    if page > 1:
        if "?" in url:
            # Search URL: /?q=query -> /?q=query&p=2
            url = f"{url}&p={page}"
        elif url == "https://hqporner.com" or url == "http://hqporner.com":
            # Home Page: / -> /hdporn/2
            url = f"{url}/hdporn/{page}"
        else:
            # Category/Top: /category/name -> /category/name/2
            url = f"{url}/{page}"

    try:
        html = await fetch_html(url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    
    # Check for "No results" or "End of list" message
    # HQPorner shows "CAN'T FIND ..." or "SORRY, I CAN'T FIND ..." when no more results
    header = soup.select_one("h1.main-h1, h1")
    if header:
        header_text = header.get_text().upper()
        if "CAN'T FIND" in header_text or "SORRY" in header_text:
            return []

    items = []

    # Video items are in section.box.feature containers
    video_items = soup.select("section.box.feature")

    for item in video_items:
        try:
            # Title & URL from h3.meta-data-title a
            title_link = item.select_one("h3.meta-data-title a")
            if not title_link:
                continue

            href = title_link.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://hqporner.com" + href

            title = title_link.get_text(strip=True) or "Unknown"

            # Thumbnail from image link
            thumb = None
            img = item.select_one("a.image img, a.featured img, img")
            if img:
                thumb = img.get("src") or img.get("data-src")
                if thumb and thumb.startswith("//"):
                    thumb = "https:" + thumb

            # Duration from span with clock icon
            duration = "0:00"
            dur_tag = item.select_one("span.icon.fa-clock-o.meta-data, span.fa-clock-o")
            if dur_tag:
                duration = dur_tag.get_text(strip=True)

            items.append({
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": "0",
                "uploader_name": "HQPorner"
            })

        except Exception:
            continue

    return items
