from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "tnaflix.com" or h.endswith(".tnaflix.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tnaflix.com/",
    }
    return await pool_fetch_html(url, headers=headers)


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    return None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return None


def _normalize_duration(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        total = int(v)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    raw = str(v).strip()
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", raw)
    if m:
        h = int(m.group(1) or 0)
        mm = int(m.group(2) or 0)
        ss = int(m.group(3) or 0)
        return f"{h}:{mm:02d}:{ss:02d}" if h else f"{mm}:{ss:02d}"
    return raw or None


def _clean_views_text(v: str | None) -> Optional[str]:
    if not v:
        return None
    txt = str(v).strip()
    if not txt:
        return None
    txt = txt.replace(",", "").replace("\u00a0", " ")
    # Keep digits and common suffixes; also allow dots for 1.2M
    txt = re.sub(r"[^0-9KMBkmb\. ]", "", txt).strip()
    txt = txt.replace(" ", "")
    return txt or None


def _extract_uploader_and_views_from_badges(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str]]:
    """
    TNAFlix commonly renders uploader + views as small badges, e.g.

      <a href="https://www.tnaflix.com/profile/barton_julio" class="... badge-video-info ...">
        barton_julio
      </a>
      <div class="me-2"><i class="icon-eye"></i>416</div>
    """
    uploader = None
    views = None

    # Uploader: prefer /profile/ links (more specific than /users/ or /channels/)
    # Prefer the user/uploader badge (often has badge-unverified/badge-verified + me-2),
    # not the pornstar badge variants (e.g. badge-kiss).
    up = soup.select_one(
        'a.badge-video-info.badge-unverified[href*="/profile/"],'
        'a.badge-video-info.badge-verified[href*="/profile/"],'
        'a.me-2.badge-video-info[href*="/profile/"],'
        'a.badge-video-info[href*="/profile/"]'
    )
    if up:
        uploader = up.get_text(" ", strip=True) or None

    # Views: icon-eye is the strongest signal; parse nearby text.
    for eye in soup.select("i.icon-eye"):
        # Prefer the badge block like: <div class="me-2"><i class="icon-eye"></i>416</div>
        badge = eye.find_parent(class_="me-2") or eye.parent
        if not badge:
            continue
        raw = badge.get_text(" ", strip=True)
        raw = raw.replace("views", "").replace("view", "")
        parsed = _clean_views_text(raw)
        if parsed:
            views = parsed
            break

    return uploader, views


def _extract_video_urls(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    # Capture both escaped and unescaped forms from inline scripts.
    candidates = re.findall(r'https?://[^"\'\s<>]+(?:\.m3u8|\.mp4)[^"\'\s<>]*', html, flags=re.IGNORECASE)
    candidates += re.findall(r'https?:\\?/\\?/[^"\'\s<>]+(?:\.m3u8|\.mp4)[^"\'\s<>]*', html, flags=re.IGNORECASE)

    for raw in candidates:
        url = raw.replace("\\/", "/").replace("\\u0026", "&")
        if url.startswith("http://") or url.startswith("https://"):
            pass
        elif url.startswith("https://") or url.startswith("http://"):
            pass
        else:
            continue
        if url in seen:
            continue
        seen.add(url)

        # Skip obvious trailers/previews; they pollute the quality list.
        lower_url = url.lower()
        if "/trailer/" in lower_url or lower_url.endswith("/trailer.mp4") or "/trailer.mp4" in lower_url:
            continue

        is_hls = ".m3u8" in url.lower()
        quality = "adaptive" if is_hls else "default"
        qm = re.search(r"(\d{3,4})p", url, re.IGNORECASE)
        if qm:
            quality = f"{qm.group(1)}p"
        streams.append(
            {
                "quality": quality,
                "url": url,
                "format": "hls" if is_hls else "mp4",
            }
        )

    def _score(s: dict[str, str]) -> int:
        q = s.get("quality", "")
        digits = "".join(ch for ch in q if ch.isdigit())
        return int(digits) if digits else 0

    streams.sort(key=lambda s: (_score(s), 1 if s.get("format") == "hls" else 0), reverse=True)

    # Prefer HLS master as default if present, else highest-quality MP4.
    default_url = None
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    if hls:
        default_url = hls.get("url")
    elif streams:
        default_url = streams[0].get("url")
    return {"streams": streams, "default": default_url, "has_video": bool(streams)}


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    title = _first_non_empty(_meta(soup, prop="og:title"), _meta(soup, name="twitter:title"), soup.title.get_text(strip=True) if soup.title else None)
    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))

    duration = None
    views = None
    uploader = None
    tags: list[str] = []
    category = None

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        objs = parsed if isinstance(parsed, list) else [parsed]
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            type_match = t == "VideoObject" or (isinstance(t, list) and "VideoObject" in t)
            if not type_match:
                continue
            title = _first_non_empty(title, obj.get("name"))
            description = _first_non_empty(description, obj.get("description"))
            thumb = obj.get("thumbnailUrl")
            if isinstance(thumb, list) and thumb:
                thumb = thumb[0]
            thumbnail = _first_non_empty(thumbnail, thumb)
            duration = _first_non_empty(duration, _normalize_duration(obj.get("duration")))
            if not views:
                iv = obj.get("interactionCount") or obj.get("viewCount")
                if iv is not None:
                    views = str(iv)
            author = obj.get("author")
            if isinstance(author, dict):
                uploader = _first_non_empty(uploader, author.get("name"))
            elif isinstance(author, str):
                uploader = _first_non_empty(uploader, author)
            genre = obj.get("genre")
            if isinstance(genre, str):
                category = _first_non_empty(category, genre)
            kw = obj.get("keywords")
            if isinstance(kw, str):
                tags.extend([x.strip() for x in kw.split(",") if x.strip()])
            elif isinstance(kw, list):
                tags.extend([str(x).strip() for x in kw if str(x).strip()])

    if not duration:
        text = soup.get_text(" ", strip=True)
        m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
        if m:
            duration = m.group(0)

    if not views:
        # Prefer the explicit badge view counter when present.
        _, badge_views = _extract_uploader_and_views_from_badges(soup)
        if badge_views:
            views = badge_views
        else:
            text = soup.get_text(" ", strip=True)
            m = re.search(r"(\d[\d,\.]*\s*[KMB]?)\s*(?:views|view)\b", text, re.IGNORECASE)
            if m:
                views = _clean_views_text(m.group(1))

    if not uploader:
        badge_uploader, _ = _extract_uploader_and_views_from_badges(soup)
        if badge_uploader:
            uploader = badge_uploader
        else:
            up = soup.select_one('a[href*="/profile/"], a[href*="/channels/"], a[href*="/pornstars/"], a[href*="/users/"]')
            if up:
                uploader = up.get_text(strip=True) or None

    if not tags:
        for a in soup.select('a[href*="/tag/"], a[href*="/tags/"]'):
            t = a.get_text(strip=True)
            if t:
                tags.append(t)
    tags = list(dict.fromkeys(tags))

    video = _extract_video_urls(html)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": category,
        "tags": tags,
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    url = base_url.rstrip("/")
    if page <= 1:
        return url
    # Conservative pagination strategy with query params commonly used across list/search pages.
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}page={page}"


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Generic TNAFlix card discovery. We keep this permissive with strict URL filtering.
    for a in soup.select('a[href]'):
        if len(items) >= limit:
            break
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if "/video" not in href.lower():
            continue
        if href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("/"):
            href = f"https://www.tnaflix.com{href}"
        if not href.startswith("http"):
            continue
        href = href.split("#")[0]
        if href in seen:
            continue

        img = a.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = title.strip() if title else "Unknown Video"
        if not title:
            title = "Unknown Video"

        # Read nearby metadata text when present.
        container = a.find_parent(["article", "li", "div"]) or a
        ctext = container.get_text(" ", strip=True) if container else ""

        duration = None
        dm = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", ctext)
        if dm:
            duration = dm.group(0)

        views = None
        # Prefer icon-eye badge count if present in container
        eye = container.select_one("i.icon-eye") if container else None
        if eye and eye.parent:
            views = _clean_views_text(eye.parent.get_text(" ", strip=True))
        if not views:
            vm = re.search(r"(\d[\d,\.]*\s*[KMB]?)\s*(?:views|view)\b", ctext, re.IGNORECASE)
            if vm:
                views = _clean_views_text(vm.group(1))

        uploader = None
        up = container.select_one('a[href*="/profile/"], a[href*="/channels/"], a[href*="/users/"], a[href*="/pornstars/"]') if container else None
        if up:
            uploader = up.get_text(" ", strip=True) or None

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader,
            }
        )

    return items[:limit]
