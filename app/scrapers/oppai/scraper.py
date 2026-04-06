from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlencode, urlunparse, urljoin

import httpx
from bs4 import BeautifulSoup

BASE = "https://oppai.stream"
RESULTS_API = f"{BASE}/actions/results.php"
SEARCH_API = f"{BASE}/actions/search.php"

_MPD_RE = re.compile(r'https://s2\.myspacecat\.pictures/[^"\'\\s<>]+\.mpd', re.I)
_MP4_RE = re.compile(r'https://(?:s2\.)?myspacecat\.pictures/[^"\'\\s<>]+\.mp4', re.I)
_VSRC_MPD_RE = re.compile(
    r'vsource\s*\[\s*["\']r-(?:720|1080|4k)["\']\s*\]\s*=\s*["\'](https://s2\.myspacecat\.pictures/[^"\']+\.mpd)["\']',
    re.I,
)

def can_handle(host: str) -> bool:
    h = (host or "").lower()
    if h == "oppai.stream" or (h.endswith(".oppai.stream") and not h.startswith("read.") and not h.startswith("rule34.")):
        return True
    return False


def _default_http_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{BASE}/",
    }


def get_categories() -> list[dict[str, Any]]:
    """Load categories from categories.json (feeds + genre tags)."""
    try:
        path = os.path.join(os.path.dirname(__file__), "categories.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        print(f"Error loading oppai categories: {e}")
    return []


async def fetch_html(url: str) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(25.0, connect=20.0),
        headers=_default_http_headers(),
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def _normalize_watch_url(url: str) -> str:
    raw = (url or "").strip()
    p = urlparse(raw)
    if not p.netloc or not can_handle(p.netloc):
        raise ValueError("URL must be on oppai.stream (main site)")
    path = (p.path or "").rstrip("/") or "/"
    if "watch" not in path and "/watch" not in raw.lower():
        raise ValueError("Expected an episode URL such as https://oppai.stream/watch?e=Series-Name-1")
    qs = parse_qs(p.query)
    slug_list = qs.get("e") or qs.get("E")
    if not slug_list or not slug_list[0]:
        raise ValueError("Missing episode slug: use ?e=... in the watch URL")
    slug = slug_list[0].strip()
    q = urlencode({"e": slug})
    return urlunparse(("https", "oppai.stream", "/watch", "", q, ""))


def _meta_content(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> str | None:
    attrs = {}
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
    else:
        tag = soup.find("meta", attrs={"name": name})
    if tag and tag.get("content"):
        return str(tag.get("content")).strip()
    return None


def _quality_rank(label: str) -> int:
    q = (label or "").lower().replace("p", "")
    if q in ("4k", "2160", "uhd"):
        return 4
    if q in ("1080",):
        return 3
    if q in ("720",):
        return 2
    if q in ("480",):
        return 1
    return 0


def _quality_from_stream_url(stream_url: str) -> str:
    lower = stream_url.lower()
    if "/4k/" in lower or "/2160/" in lower:
        return "4k"
    if "/1080/" in lower:
        return "1080p"
    if "/720/" in lower:
        return "720p"
    if "/480/" in lower:
        return "480p"
    return "unknown"


def _extract_streams(html: str, soup: BeautifulSoup) -> tuple[list[dict[str, Any]], str | None]:
    streams: list[dict[str, Any]] = []
    seen: set[str] = set()

    video_el = soup.find("video", id="episode")
    if video_el:
        for src in video_el.find_all("source"):
            u = src.get("src")
            if not u or ".mp4" not in u.lower():
                continue
            if u in seen:
                continue
            seen.add(u)
            q = _quality_from_stream_url(u)
            streams.append(
                {
                    "quality": q,
                    "url": u,
                    "format": "mp4",
                    "server": "myspacecat",
                }
            )

    for u in _MPD_RE.findall(html):
        if u in seen:
            continue
        seen.add(u)
        q = _quality_from_stream_url(u)
        streams.append(
            {
                "quality": q,
                "url": u,
                "format": "dash",
                "server": "myspacecat-dash",
            }
        )

    for m in _VSRC_MPD_RE.finditer(html):
        u = m.group(1)
        if u in seen:
            continue
        seen.add(u)
        streams.append(
            {
                "quality": _quality_from_stream_url(u),
                "url": u,
                "format": "dash",
                "server": "myspacecat-dash",
            }
        )

    # Fallback: any mp4 URL embedded in scripts
    for u in _MP4_RE.findall(html):
        if u in seen:
            continue
        seen.add(u)
        streams.append(
            {
                "quality": _quality_from_stream_url(u),
                "url": u,
                "format": "mp4",
                "server": "myspacecat",
            }
        )

    streams.sort(key=lambda s: _quality_rank(str(s.get("quality", ""))), reverse=True)
    default_url: str | None = None
    mp4_only = [s for s in streams if s.get("format") == "mp4"]
    if mp4_only:
        default_url = max(mp4_only, key=lambda s: _quality_rank(str(s.get("quality", ""))))["url"]
    elif streams:
        default_url = streams[0]["url"]
    return streams, default_url


def _title_from_page(soup: BeautifulSoup) -> str | None:
    h1 = soup.select_one("h1.white.bebas.line-2")
    if h1:
        t = h1.get_text(strip=True)
        if t:
            return t
    og = _meta_content(soup, prop="og:title")
    if og:
        og = re.sub(r"^\s*Watch\s+", "", og, flags=re.I)
        og = re.sub(r"\s+on\s+Oppai\.Stream\s*$", "", og, flags=re.I)
        return og.strip() or None
    tag = soup.find("title")
    if tag:
        return tag.get_text(strip=True) or None
    return None


def _description_from_page(soup: BeautifulSoup) -> str | None:
    desc_el = soup.select_one(".description h5")
    if desc_el:
        text = desc_el.get_text("\n", strip=True)
        if text:
            return text
    og = _meta_content(soup, prop="og:description")
    if og:
        main = og.split("|", 1)[0].strip()
        return main or og
    return None


def _tags_from_page(soup: BeautifulSoup) -> list[str]:
    out: list[str] = []
    for a in soup.select(".tags a.tag h5"):
        t = a.get_text(strip=True)
        if t:
            out.append(t)
    return out


def _studio_from_page(soup: BeautifulSoup) -> str | None:
    a = soup.select_one('a[href*="search?studio="]')
    if not a:
        return None
    href = a.get("href") or ""
    if "studio=" not in href:
        return None
    q = urlparse(href).query
    for part in q.split("&"):
        if part.startswith("studio="):
            return unquote(part.split("=", 1)[1]).replace("+", " ").strip() or None
    return None


def _related_videos(soup: BeautifulSoup, current_slug: str) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    seen: set[str] = set()
    for grid in soup.select("div.episode-shown"):
        link = grid.select_one("a[href*='watch?e=']")
        if not link:
            continue
        href = link.get("href") or ""
        q = parse_qs(urlparse(href).query)
        # Oppai has multiple episode grids on the watch page (same series eps vs recommended).
        # "More Episodes" uses `for=episode-more`; recommended uses other `for` values.
        for_param = (q.get("for") or [""])[0].strip().lower()
        if for_param != "episode-more":
            continue
        slug = (q.get("e") or [None])[0]
        if not slug or slug == current_slug or slug in seen:
            continue
        seen.add(slug)
        name = (grid.get("name") or "").strip()
        ep = (grid.get("ep") or "").strip()
        title = f"{name} {ep}".strip() or slug.replace("-", " ")
        thumb_el = grid.select_one("img.cover-img-in")
        thumb = None
        if thumb_el:
            thumb = thumb_el.get("original") or thumb_el.get("src")
        full_href = href if href.startswith("http") else urljoin(BASE, href)
        item: dict[str, Any] = {
            "url": _normalize_watch_url(full_href),
            "title": title,
            "thumbnail_url": thumb,
        }
        tags_raw = grid.get("tags")
        if tags_raw:
            item["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        related.append(item)
    return related[:48]


def _results_section_key(base_url: str) -> str:
    """Map browse URL to results.php sc=… (matches site sections)."""
    p = urlparse(base_url or "")
    qs = parse_qs(p.query)
    a_val = (qs.get("a") or qs.get("A") or [""])[0].lower().strip()
    if a_val == "trending":
        return "weekly-views"
    if a_val == "random":
        return "random"
    if a_val == "recent":
        return "recent"
    if a_val == "uploaded":
        return "uploaded"

    u = (base_url or "").lower()
    path = (p.path or "").lower()
    if "weekly-views" in u or "trending" in u or "/trending" in path or "top-10" in u or "top10" in u:
        return "weekly-views"
    if "random" in u:
        return "random"
    if "for-you" in u or "for_you" in u or ("recent" in u and "uploaded" not in u and "search" not in u):
        return "recent"
    return "uploaded"


def _order_for_search_api(qs: dict[str, list[str]]) -> str:
    """Map search page ?a=… to actions/search.php order=."""
    a = (qs.get("a") or [""])[0].lower().strip()
    if a == "trending":
        return "views"
    if a == "uploaded":
        return "uploaded"
    if a == "random":
        return "random"
    return "recent"


def _browse_search_api_url(base_url: str, page: int, limit: int) -> str | None:
    """
    Build actions/search.php URL from /search?… browse links.
    Supports site-style params: t text, g genres, b blacklist, s studio, a sort.
    """
    p = urlparse(base_url or "")
    qs = parse_qs(p.query)
    text = (qs.get("t") or qs.get("q") or [""])[0].strip()
    genres = (qs.get("g") or [""])[0].strip()
    blacklist = (qs.get("b") or [""])[0].strip()
    studio = (qs.get("s") or qs.get("studio") or [""])[0].strip()
    if not any([text, genres, blacklist, studio]):
        return None
    order = _order_for_search_api(qs)
    params = urlencode(
        {
            "text": text,
            "order": order,
            "page": str(page),
            "limit": str(limit),
            "genres": genres,
            "blacklist": blacklist,
            "studio": studio,
            "ibt": "0",
            "swa": "",
        }
    )
    return f"{SEARCH_API}?{params}"


def _category_browse_url(base_url: str, page: int, limit: int) -> str | None:
    """Legacy /category/{slug} → search API genres filter."""
    parts = [x for x in (urlparse(base_url or "").path or "").strip("/").split("/") if x]
    if len(parts) < 2 or parts[0].lower() != "category":
        return None
    slug = parts[1].strip()
    if not slug:
        return None
    params = urlencode(
        {
            "text": "",
            "order": "recent",
            "page": str(page),
            "limit": str(limit),
            "genres": slug,
            "blacklist": "",
            "studio": "",
            "ibt": "0",
            "swa": "",
        }
    )
    return f"{SEARCH_API}?{params}"


async def scrape(url: str) -> dict[str, Any]:
    watch_url = _normalize_watch_url(url)
    html = await fetch_html(watch_url)
    soup = BeautifulSoup(html, "lxml")
    slug = parse_qs(urlparse(watch_url).query).get("e", [""])[0]

    title = _title_from_page(soup)
    thumbnail = _meta_content(soup, prop="og:image")
    if not thumbnail and soup.find("video", id="episode"):
        thumbnail = soup.find("video", id="episode").get("poster")

    description = _description_from_page(soup)
    tags = _tags_from_page(soup)
    uploader = _studio_from_page(soup)

    streams, default_url = _extract_streams(html, soup)
    video_data = {
        "streams": streams,
        "default": default_url,
        "hls": None,
        "has_video": bool(default_url),
    }

    return {
        "url": watch_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "upload_date": None,
        "uploader_name": uploader,
        "category": None,
        "tags": tags,
        "video": video_data,
        "related_videos": _related_videos(soup, slug),
    }


def _parse_list_html(fragment: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(fragment, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for grid in soup.select("div.episode-shown"):
        link = grid.select_one("a[href*='watch?e=']")
        if not link:
            continue
        href = link.get("href") or ""
        q = parse_qs(urlparse(href).query)
        slug = (q.get("e") or [None])[0]
        if not slug or slug in seen:
            continue
        seen.add(slug)
        name = (grid.get("name") or "").strip()
        ep = (grid.get("ep") or "").strip()
        title = f"{name} {ep}".strip() or slug.replace("-", " ")
        thumb_el = grid.select_one("img.cover-img-in")
        thumb = None
        if thumb_el:
            thumb = thumb_el.get("original") or thumb_el.get("src")

        views = None
        stat_heads = grid.select(".stats-flex .stats-in h6.gray")
        if stat_heads:
            views = stat_heads[0].get_text(strip=True)

        tags_raw = grid.get("tags")
        tags = [t.strip() for t in tags_raw.split(",")] if tags_raw else None

        watch = href if href.startswith("http") else f"{BASE}{href if href.startswith('/') else '/' + href}"
        items.append(
            {
                "url": _normalize_watch_url(watch),
                "title": title,
                "thumbnail_url": thumb,
                "duration": None,
                "views": views,
                "upload_date": None,
                "uploader_name": None,
                "tags": tags,
            }
        )
    return items


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page = max(1, page)
    limit = min(max(1, limit), 100)
    browse_api = _browse_search_api_url(base_url, page, limit)
    if browse_api:
        html = await fetch_html(browse_api)
        return _parse_list_html(html)
    category_url = _category_browse_url(base_url, page, limit)
    if category_url:
        html = await fetch_html(category_url)
        return _parse_list_html(html)

    offset = (page - 1) * limit
    section = _results_section_key(base_url or "")

    params = {
        "sc": section,
        "am": str(limit),
        "of": str(offset),
        "sts": "1",
        "ibt": "0",
    }
    q = urlencode(params)
    url = f"{RESULTS_API}?{q}"
    html = await fetch_html(url)
    return _parse_list_html(html)
