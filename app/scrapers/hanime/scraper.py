from __future__ import annotations
import json
import html as html_lib
import re
import secrets
from typing import Any, Optional
import httpx
from urllib.parse import urlparse
from bs4 import BeautifulSoup

from app.core.pool import fetch_html, fetch_json, post_json

def can_handle(host: str) -> bool:
    return "hanime.tv" in host.lower()

def _extract_slug(url: str) -> Optional[str]:
    """Extract slug from URLs like https://hanime.tv/videos/hentai/[slug]"""
    m = re.search(r"/videos/hentai/([^/?#]+)", url)
    if m:
        return m.group(1)
    return None


async def _fetch_json_resilient(url: str, headers: dict[str, str]) -> dict[str, Any]:
    """Fetch JSON with pool first, then httpx fallback for flaky hosts."""
    try:
        return await fetch_json(url, headers=headers)
    except Exception:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()


async def _fetch_html_resilient(url: str, headers: dict[str, str]) -> str:
    """Fetch HTML with pool first, then httpx fallback for flaky hosts."""
    try:
        return await fetch_html(url, headers=headers)
    except Exception:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text


def _related_from_payload(data: dict[str, Any], current_slug: str) -> list[dict[str, Any]]:
    """Extract related/episode items from known HAnime API structures."""
    related: list[dict[str, Any]] = []
    seen: set[str] = {current_slug}

    def add_many(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            related.append(
                {
                    "url": f"https://hanime.tv/videos/hentai/{slug}",
                    "title": item.get("name") or slug.replace("-", " "),
                    "thumbnail_url": item.get("poster_url") or item.get("cover_url"),
                    "views": str(item.get("views", "0")),
                    "upload_date": item.get("released_at") or item.get("created_at"),
                    "uploader_name": item.get("brand"),
                }
            )

    video_info = data.get("hentai_video", {}) or {}

    # Common related sources seen in HAnime payloads (episodes, franchise, recommendations).
    add_many(video_info.get("hentai_franchise_hentai_videos"))
    add_many(video_info.get("hentai_recommendations"))
    add_many(video_info.get("related_hentai_videos"))
    add_many(data.get("related_hentai_videos"))
    add_many(data.get("hentai_videos"))
    add_many(data.get("recommendations"))

    return related[:48]


def _related_from_html(html: str, current_slug: str) -> list[dict[str, Any]]:
    """Fallback parser for 'More from <series>' / Up Next blocks on page HTML."""
    soup = BeautifulSoup(html, "lxml")
    related: list[dict[str, Any]] = []
    seen: set[str] = {current_slug}

    for card in soup.select(".rc-section .video__item"):
        link = card.select_one("a[href*='/videos/hentai/']")
        if not link:
            continue
        href = (link.get("href") or "").strip()
        if not href:
            continue
        full_url = href if href.startswith("http") else f"https://hanime.tv{href}"
        slug = _extract_slug(full_url)
        if not slug or slug in seen:
            continue
        seen.add(slug)

        title = (
            card.select_one(".video__item__info__title").get_text(strip=True)
            if card.select_one(".video__item__info__title")
            else slug.replace("-", " ")
        )

        # Poster is in inline style: background: url("...") ...
        thumb = None
        image = card.select_one(".video__item__image")
        if image:
            style = image.get("style") or ""
            m = re.search(r'url\(["\']?([^"\')]+)', style)
            if m:
                thumb = html_lib.unescape(m.group(1)).strip()

        subtitle_lines = [
            x.get_text(strip=True)
            for x in card.select(".video__item__info__subtitle__one_liner")
            if x.get_text(strip=True)
        ]
        uploader = subtitle_lines[0] if subtitle_lines else None
        views = "0"
        if len(subtitle_lines) > 1:
            vm = re.search(r"([\d,]+)\s*views?", subtitle_lines[1], re.IGNORECASE)
            if vm:
                views = vm.group(1).replace(",", "")

        related.append(
            {
                "url": full_url,
                "title": title,
                "thumbnail_url": thumb,
                "views": views,
                "upload_date": None,
                "uploader_name": uploader,
            }
        )

    if related:
        return related[:48]

    # Target the exact "More from ... series" container first.
    section_match = re.search(
        r'<div class="rc-section">.*?</div>\s*</div>\s*</div>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    section_html = html_lib.unescape(section_match.group(0)) if section_match else html_lib.unescape(html)

    # Fallback: parse raw card blocks when DOM selectors fail on minified/SSR variants.
    block_re = re.compile(
        r'<div class="video__item[^"]*".*?<a[^>]+href="(?P<href>/videos/hentai/[^"]+)"[^>]*>.*?'
        r'<div class="video__item__image"[^>]*style="(?P<style>[^"]*)"[^>]*>.*?'
        r'<div class="video__item__info__title">(?P<title>[^<]+)</div>.*?'
        r'(?:<div class="video__item__info__subtitle__one_liner">(?P<uploader>[^<]*)</div>.*?'
        r'<div class="video__item__info__subtitle__one_liner">(?P<views>[^<]*)</div>)?',
        re.IGNORECASE | re.DOTALL,
    )
    for m in block_re.finditer(section_html):
        href = (m.group("href") or "").strip()
        full_url = f"https://hanime.tv{href}" if href.startswith("/") else href
        slug = _extract_slug(full_url)
        if not slug or slug in seen:
            continue
        seen.add(slug)

        style = m.group("style") or ""
        thumb = None
        tm = re.search(r'url\(["\']?([^"\')]+)', style)
        if tm:
            thumb = html_lib.unescape(tm.group(1)).strip()

        title = (m.group("title") or "").strip() or slug.replace("-", " ")
        uploader = (m.group("uploader") or "").strip() or None
        views = "0"
        raw_views = (m.group("views") or "").strip()
        vm = re.search(r"([\d,]+)\s*views?", raw_views, re.IGNORECASE)
        if vm:
            views = vm.group(1).replace(",", "")

        related.append(
            {
                "url": full_url,
                "title": title,
                "thumbnail_url": thumb,
                "views": views,
                "upload_date": None,
                "uploader_name": uploader,
            }
        )

    return related[:48]


def _related_from_nuxt_state(html: str, current_slug: str) -> list[dict[str, Any]]:
    """
    Parse related franchise videos from `window.__NUXT__` serialized state.
    This is a robust fallback when the rendered card markup is absent.
    """
    related: list[dict[str, Any]] = []
    seen: set[str] = {current_slug}

    m = re.search(
        r"hentai_franchise_hentai_videos:\[(.*?)\],hentai_video_storyboards:",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return related
    chunk = m.group(1)

    def append_slug(slug: str) -> None:
        nonlocal related, seen
        slug = slug.strip()
        if not slug or slug in seen:
            return
        seen.add(slug)

        # Locate the closest object literal containing this slug.
        obj_match = re.search(
            r"\{[^{}]*slug:\"" + re.escape(slug) + r"\"[^{}]*\}",
            chunk,
            re.IGNORECASE | re.DOTALL,
        )
        obj = obj_match.group(0) if obj_match else ""
        title_match = re.search(r'name:"([^"]+)"', obj, re.IGNORECASE)
        poster_match = re.search(r'poster_url:"([^"]+)"', obj, re.IGNORECASE)
        cover_match = re.search(r'cover_url:"([^"]+)"', obj, re.IGNORECASE)

        thumb = None
        if poster_match:
            thumb = html_lib.unescape(poster_match.group(1)).strip()
        elif cover_match:
            thumb = html_lib.unescape(cover_match.group(1)).strip()
        else:
            # Fallback: search globally for this slug's poster/cover reference.
            global_poster = re.search(
                re.escape(slug) + r'[^"\n\r]{0,200}?(https?://[^"\']+?\.(?:png|jpe?g|webp))',
                html_lib.unescape(html),
                re.IGNORECASE,
            )
            if global_poster:
                thumb = global_poster.group(1).strip()

        related.append(
            {
                "url": f"https://hanime.tv/videos/hentai/{slug}",
                "title": (title_match.group(1).strip() if title_match else slug.replace("-", " ").title()),
                "thumbnail_url": thumb,
                "views": "0",
                "upload_date": None,
                "uploader_name": None,
            }
        )

    # Best-effort extraction: explicit slugs in franchise chunk.
    for slug in re.findall(r'slug:"([^"]+)"', chunk, re.IGNORECASE):
        append_slug(slug)

    # Additional fallback: extract any /videos/hentai/<slug> references from full page source.
    decoded_html = html_lib.unescape(html)
    for slug in re.findall(r"/videos/hentai/([a-z0-9\-]+)", decoded_html, re.IGNORECASE):
        append_slug(slug)

    return related[:48]

async def scrape(url: str) -> dict[str, Any]:
    slug = _extract_slug(url)
    if not slug:
        raise ValueError(f"Could not extract slug from URL: {url}")

    # Use the v8 API to get video details
    api_url = f"https://hanime.tv/api/v8/video?id={slug}"
    
    # HAnime.tv often requires specific headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://hanime.tv/",
        "X-Signature-Version": "web2",
        "X-Signature": secrets.token_hex(32)
    }
    
    data = await _fetch_json_resilient(api_url, headers=headers)
    
    video_info = data.get("hentai_video", {})
    if not video_info:
        raise ValueError(f"No video information found for slug: {slug}")

    # Metadata
    title = video_info.get("name")
    description = video_info.get("description")
    thumbnail = video_info.get("poster_url") or video_info.get("cover_url")
    views = str(video_info.get("views", "0"))
    upload_date = video_info.get("released_at") # Or created_at_unix
    duration = None # HAnime doesn't always provide duration in main object
    
    tags = [tag.get("text") for tag in video_info.get("hentai_tags", []) if tag.get("text")]
    uploader = video_info.get("brand")

    # Streams
    streams = []
    default_url = None
    
    manifest = data.get("videos_manifest", {})
    servers = manifest.get("servers", [])
    
    for server in servers:
        server_name = server.get("name", "Unknown Server")
        for stream in server.get("streams", []):
            url_stream = stream.get("url")
            if not url_stream:
                continue
                
            quality = stream.get("height", "unknown")
            quality_str = f"{quality}p" if str(quality).isdigit() else quality
            
            # Format detection
            ext = "hls" if ".m3u8" in url_stream.lower() else "mp4"
            
            streams.append({
                "quality": quality_str,
                "url": url_stream,
                "format": ext,
                "server": server_name
            })
            
            # Use the highest quality as default
            if not default_url:
                default_url = url_stream

    video_data = {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

    related_videos = _related_from_payload(data, slug)
    if not related_videos:
        try:
            html = await _fetch_html_resilient(url, headers=headers)
            related_videos = _related_from_html(html, slug)
            if not related_videos:
                related_videos = _related_from_nuxt_state(html, slug)
        except Exception:
            # Keep scraper resilient: related remains optional metadata.
            related_videos = []

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "upload_date": upload_date,
        "uploader_name": uploader,
        "category": None,
        "tags": tags,
        "video": video_data,
        "related_videos": related_videos
    }


async def get_related_videos(url: str) -> list[dict[str, Any]]:
    """
    Lightweight related-only fetch for /videos/related endpoint.
    Avoids full scrape dependency and is more resilient for Hanime.
    """
    slug = _extract_slug(url)
    if not slug:
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://hanime.tv/",
        "X-Signature-Version": "web2",
        "X-Signature": secrets.token_hex(32)
    }

    try:
        api_url = f"https://hanime.tv/api/v8/video?id={slug}"
        data = await _fetch_json_resilient(api_url, headers=headers)
        related = _related_from_payload(data, slug)
        if related:
            return related
    except Exception:
        # Continue to HTML/Nuxt fallbacks below
        pass

    try:
        html = await _fetch_html_resilient(url, headers=headers)
        related = _related_from_html(html, slug)
        if related:
            return related
        return _related_from_nuxt_state(html, slug)
    except Exception:
        return []

async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, object]]:
    """List videos from hanime.tv API"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://hanime.tv/",
        "X-Signature-Version": "web2",
        "X-Signature": secrets.token_hex(32)
    }
    
    # API is 0-indexed using 'p' parameter
    api_page = page - 1 if page > 0 else 0
    
    api_url = f"https://hanime.tv/api/v8/browse-trending?time=month&p={api_page}"
    if "browse-new-releases" in base_url or "newest" in base_url.lower():
        api_url = f"https://hanime.tv/api/v8/browse-new-releases?p={api_page}"
    
    # Default to newest if search fails or no query
    search_api = "https://search.htv-services.com/"
    
    # Map common URLs to search parameters
    order_by = "created_at_unix"
    ordering = "desc"
    search_text = ""
    
    if "browse-trending" in base_url or "trending" in base_url.lower():
        order_by = "views"
        # Or order_by: "monthly_rank"
    
    parsed = urlparse(base_url)
    params = dict(p.split('=') for p in parsed.query.split('&') if '=' in p)
    search_text = params.get('q', search_text)

    payload = {
        "search_text": search_text,
        "tags": [],
        "tags_mode": "and",
        "brands": [],
        "blacklist": [],
        "order_by": order_by,
        "ordering": ordering,
        "page": api_page
    }

    try:
        data = await post_json(search_api, payload, headers=headers)
        hits = data.get("hits", [])
        if isinstance(hits, str):
            hits = json.loads(hits)
            
        items = []
        for v in hits:
            slug = v.get("slug")
            if not slug: continue
            items.append({
                "url": f"https://hanime.tv/videos/hentai/{slug}",
                "title": v.get("name"),
                "thumbnail_url": v.get("poster_url") or v.get("cover_url"),
                "duration": None,
                "views": str(v.get("views", "0")),
                "upload_date": None,
                "uploader_name": v.get("brand")
            })
        return items
    except Exception as e:
        print(f"Error using hanime search api: {e}")
        # Fallback to the original browse API if search API fails
        try:
            data = await fetch_json(api_url, headers=headers)
            videos = data.get("hentai_videos", [])
            items = []
            for v in videos:
                slug = v.get("slug")
                if not slug: continue
                items.append({
                    "url": f"https://hanime.tv/videos/hentai/{slug}",
                    "title": v.get("name"),
                    "thumbnail_url": v.get("poster_url") or v.get("cover_url"),
                    "duration": None,
                    "views": str(v.get("views", "0")),
                    "upload_date": None,
                    "uploader_name": v.get("brand")
                })
            return items
        except Exception as e2:
            print(f"Error falling back to hanime browse api: {e2}")
            return []

def get_categories() -> list[dict[str, object]]:
    """Load categories from categories.json"""
    import os
    try:
        path = os.path.join(os.path.dirname(__file__), 'categories.json')
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading hanime categories: {e}")
    return []
