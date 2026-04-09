from __future__ import annotations

import json
import os
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "hornysimp.com" or h.endswith(".hornysimp.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer or "https://hornysimp.com/",
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
    for key in ("data-src", "data-original", "data-lazy", "src", "srcset"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        # srcset: take the first URL
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
    for suffix in (" - HornySimp", " | HornySimp"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


_EXCLUDED_SECTION_PREFIXES = (
    "https://hornysimp.com/leaked-clips/",
    "https://hornysimp.com/hd-porns/",
    "https://hornysimp.com/jav/",
    "https://hornysimp.com/models/",
)


def _normalize_hornysimp_post_href(href: str) -> Optional[str]:
    """Return canonical post URL or None if not a single-post link."""
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    if not href.startswith("https://hornysimp.com/"):
        return None
    if any(href.startswith(p) and href.rstrip("/") == p.rstrip("/") for p in _EXCLUDED_SECTION_PREFIXES):
        return None
    if any(x in href for x in ("/wp-content/", "/wp-json/", "/tag/", "/category/", "/page/", "/feed/")):
        return None
    if "?" in href:
        return None
    if href.rstrip("/").count("/") < 3:
        return None
    return href.split("#", 1)[0]


def _list_from_pt_cv_title(
    soup: BeautifulSoup, limit: int, seen: set[str]
) -> list[dict[str, Any]]:
    """
    Portfolio / Content Views grids use <h4 class="pt-cv-title"><a>Title text</a></h4>.
    Prefer this path so titles match visible headings (not image alt / other links).
    """
    items: list[dict[str, Any]] = []
    for a in soup.select("h4.pt-cv-title a[href], .pt-cv-title a[href]"):
        if len(items) >= limit:
            break
        canon = _normalize_hornysimp_post_href(a.get("href") or "")
        if not canon or canon in seen:
            continue

        title = _clean_title(a.get_text(" ", strip=True)) or "Unknown Video"

        thumb: Optional[str] = None
        field = a.find_parent("div", class_="pt-cv-ifield")
        if field:
            thumb = _best_image_url(field.select_one("img"))

        seen.add(canon)
        items.append(
            {
                "url": canon,
                "title": title,
                "thumbnail_url": thumb,
                "duration": None,
                "views": None,
                "uploader_name": None,
            }
        )

    return items


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    return any(x in s for x in ("trudigo.com/banner", "googlesyndication", "doubleclick", "adservice"))


def _embed_quality_label(idx: int) -> str:
    """
    Match HornySimp UI: `.hscp-tab-button` labels "Server 1" / "Server 2".
    Same shape as xxxparodyhd: `quality` is a human label, `format` is `embed`.
    """
    return f"Server {idx + 1}"


def _default_embed_url(embed_urls: list[str]) -> str | None:
    """Prefer Byse (`byseraguci.com`) — HornySimp \"Server 2\" — as default when present."""
    if not embed_urls:
        return None
    for u in embed_urls:
        if "byseraguci.com" in u.lower():
            return u
    for u in embed_urls:
        low = u.lower()
        if "hrnyvid" in low or "lulu" in low:
            return u
    return embed_urls[0]


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

    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    # Collect embed iframes (players)
    embed_urls: list[str] = []
    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        if not src.startswith("http"):
            continue
        if _is_probable_ad_iframe(src):
            continue
        embed_urls.append(src)
    embed_urls = list(dict.fromkeys(embed_urls))

    # Same pattern as xxxparodyhd: multiple `format: embed` streams with human labels (Server 1 / Server 2).
    streams: list[dict[str, str]] = []
    for idx, e in enumerate(embed_urls):
        streams.append({"url": e, "quality": _embed_quality_label(idx), "format": "embed"})

    default_url = _default_embed_url(embed_urls)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": [],
        "video": {
            "streams": streams,
            "hls": None,
            "default": default_url,
            "has_video": bool(default_url),
        },
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer="https://hornysimp.com/")
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    """
    HornySimp uses Elementor pagination with a `_page` query param.
    Works for home and for section pages like `/leaked-clips/`.
    """
    url = base_url.strip()
    if page <= 1:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_page={page}"


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer="https://hornysimp.com/")
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()

    # 1) PT Content Views: <h4 class="pt-cv-title"><a>…</a></h4> — correct visible titles
    items = _list_from_pt_cv_title(soup, limit, seen)

    # 2) Fallback: image/card links (older layouts)
    for a in soup.select("a[href]"):
        if len(items) >= limit:
            break
        canon = _normalize_hornysimp_post_href(a.get("href") or "")
        if not canon or canon in seen:
            continue

        img = a.find("img") or (a.find_parent(["article", "div", "li"]) or a).find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = _clean_title(title) or "Unknown Video"

        seen.add(canon)
        items.append(
            {
                "url": canon,
                "title": title,
                "thumbnail_url": thumb,
                "duration": None,
                "views": None,
                "uploader_name": None,
            }
        )

    return items[:limit]

