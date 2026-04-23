from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

BASE = "https://xmoviesforyou.com"

# Single-segment paths that look like posts but are hub/nav pages (not video detail URLs).
_NON_VIDEO_PATH_SEGMENTS = frozenset(
    {
        "categories",
        "tags",
        "pornstars",
        "studios",
        "most-viewed",
        "top-rated",
        "random",
        "dmca",
        "contact",
        "privacy-policy",
        "terms",
    }
)


def get_categories() -> list[dict[str, Any]]:
    """
    Load curated sections and popular categories for XMoviesForYou
    from categories.json, similar to oppai.
    """
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "xmoviesforyou.com" or h == "www.xmoviesforyou.com"


def _default_http_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{BASE}/",
    }


async def fetch_html(url: str) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(25.0, connect=20.0),
        headers=_default_http_headers(),
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def _abs_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    return urljoin(BASE, u)


def _is_video_path(url: str) -> bool:
    p = urlparse(url)
    if not p.netloc or not can_handle(p.netloc):
        return False
    path = (p.path or "").strip("/")
    if not path:
        return False
    blocked_prefixes = ("tag/", "category/", "studio/", "pornstar/", "page/", "search")
    if any(path.startswith(x) for x in blocked_prefixes):
        return False
    if "/" not in path:
        first = path.split("/", 1)[0].lower()
        if first in _NON_VIDEO_PATH_SEGMENTS:
            return False
    return "/" not in path


def _clean_text(s: str | None) -> str | None:
    t = (s or "").strip()
    return t or None


def _clean_card_title(s: str | None) -> str | None:
    """Remove leaked UI/icon tokens from listing card link text."""
    t = _clean_text(s)
    if not t:
        return None
    t = re.sub(r"(?i)HD\s+play_circle\s+movie\s+", "HD ", t)
    t = re.sub(r"(?i)\bplay_circle\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def _uploader_from_list_title(title: str | None) -> str | None:
    """
    Listing cards often omit /studio/ links; titles use
    «… ) NetworkOrStudio • 18th April 2026» — take the segment after the last ')'.
    """
    t = _clean_text(title)
    if not t or "•" not in t:
        return None
    before = t.split("•", 1)[0].strip()
    if ")" not in before:
        return None
    name = before.rsplit(")", 1)[-1].strip()
    if not name or len(name) > 120:
        return None
    if "\n" in name:
        return None
    return name


def _list_uploader_name(container: Any | None, title: str | None) -> str | None:
    if container:
        studio_a = container.select_one('a[href*="/studio/"]')
        if studio_a:
            return _clean_text(studio_a.get_text(" ", strip=True))
    return _uploader_from_list_title(title)


def _extract_date(text: str | None) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^[A-Za-z_]+\s*", "", raw).strip()
    m = re.search(r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})", raw)
    if m:
        return m.group(1)
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", raw)
    if m:
        return m.group(1)
    return None


def _normalize_embed_url(url: str) -> str:
    p = urlparse(url)
    host = (p.netloc or "").lower().replace("www.", "")
    path = p.path or ""

    # Normalize known providers to their embed route.
    if host == "mixdrp.click":
        path = re.sub(r"^/f/([^/?#]+)", r"/e/\1", path)
    elif host == "playmogo.com":
        path = re.sub(r"^/d/([^/?#]+)", r"/e/\1", path)
    elif host == "streamtape.com":
        path = re.sub(r"^/v/([^/?#]+)", r"/e/\1", path)

    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, p.fragment))


def _extract_stream_links(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], str | None]:
    streams: list[dict[str, Any]] = []
    seen: set[str] = set()

    def provider_quality(label: str, host: str) -> str:
        t = (label or "").upper()
        h = (host or "").lower().replace("www.", "")
        if "STREAMTAPE" in t or "streamtape" in h:
            return "streamtape"
        if "MIXDROP" in t or "mixdrop" in t or "mixdrp" in h:
            return "mixdrop"
        if "DOOD" in t or "DOODSTREAM" in t or "dood" in h or "playmogo.com" in h:
            return "doodstream"
        # Avoid literal "embed" — it collides in flat quality maps and triggers wrong client branches.
        if h and "." in h:
            return h.split(".")[0]
        return "mirror"

    # Primary path: provider buttons block on detail page:
    # <div class="flex flex-wrap gap-4 mb-8"> ... <a href="https://...">STREAMTAPE</a> ...
    provider_containers = soup.select("div.flex.flex-wrap.gap-4.mb-8, div.mb-8")
    for container in provider_containers:
        for a in container.select("a[href]"):
            full = _abs_url(a.get("href"))
            if not full:
                continue
            full = _normalize_embed_url(full)
            if full in seen:
                continue

            p = urlparse(full)
            host = (p.netloc or "").lower().replace("www.", "")
            # Button label is usually provider name (STREAMTAPE/MIXDROP/DOODSTREAM)
            provider_label = a.get_text(" ", strip=True).upper()

            # Keep provider links only (external hosts or explicit stream/watch routes).
            if can_handle(host):
                same_site_path = (p.path or "").lower()
                if p.fragment:
                    continue
                if not any(x in same_site_path for x in ("/watch", "/stream", "/go/", "/out/")):
                    continue

            # Skip obvious non-provider links in generic mb-8 blocks.
            if can_handle(host) and not any(x in provider_label for x in ("STREAM", "DOWNLOAD")):
                continue

            seen.add(full)
            streams.append(
                {
                    "quality": provider_quality(provider_label, host),
                    "url": full,
                    "format": "embed",
                    "server": provider_label or host or "external",
                }
            )

    # Fallback path: broad scan for anchors that look like stream/download providers.
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        full = _abs_url(href)
        if not full:
            continue
        full = _normalize_embed_url(full)
        p = urlparse(full)
        host = (p.netloc or "").lower()
        text = a.get_text(" ", strip=True).lower()
        title = (a.get("title") or "").lower()

        likely_stream_link = any(x in text or x in title for x in ("watch stream", "download", "streamtape", "mixdrop", "doodstream"))
        if not likely_stream_link:
            continue

        # Keep only offsite providers, or same-site redirect pages that look like stream gateways.
        if can_handle(host):
            same_site_path = (p.path or "").lower()
            if p.fragment:
                continue
            if not any(x in same_site_path for x in ("/watch", "/stream", "/go/", "/out/")):
                continue
        if full in seen:
            continue
        seen.add(full)

        server = host.replace("www.", "") or "external"
        streams.append(
            {
                "quality": provider_quality(server, host),
                "url": full,
                "format": "embed",
                "server": server,
            }
        )

    default_url = streams[0]["url"] if streams else None
    if streams:
        streams[0]["format"] = "default"
    return streams, default_url


def _related_from_page(soup: BeautifulSoup, current_url: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = {current_url}
    for a in soup.select("a[href]"):
        href = _abs_url(a.get("href"))
        if not href or not _is_video_path(href) or href in seen:
            continue
        text = _clean_card_title(a.get_text(" ", strip=True))
        if not text:
            continue
        seen.add(href)
        out.append({"url": href, "title": text, "thumbnail_url": None})
    return out[:36]


def _build_list_page_url(base_url: str, page: int) -> str:
    page = max(1, page)
    if page == 1:
        return base_url

    parsed = urlparse(base_url)
    path = parsed.path or "/"
    query = parse_qs(parsed.query)

    if parsed.query:
        query["page"] = [str(page)]
        return urlunparse((parsed.scheme, parsed.netloc, path, "", urlencode(query, doseq=True), ""))

    new_path = re.sub(r"/page/\d+/?$", "/", path.rstrip("/")) or "/"
    if not new_path.endswith("/"):
        new_path += "/"
    new_path = f"{new_path}page/{page}/"
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", "", ""))


def _img_class_list(img: Any) -> list[str]:
    c = img.get("class") if hasattr(img, "get") else None
    if not c:
        return []
    if isinstance(c, str):
        return c.split()
    return list(c)


def _element_has_cover_image(el: Any) -> bool:
    if not hasattr(el, "select"):
        return False
    if el.select_one("img.object-cover") or el.select_one("img.mb-12"):
        return True
    for img in el.select("img"):
        cls = _img_class_list(img)
        if "object-cover" in cls or "mb-12" in cls:
            return True
    return False


def _extract_thumb(container: BeautifulSoup | None) -> str | None:
    if not container:
        return None
    # Prefer card cover images (Tailwind: object-cover); avoid header logos (object-contain, h-10).
    imgs: list[Any] = []
    for img in container.select("img"):
        cls = _img_class_list(img)
        if "object-cover" in cls or "mb-12" in cls:
            imgs.append(img)
    if not imgs:
        imgs = list(container.select("img"))

    for img in imgs:
        cls = _img_class_list(img)
        if "object-contain" in cls and "object-cover" not in cls:
            continue
        if "h-10" in cls and "w-auto" in cls:
            continue

        src = (
            img.get("data-src")
            or img.get("data-lazy-src")
            or img.get("data-original")
            or img.get("data-srcset")
            or img.get("srcset")
            or img.get("src")
        )
        if not src:
            continue

        # srcset may contain multiple URLs: take the first URL token.
        if " " in src and (".webp" in src or ".jpg" in src or ".png" in src):
            src = src.split(",", 1)[0].strip().split(" ", 1)[0].strip()

        url = _abs_url(src)
        if not url:
            continue
        if url.rstrip("/").endswith("/logo.png"):
            continue
        return url

    return None


def _find_card_container(anchor: Any) -> Any | None:
    """
    On xmoviesforyou the title <a> is often nested in a small text-only div,
    while the thumbnail <img class="object-cover"> lives in a higher parent.
    The card link may also wrap both image and title — check the anchor itself,
    then walk up to find the wrapper that contains the real cover image.
    """
    node: Any = anchor
    for _ in range(18):
        if not node:
            break
        if _element_has_cover_image(node):
            return node
        parent = getattr(node, "parent", None)
        if not parent:
            break
        node = parent
    return getattr(anchor, "parent", None)


def _parse_list_html(html: str, limit: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Prefer heading-based title links; listing pages may use plain text links instead (see fallback).
    for a in soup.select(
        "h1 a[href], h2 a[href], h3 a[href], h4 a[href], "
        "article h1 a[href], article h2 a[href], article h3 a[href], article h4 a[href]"
    ):
        href = _abs_url(a.get("href"))
        if not href or not _is_video_path(href) or href in seen:
            continue
        seen.add(href)

        title = _clean_card_title(a.get_text(" ", strip=True)) or href.rstrip("/").split("/")[-1].replace("-", " ")
        container = _find_card_container(a)
        thumb = _extract_thumb(container)
        uploader = _list_uploader_name(container, title)

        upload_date = _extract_date(title)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": None,
                "views": None,
                "upload_date": upload_date,
                "uploader_name": uploader,
            }
        )
        if len(items) >= limit:
            return items

    # Fallback for pages where title blocks are not in heading tags.
    for a in soup.select("a[href]"):
        if len(items) >= limit:
            break
        href = _abs_url(a.get("href"))
        if not href or not _is_video_path(href) or href in seen:
            continue
        text = _clean_card_title(a.get_text(" ", strip=True))
        if not text or text.lower().startswith("download"):
            continue
        seen.add(href)
        container = _find_card_container(a)
        thumb = _extract_thumb(container)
        uploader = _list_uploader_name(container, text)
        items.append(
            {
                "url": href,
                "title": text,
                "thumbnail_url": thumb,
                "duration": None,
                "views": None,
                "upload_date": _extract_date(text),
                "uploader_name": uploader,
            }
        )
    return items[:limit]


async def scrape(url: str) -> dict[str, Any]:
    full_url = _abs_url(url) or url
    html = await fetch_html(full_url)
    soup = BeautifulSoup(html, "lxml")

    title = None
    h1 = soup.find("h1")
    if h1:
        title = _clean_text(h1.get_text(" ", strip=True))
    if not title:
        og_title = soup.find("meta", attrs={"property": "og:title"})
        title = _clean_text(og_title.get("content")) if og_title else None

    description = None
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc:
        description = _clean_text(og_desc.get("content"))
    if not description:
        first_p = soup.find("p")
        description = _clean_text(first_p.get_text(" ", strip=True)) if first_p else None

    thumbnail = None
    og_img = soup.find("meta", attrs={"property": "og:image"})
    if og_img:
        thumbnail = _abs_url(og_img.get("content"))
    if not thumbnail:
        thumbnail = _extract_thumb(soup)

    tags: list[str] = []
    for a in soup.select('a[href*="/tag/"]'):
        t = _clean_text(a.get_text(" ", strip=True))
        if t:
            tags.append(t.lstrip("#"))

    categories: list[str] = []
    for a in soup.select('a[href*="/category/"]'):
        c = _clean_text(a.get_text(" ", strip=True))
        if c:
            categories.append(c.replace("category", "").strip())

    uploader = None
    studio_a = soup.select_one('a[href*="/studio/"]')
    if studio_a:
        uploader = _clean_text(studio_a.get_text(" ", strip=True))

    page_text = soup.get_text("\n", strip=True)
    upload_date = _extract_date(page_text)

    streams, default_url = _extract_stream_links(soup)
    video_data = {
        "streams": streams,
        "default": default_url,
        "hls": None,
        "has_video": bool(default_url),
    }

    return {
        "url": full_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "upload_date": upload_date,
        "uploader_name": uploader,
        "category": categories[0] if categories else None,
        "tags": tags,
        "video": video_data,
        "related_videos": _related_from_page(soup, full_url),
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page = max(1, page)
    limit = min(max(1, limit), 100)
    url = _build_list_page_url(base_url, page)
    html = await fetch_html(url)
    return _parse_list_html(html, limit)
