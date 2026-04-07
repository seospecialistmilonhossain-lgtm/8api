from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

BASE = "https://xmoviesforyou.com"


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
    return "/" not in path


def _clean_text(s: str | None) -> str | None:
    t = (s or "").strip()
    return t or None


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


def _looks_like_streamtape(url: str) -> bool:
    p = urlparse(url)
    host = (p.netloc or "").lower().replace("www.", "")
    return host == "streamtape.com" and (p.path.startswith("/e/") or p.path.startswith("/v/"))


def _infer_stream_format(url: str) -> str:
    u = (url or "").lower()
    if ".m3u8" in u:
        return "hls"
    if ".mp4" in u or "stream=1" in u or "/get_video?" in u or "tapecontent.net/" in u:
        return "mp4"
    return "embed"


async def _resolve_streamtape_direct(streamtape_url: str) -> str | None:
    """
    Resolve streamtape embed URL to a direct URL.
    Returns None when resolution fails so callers can safely fall back to embed URL.
    """
    if not _looks_like_streamtape(streamtape_url):
        return None

    headers = _default_http_headers() | {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://streamtape.com/",
    }

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(20.0, connect=15.0),
            headers=headers,
        ) as client:
            page = await client.get(streamtape_url)
            page.raise_for_status()
            html = page.text or ""

            candidates: list[str] = []

            # Absolute get_video links
            for m in re.finditer(
                r'["\']((?:https?:)?//(?:www\.)?streamtape\.com/get_video\?[^"\']+)["\']',
                html,
                flags=re.I,
            ):
                candidates.append(m.group(1))

            # Relative get_video links
            for m in re.finditer(r'["\'](/get_video\?[^"\']+)["\']', html, flags=re.I):
                candidates.append(urljoin("https://streamtape.com", m.group(1)))

            # Common variant found in hidden divs: /streamtape.com/get_video?...
            for m in re.finditer(r"(/streamtape\.com/get_video\?[^<\"'\s]+)", html, flags=re.I):
                raw = m.group(1).strip()
                candidates.append(f"https:/{raw}" if raw.startswith("/") else raw)

            # Some pages expose escaped variants.
            for m in re.finditer(r"(/get_video\\\?[^\"'\\]+)", html, flags=re.I):
                unescaped = m.group(1).replace("\\/", "/").replace("\\?", "?")
                candidates.append(urljoin("https://streamtape.com", unescaped))

            # Tokens in hidden elements are often obfuscated (e.g., *cde, *xyza).
            # The valid token usually appears in inline script literals.
            script_tokens = [
                t
                for t in re.findall(r"token=([A-Za-z0-9_-]{8,})", html, flags=re.I)
                if not (t.endswith("cde") or t.endswith("xyza"))
            ]

            # Build token-swapped candidates from discovered /get_video URLs.
            if script_tokens and candidates:
                swapped: list[str] = []
                unique_tokens: list[str] = []
                token_seen: set[str] = set()
                for t in script_tokens:
                    if t in token_seen:
                        continue
                    token_seen.add(t)
                    unique_tokens.append(t)
                for base in candidates:
                    for t in unique_tokens:
                        swapped.append(re.sub(r"token=[^&\"'\s]+", f"token={t}", base, count=1))
                candidates.extend(swapped)

            # Deduplicate while preserving first-seen order.
            seen: set[str] = set()
            uniq_candidates: list[str] = []
            for c in candidates:
                c2 = c.strip()
                if not c2:
                    continue
                if c2.startswith("//"):
                    c2 = f"https:{c2}"
                if c2 in seen:
                    continue
                seen.add(c2)
                uniq_candidates.append(c2)

            for candidate in uniq_candidates:
                try:
                    # Prefer asking for the direct stream variant, which should 302 to tapecontent.
                    stream_candidate = candidate
                    if "stream=1" not in stream_candidate:
                        joiner = "&" if "?" in stream_candidate else "?"
                        stream_candidate = f"{stream_candidate}{joiner}stream=1"

                    probe = await client.get(
                        stream_candidate,
                        headers={
                            "User-Agent": headers["User-Agent"],
                            "Accept": "*/*",
                            "Referer": streamtape_url,
                        },
                        follow_redirects=False,
                    )
                    location = (probe.headers.get("Location") or "").strip()
                    if location:
                        abs_location = urljoin("https://streamtape.com", location)
                        if "tapecontent.net" in abs_location:
                            return abs_location

                    body = probe.text or ""
                    if '"status":500' in body:
                        # Invalid token candidate.
                        continue

                    # Fallback: follow to final signed CDN URL where possible.
                    resolved = await client.get(
                        stream_candidate,
                        headers={
                            "User-Agent": headers["User-Agent"],
                            "Accept": "*/*",
                            "Referer": streamtape_url,
                        },
                    )
                    final_url = str(resolved.url)
                    if final_url and (
                        "tapecontent.net" in final_url
                        or "streamtape.com/get_video" in final_url
                        or "stream=1" in final_url
                    ):
                        return final_url
                except Exception:
                    continue
    except Exception:
        return None
    return None


async def _extract_stream_links(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], str | None]:
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
        return "embed"

    # Primary path: provider buttons block on detail page:
    # <div class="flex flex-wrap gap-4 mb-8"> ... <a href="https://...">STREAMTAPE</a> ...
    provider_containers = soup.select("div.flex.flex-wrap.gap-4.mb-8, div.mb-8")
    for container in provider_containers:
        for a in container.select("a[href]"):
            full = _abs_url(a.get("href"))
            if not full or full in seen:
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

            # Replace Streamtape embed URL with direct URL when possible.
            if provider_quality(provider_label, host) == "streamtape" and _looks_like_streamtape(full):
                seen.add(full)
                direct_url = await _resolve_streamtape_direct(full)
                if direct_url and direct_url not in seen:
                    seen.add(direct_url)
                    streams.append(
                        {
                            "quality": "streamtape",
                            "url": direct_url,
                            "format": _infer_stream_format(direct_url),
                            "server": "streamtape-direct",
                        }
                    )
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

        if "streamtape.com" in host and _looks_like_streamtape(full):
            seen.add(full)
            direct_url = await _resolve_streamtape_direct(full)
            if direct_url and direct_url not in seen:
                seen.add(direct_url)
                streams.append(
                    {
                        "quality": "streamtape",
                        "url": direct_url,
                        "format": _infer_stream_format(direct_url),
                        "server": "streamtape-direct",
                    }
                )
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
    return streams, default_url


def _related_from_page(soup: BeautifulSoup, current_url: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = {current_url}
    for a in soup.select("a[href]"):
        href = _abs_url(a.get("href"))
        if not href or not _is_video_path(href) or href in seen:
            continue
        text = _clean_text(a.get_text(" ", strip=True))
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


def _extract_thumb(container: BeautifulSoup | None) -> str | None:
    if not container:
        return None
    # Site uses modern card images; prioritize likely "cover" images over logos/ads.
    img = container.select_one("img.object-cover") or container.select_one("img.mb-12") or container.select_one("img")
    if not img:
        return None
    src = (
        img.get("data-src")
        or img.get("data-lazy-src")
        or img.get("data-original")
        or img.get("data-srcset")
        or img.get("srcset")
        or img.get("src")
    )
    if not src:
        return None

    # srcset may contain multiple URLs: take the first URL token.
    if " " in src and (".webp" in src or ".jpg" in src or ".png" in src):
        src = src.split(",", 1)[0].strip().split(" ", 1)[0].strip()

    url = _abs_url(src)
    if not url:
        return None

    # Avoid returning site logo as thumbnail.
    if url.rstrip("/").endswith("/logo.png"):
        return None

    return url


def _find_card_container(anchor: Any) -> Any | None:
    """
    On xmoviesforyou the title <a> is often nested in a small text-only div,
    while the thumbnail <img class="object-cover"> lives in a higher parent.
    Walk up a few levels to find the wrapper that contains the real cover image.
    """
    node = anchor
    for _ in range(18):
        if not node:
            break
        parent = getattr(node, "parent", None)
        if not parent:
            break
        if hasattr(parent, "select_one") and (parent.select_one("img.object-cover") or parent.select_one("img.mb-12")):
            return parent
        node = parent
    return getattr(anchor, "parent", None)


def _parse_list_html(html: str, limit: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("h1 a[href], h2 a[href], h3 a[href], h4 a[href]"):
        href = _abs_url(a.get("href"))
        if not href or not _is_video_path(href) or href in seen:
            continue
        seen.add(href)

        title = _clean_text(a.get_text(" ", strip=True)) or href.rstrip("/").split("/")[-1].replace("-", " ")
        container = _find_card_container(a)
        thumb = _extract_thumb(container)
        uploader = None
        if container:
            studio_a = container.select_one('a[href*="/studio/"]')
            if studio_a:
                uploader = _clean_text(studio_a.get_text(" ", strip=True))

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
        text = _clean_text(a.get_text(" ", strip=True))
        if not text or text.lower().startswith("download"):
            continue
        seen.add(href)
        items.append(
            {
                "url": href,
                "title": text,
                "thumbnail_url": None,
                "duration": None,
                "views": None,
                "upload_date": _extract_date(text),
                "uploader_name": None,
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

    streams, default_url = await _extract_stream_links(soup)
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
