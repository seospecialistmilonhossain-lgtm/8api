from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "bollywoodmaal.com" or h.endswith(".bollywoodmaal.com")


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
        "Referer": "https://bollywoodmaal.com/",
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
    for key in ("data-src", "data-lazy-src", "data-original", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    for suffix in (" – Bollywood Actress Porn Videos", " - Bollywood Actress Porn Videos", " | Bollywood Actress Porn Videos"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _clean_views_text(v: str | None) -> Optional[str]:
    if not v:
        return None
    txt = str(v).strip().replace(",", "").replace("\u00a0", "")
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt or None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://bollywoodmaal.com{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if "bollywoodmaal.com" not in parsed.netloc.lower():
        return None

    path = parsed.path.lower().rstrip("/")
    blocked_exact = {
        "",
        "/",
        "/contact-us",
        "/privacy-policy",
        "/dmca",
        "/18-u-s-c-2257",
        "/login",
    }
    if path in blocked_exact:
        return None
    if any(p in path for p in ("/tag/", "/category/", "/feed/", "/author/", "/wp-content/", "/wp-json/")):
        return None
    if any(k in parsed.query.lower() for k in ("replytocom=", "amp")):
        return None

    # Post pages are generally first-level slugs on this site.
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) != 1:
        return None
    if segments[0] in {"home", "tollywood", "tv-celebrity", "sports-celebrities", "indian-xxx-videos", "bollywood-xxx"}:
        return None

    return urlunparse(("https", "bollywoodmaal.com", f"/{segments[0]}/", "", "", ""))


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []

    for pat in (
        r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*",
    ):
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            u = m.group(0).strip()
            if u:
                urls.append(u)

    return list(dict.fromkeys(urls))


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if ".m3u8" in low:
        return "adaptive"
    return "source"


def _extract_streams(soup: BeautifulSoup, html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    # Native <video> sources
    for source in soup.select("video source[src], video[src]"):
        src = (source.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        if src.startswith("/"):
            src = urljoin("https://bollywoodmaal.com/", src)
        if not src.startswith("http") or src in seen:
            continue
        seen.add(src)
        fmt = "hls" if ".m3u8" in src.lower() else "mp4"
        streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    # Embedded iframes
    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        if src.startswith("/"):
            src = urljoin("https://bollywoodmaal.com/", src)
        if not src.startswith("http") or src in seen:
            continue
        seen.add(src)
        streams.append({"url": src, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    # Inline script links
    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        seen.add(src)
        fmt = "hls" if ".m3u8" in src.lower() else "mp4"
        streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    def _score(s: dict[str, str]) -> int:
        fmt = s.get("format", "")
        if fmt == "embed":
            return -1
        q = s.get("quality", "")
        if q == "source":
            return 2000
        digits = "".join(ch for ch in q if ch.isdigit())
        return int(digits) if digits else (1000 if fmt == "hls" else 0)

    streams.sort(key=_score, reverse=True)
    direct_mp4 = next((s for s in streams if s.get("format") == "mp4"), None)
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    # Prefer embed as default when available for this source.
    default_url = embed["url"] if embed else (direct_mp4["url"] if direct_mp4 else (hls["url"] if hls else None))
    hls_url = hls["url"] if hls else None

    return {
        "streams": streams,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(default_url),
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="twitter:description"),
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = None
    views = None
    text_blob = soup.get_text(" ", strip=True)
    dm = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text_blob)
    if dm:
        duration = dm.group(0)
    vm = re.search(r"(\d[\d,\.]*\s*[KMB]?)\s*(?:views|view)\b", text_blob, re.IGNORECASE)
    if vm:
        views = _clean_views_text(vm.group(1))

    video = _extract_streams(soup, html)
    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "category": None,
        "tags": [],
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or "bollywoodmaal.com"
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    # Prefer common WP paged query param; parser is resilient to pager layout changes.
    query_items["paged"] = str(page)
    return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        container = a.find_parent(["article", "li", "div"]) or a
        img = a.find("img") or (container.find("img") if container else None)
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = _clean_title(title) or "Unknown Video"

        ctext = container.get_text(" ", strip=True) if container else ""
        duration = None
        views = None
        dm = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", ctext)
        if dm:
            duration = dm.group(0)
        vm = re.search(r"(\d[\d,\.]*\s*[KMB]?)\s*(?:views|view)\b", ctext, re.IGNORECASE)
        if vm:
            views = _clean_views_text(vm.group(1))

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": None,
            }
        )

    return items[:limit]
