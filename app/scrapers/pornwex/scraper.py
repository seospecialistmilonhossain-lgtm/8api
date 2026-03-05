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


def _resolve_kt_url(raw: str) -> str:
    """
    kt_player stores video URLs as 'function/0/<real_url>' where the
    real_url is the /get_file/ endpoint.  Strip the prefix and return
    the bare URL.  If the value is already a plain URL, return as-is.
    """
    # Strip 'function/<n>/' prefix that kt_player uses
    m = re.match(r'^function/\d+/(https?://.+)$', raw.strip())
    if m:
        return m.group(1)
    if raw.startswith("//"):
        return f"https:{raw}"
    return raw


async def _follow_to_direct_url(get_file_url: str) -> str:
    """
    Follow the /get_file/ redirect chain to obtain the final CDN URL
    (typically remote_control.php on a CDN host).
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://www.pornwex.tv/",
    }
    try:
        resp = await pool.client.get(
            get_file_url,
            headers=headers,
            follow_redirects=True,
        )
        # The final URL after redirects is the signed CDN link
        final = str(resp.url)
        # Only accept if it looks like a real media URL
        if any(x in final for x in ("remote_control", ".mp4", ".m3u8")):
            return final
    except Exception:
        pass
    # Fallback: return the get_file URL itself
    return get_file_url


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

    # -------------------------------------------------------------------------
    # Video Stream URL extraction
    # -------------------------------------------------------------------------
    # PornWex uses kt_player.  The player config object in the HTML contains:
    #
    #   video_url: 'function/0/https://www.pornwex.tv/get_file/<hash>/video.mp4/'
    #
    # The "function/0/" prefix is a kt_player directive — strip it to get the
    # /get_file/ URL, then follow its redirect chain to obtain the real signed
    # CDN URL (remote_control.php on a CDN host such as g02s01.pornwex.tv).
    # -------------------------------------------------------------------------
    video_url = None
    streams = []

    # Patterns for kt_player config keys that hold video source URLs.
    # They can be either plain https:// or prefixed with 'function/<n>/'.
    kt_url_keys = r'(?:video_url|video_url_text|video_alt_url|video_alt_url2)'
    kt_patterns = [
        # single-quoted value
        rf"{kt_url_keys}\s*[:\=]\s*'([^']+)'",
        # double-quoted value
        rf'{kt_url_keys}\s*[:\=]\s*"([^"]+)"',
    ]

    raw_urls: list[tuple[str, str]] = []  # (key_name, raw_value)
    for pattern in kt_patterns:
        for m in re.finditer(pattern, html):
            raw = m.group(1).strip()
            key = re.match(r'(\w+)', m.group(0)).group(1)
            if raw and (raw, key) not in [(r, k) for r, k in raw_urls]:
                raw_urls.append((key, raw))

    # Map key to quality hint: video_url = primary, alt variants = fallback
    quality_map = {
        "video_url": "default",
        "video_url_text": "default",
        "video_alt_url": "alt",
        "video_alt_url2": "alt2",
    }

    for key, raw in raw_urls:
        resolved = _resolve_kt_url(raw)
        if not resolved:
            continue
        # Must look like a /get_file/ or https:// media URL
        if "get_file" not in resolved and not re.search(r'\.mp4|\.m3u8', resolved):
            continue
        qual = quality_map.get(key, "default")
        if resolved not in [s["url"] for s in streams]:
            streams.append({
                "url": resolved,
                "quality": qual,
                "format": "hls" if ".m3u8" in resolved else "mp4",
                "_needs_redirect": True,
            })

    # Fallback 1: <video><source> elements
    video_el = soup.select_one("video")
    if video_el:
        for source_el in video_el.select("source"):
            src = source_el.get("src")
            if src:
                s_url = f"https:{src}" if src.startswith("//") else src
                if s_url in [s["url"] for s in streams]:
                    continue
                q_label = (
                    source_el.get("label")
                    or source_el.get("title")
                    or source_el.get("res")
                    or "default"
                )
                if str(q_label).isdigit():
                    q_label += "p"
                streams.append({
                    "url": s_url,
                    "quality": q_label,
                    "format": "hls" if ".m3u8" in s_url else "mp4",
                })

    # Fallback 2: iframes
    for iframe in soup.select("iframe[src]"):
        src = iframe.get("src", "")
        if src and ("http" in src or src.startswith("//")):
            if src.startswith("//"):
                src = f"https:{src}"
            if src in [s["url"] for s in streams]:
                continue
            streams.append({
                "url": src,
                "quality": "embed",
                "format": "embed",
            })

    # Sort: primary first, then fallbacks
    def _qval(s):
        order = {"default": 0, "alt": 1, "alt2": 2, "embed": 99}
        q = s["quality"]
        if q in order:
            return order[q]
        q2 = q.replace("p", "")
        try:
            return -int(q2)  # higher resolution = lower sort key = first
        except (ValueError, TypeError):
            return 50

    streams.sort(key=_qval)

    if streams:
        non_embed = [s for s in streams if s.get("format") != "embed"]
        video_url = non_embed[0]["url"] if non_embed else streams[0]["url"]

    # Tags
    tags = []
    for a in soup.select('a[href*="/tags/"]'):
        t = _text(a)
        if t and t != "-":
            tags.append(t)

    # Duration — prefer schema.org data (seconds), fall back to visible text
    duration = None
    schema_dur = re.search(r'"duration"\s*:\s*"(PT[^"]+)"', html)
    if schema_dur:
        # Convert PT0H41M16S → 41:16
        pt = schema_dur.group(1)
        hm = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', pt)
        if hm:
            h, m, s = (int(x or 0) for x in hm.groups())
            if h:
                duration = f"{h}:{m:02d}:{s:02d}"
            else:
                duration = f"{m}:{s:02d}"
    if not duration:
        dur_el = soup.select_one(".duration, .video-duration, em")
        if dur_el:
            duration = _text(dur_el)

    # Views — prefer schema.org WatchAction count
    views = None
    watch_match = re.search(
        r'"interactionType"\s*:\s*"http://schema\.org/WatchAction"[^}]*"userInteractionCount"\s*:\s*"(\d+)"',
        html,
    )
    if watch_match:
        views = watch_match.group(1)
    if not views:
        views_match = re.search(r'(\d[\d,]*)\s*(?:views|Views)', html)
        if views_match:
            views = views_match.group(1)

    # Related Videos
    related_videos = []
    for item in soup.select(".list-videos .item, .related-videos .item, .video-item"):
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

    # Strip internal _needs_redirect flag before returning
    clean_streams = [
        {k: v for k, v in s.items() if k != "_needs_redirect"}
        for s in streams
    ]

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
            "streams": clean_streams,
        },
        # Internal hint for the caller: these /get_file/ URLs need redirect-following
        "_streams_raw": streams,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    result = parse_page(html, url)

    # Resolve /get_file/ redirect URLs to get the final signed CDN links
    raw_streams = result.pop("_streams_raw", [])
    resolved_streams = []
    for s in raw_streams:
        needs_redirect = s.pop("_needs_redirect", False)
        if needs_redirect and "get_file" in s["url"]:
            s["url"] = await _follow_to_direct_url(s["url"])
            s["format"] = "hls" if ".m3u8" in s["url"] else "mp4"
        resolved_streams.append(s)

    result["video"]["streams"] = resolved_streams
    if resolved_streams:
        non_embed = [s for s in resolved_streams if s.get("format") != "embed"]
        result["video"]["default"] = non_embed[0]["url"] if non_embed else resolved_streams[0]["url"]
        result["video"]["has_video"] = True

    return result


def get_categories() -> list[dict[str, Any]]:
    file_path = os.path.join(os.path.dirname(__file__), "categories.json")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    target_url = base_url.rstrip("/")
    is_search = "/search/" in target_url

    if is_search:
        query_match = re.search(r'/search/([^/]+)', target_url)
        query = query_match.group(1) if query_match else ""

        if page == 1:
            if not target_url.endswith("/"):
                target_url += "/"
            html = await fetch_html(target_url)
        else:
            post_data = {
                "mode": "async",
                "function": "get_block",
                "block_id": "list_videos_videos_list_search_result",
                "q": query,
                "category_ids": "",
                "sort_by": "",
                "from_videos": str(page),
                "from_albums": str(page),
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": target_url + "/",
            }
            resp = await pool.client.post(
                target_url + "/",
                data=post_data,
                headers=headers,
                follow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text
    else:
        if page > 1:
            if target_url in ("https://www.pornwex.tv", "https://pornwex.tv"):
                target_url = f"https://www.pornwex.tv/latest-updates/{page}/"
            else:
                target_url = f"{target_url}/{page}/"
        elif not target_url.endswith("/"):
            target_url += "/"
        html = await fetch_html(target_url)

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []

    video_cards = soup.select(".list-videos .item, .ml-item, .item")

    if not video_cards:
        for sel in [".videos-list .video-item", ".thumb-list .thumb-item",
                    ".video-list .video-item"]:
            video_cards = soup.select(sel)
            if video_cards:
                break
    if not video_cards:
        for link in soup.select('a[href*="/video/"]'):
            parent = link.find_parent(["div", "li", "article"])
            if parent and parent not in video_cards:
                video_cards.append(parent)

    for card in video_cards:
        try:
            link = card.select_one('a[href*="/video/"]')
            if not link:
                link = card.select_one("a.ml-mask")
            if not link:
                link = card.select_one("a[href]")
            if not link:
                continue

            href = link.get("href", "") or link.get("data-href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = f"https://www.pornwex.tv{href}"

            title_el = card.select_one(".title, strong.title, .mli-info h2")
            title = _text(title_el) if title_el else (
                link.get("oldtitle") or link.get("title") or _text(link)
            )
            if not title:
                title_el = card.select_one("strong, .video-title")
                title = _text(title_el) if title_el else None

            img = card.select_one("img")
            thumb = _best_image_url(img)

            dur_el = card.select_one(".duration, .thumb-duration, .video-duration, .mli-info1")
            duration = _text(dur_el) if dur_el else None

            views_el = card.select_one(".views, .video-views")
            views = _text(views_el) if views_el else None

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
