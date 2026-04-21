from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import pool as http_pool, fetch_html as pool_fetch_html


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "pimpbunny.com" or h.endswith(".pimpbunny.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str) -> str:
    cookie = (os.getenv("PIMPBUNNY_COOKIE") or os.getenv("PIMPBUNNY_COOKIES") or "").strip()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://pimpbunny.com/",
    }
    if cookie:
        headers["Cookie"] = cookie

    def _looks_like_cloudflare_challenge(html: str) -> bool:
        h = (html or "").lower()
        if not h:
            return False
        needles = (
            "cloudflare",
            "/cdn-cgi/",
            "cf-browser-verification",
            "checking your browser",
            "just a moment",
            "verify you are human",
            "attention required",
            "turnstile",
        )
        return any(n in h for n in needles)

    def _cf_cache_path() -> str:
        # Keep outside repo to avoid committing clearance cookies.
        return os.path.join(tempfile.gettempdir(), "apphub3-pimpbunny-cf.json")

    def _load_cached_cookie_header() -> Optional[str]:
        try:
            with open(_cf_cache_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            expires_at = float(data.get("expires_at") or 0)
            if expires_at and time.time() > expires_at:
                return None
            ch = data.get("cookie_header")
            return str(ch).strip() if ch else None
        except Exception:
            return None

    def _save_cached_cookie_header(cookie_header: str, *, ttl_seconds: int = 6 * 60 * 60) -> None:
        try:
            payload = {
                "expires_at": time.time() + int(ttl_seconds),
                "cookie_header": cookie_header,
            }
            with open(_cf_cache_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:
            pass

    async def _fetch_with_curl_cffi() -> str:
        # `curl_cffi` is synchronous; keep it off the event loop.
        def _do() -> str:
            from curl_cffi import requests as creq  # imported lazily to keep scraper import-light

            resp = creq.get(
                url,
                headers=headers,
                impersonate="chrome120",
                timeout=30,
                allow_redirects=True,
            )
            resp.raise_for_status()
            return resp.text

        return await asyncio.to_thread(_do)

    async def _fetch_with_nodriver() -> str:
        """
        Cloudflare bypass (2026): Nodriver (CDP-based, stealthy).
        We use it as the final fallback to return the post-challenge HTML.
        """
        try:
            import nodriver as uc  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Nodriver is required to bypass Cloudflare for pimpbunny. Install it with `pip install nodriver`."
            ) from e

        async def _run() -> str:
            # Headless often fails on Cloudflare. Allow override via env var.
            headless_env = (os.getenv("PIMPBUNNY_HEADLESS") or "").strip().lower()
            headless = headless_env in ("1", "true", "yes", "y", "on")
            browser = await uc.start(headless=headless)
            try:
                # Warm-up: homepage then target URL (behavioral trust).
                try:
                    await browser.get("https://pimpbunny.com/")
                    await asyncio.sleep(4)
                except Exception:
                    pass

                page = await browser.get(url)
                # Let Cloudflare JS/Turnstile settle (best-effort).
                for _ in range(20):  # up to ~20s
                    await asyncio.sleep(1)
                    try:
                        content = await page.get_content()
                        if content and not _looks_like_cloudflare_challenge(content):
                            return content
                    except Exception:
                        pass

                # Nodriver API uses get_content() per the referenced article.
                content = await page.get_content()
                return content or ""
            finally:
                try:
                    stopped = browser.stop()
                    if asyncio.iscoroutine(stopped):
                        await stopped
                except Exception:
                    pass

        # Hard timeout so we don't hang worker threads forever.
        return await asyncio.wait_for(_run(), timeout=120.0)

    html: Optional[str] = None
    last_err: Optional[BaseException] = None

    # If we have a user-provided cookie or a cached clearance cookie, try curl_cffi first (fast + browser-like TLS).
    cached_cookie = _load_cached_cookie_header()
    effective_cookie = cookie or cached_cookie
    if effective_cookie:
        headers["Cookie"] = effective_cookie
        try:
            html0 = await _fetch_with_curl_cffi()
            if html0 and not _looks_like_cloudflare_challenge(html0):
                if not cookie and effective_cookie != cached_cookie:
                    _save_cached_cookie_header(effective_cookie)
                return html0
        except BaseException as e:
            last_err = e
    try:
        html = await pool_fetch_html(url, headers=headers)
    except BaseException as e:
        last_err = e

    if html and not _looks_like_cloudflare_challenge(html):
        return html

    # Fallback for Cloudflare / bot challenges.
    try:
        html2 = await _fetch_with_curl_cffi()
        if html2 and not _looks_like_cloudflare_challenge(html2):
            return html2
        # If we still look blocked, return whatever we have (helps debugging / non-CF failures).
        return html2 or (html or "")
    except BaseException as e:
        last_err = last_err or e

    # Last resort: stealth browser HTML (Nodriver).
    try:
        html3 = await _fetch_with_nodriver()
        if html3 and not _looks_like_cloudflare_challenge(html3):
            return html3
        raise RuntimeError(
            "PimpBunny is protected by Cloudflare and the scraper could not pass the verification challenge. "
            "Export `PIMPBUNNY_COOKIE` with your browser cookies (must include `cf_clearance` when present) and retry."
        )
    except RuntimeError:
        raise
    except BaseException:
        if html:
            return html
        raise last_err


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


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    for suffix in (" | PimpBunny", " - PimpBunny"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


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
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _clean_views_text(v: str | None) -> Optional[str]:
    if not v:
        return None
    txt = str(v).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"[^0-9KMBkmb\. ]", "", txt).strip().replace(" ", "")
    return txt or None


_VIDEO_PAGE_RE = re.compile(
    r"^https://(?:www\.)?pimpbunny\.com/videos/(?P<slug>[a-z0-9-]+)/?$", re.IGNORECASE
)


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://pimpbunny.com{href}"
    if not href.startswith("http"):
        return None
    href = href.split("#", 1)[0]
    m = _VIDEO_PAGE_RE.match(href.split("?", 1)[0])
    if not m:
        return None
    slug = m.group("slug").lower()
    if slug in ("upload-video", "videos"):
        return None
    return f"https://pimpbunny.com/videos/{slug}/"


def _extract_streams(html: str) -> dict[str, Any]:
    """
    Pages often list the same logical quality multiple times (stale + current signing hashes).
    The player block appears later in the document; **last match per quality wins**.
    """
    html = html.replace("\\/", "/").replace("\\u0026", "&")
    streams: list[dict[str, str]] = []
    seen_url: set[str] = set()
    mp4_by_quality: dict[str, str] = {}

    pat = re.compile(
        r"https?://(?:www\.)?pimpbunny\.com/get_file/[^\"'\s<>]+?\.mp4/?",
        re.IGNORECASE,
    )

    for m in pat.finditer(html):
        raw = m.group(0)
        url = raw.strip().rstrip("/")
        if url in seen_url:
            continue
        lower = url.lower()
        if "preview" in lower or "_preview" in lower:
            continue
        seen_url.add(url)
        quality = "default"
        qm = re.search(r"_(\d{3,4})p\.mp4", lower)
        if qm:
            quality = f"{qm.group(1)}p"
        elif re.search(r"/\d+\.mp4$", lower):
            quality = "source"
        mp4_by_quality[quality] = url

    for q, url in mp4_by_quality.items():
        streams.append({"quality": q, "url": url, "format": "mp4"})

    embed_seen = False
    for m in re.finditer(r"https://(?:www\.)?pimpbunny\.com/embed/\d+", html):
        emb = m.group(0)
        if not embed_seen:
            embed_seen = True
            # quality key matches flat API field `pimpbunny` + `pimpbunny_format` (see video_streaming)
            streams.append({"quality": "pimpbunny", "url": emb, "format": "embed"})
        break

    def _score(s: dict[str, str]) -> int:
        if s.get("format") == "embed":
            return -1
        q = s.get("quality", "")
        if q == "source":
            return 2000
        digits = "".join(ch for ch in q if ch.isdigit())
        return int(digits) if digits else 0

    streams.sort(key=_score, reverse=True)
    default_url = None
    mp4s = [s for s in streams if s.get("format") == "mp4"]
    if mp4s:
        default_url = mp4s[0].get("url")
    elif streams:
        default_url = streams[0].get("url")
    return {"streams": streams, "default": default_url, "has_video": bool(streams)}


async def _get_file_to_remote_playable(get_file_url: str, *, referer: str) -> Optional[str]:
    """
    Same-origin get_file URLs 302 to st*.pimpbunny.com/remote_control.php.
    The in-browser player uses GET with Range, the video page as Referer, and ?rnd=<ms> on the URL
    (see devtools); matching that recovers redirects for all tiers including *_pb_*360p/480p/1080p names.
    Tiers that still do not redirect return None.
    """
    session = await http_pool.get_session()
    base = get_file_url.split("?", 1)[0].strip().rstrip("/")
    ref = referer.strip() if referer.strip().startswith("http") else f"https://pimpbunny.com/{referer.strip().lstrip('/')}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": ref,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    _TIMEOUT_SEC = 15.0

    def _loc_from(resp: Any) -> Optional[str]:
        st = getattr(resp, "status", 0)
        if st in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location") if resp.headers else None
            if loc and "remote_control.php" in loc:
                return loc
        return None

    async def _attempt(url: str, method: str, range_hdr: Optional[str]) -> Optional[str]:
        h = dict(headers)
        if range_hdr:
            h["Range"] = range_hdr
        if method.upper() == "HEAD":
            async with session.head(url, headers=h, allow_redirects=False) as resp:
                return _loc_from(resp)
        async with session.get(url, headers=h, allow_redirects=False) as resp:
            return _loc_from(resp)

    # Browser uses ...mp4/?rnd=timestamp ; also try ...mp4?rnd= without slash before ?
    url_forms = (
        lambda rnd: f"{base}/?rnd={rnd}",
        lambda rnd: f"{base}?rnd={rnd}",
    )
    attempts: list[tuple[str, str, Optional[str]]] = []
    for mk in url_forms:
        rnd = int(time.time() * 1000)
        u = mk(rnd)
        attempts.extend(
            [
                (u, "HEAD", None),
                (u, "GET", "bytes=0-"),
                (u, "GET", "bytes=0-0"),
            ]
        )

    for url, method, rng in attempts:
        try:
            loc = await asyncio.wait_for(_attempt(url, method, rng), timeout=_TIMEOUT_SEC)
            if loc:
                return loc
        except (asyncio.TimeoutError, Exception):
            continue
    return None


async def _resolve_video_streams_to_remote_playable(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")]
    if not mp4:
        return

    async def resolve_one(s: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        u = await _get_file_to_remote_playable(s["url"], referer=referer)
        return (s, u)

    resolved = await asyncio.gather(*[resolve_one(s) for s in mp4])
    for s, remote in resolved:
        if remote:
            s["url"] = remote
        else:
            streams.remove(s)

    remote_mp4 = [s for s in streams if s.get("format") == "mp4" and "remote_control.php" in (s.get("url") or "")]
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    if remote_mp4:
        video["default"] = remote_mp4[0]["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None

    video["has_video"] = bool(remote_mp4) or bool(embed)


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    )
    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))

    duration = None
    views = None
    uploader = None
    tags: list[str] = []
    category = None

    kw = _meta(soup, name="keywords")
    if kw:
        tags.extend([x.strip() for x in kw.split(",") if x.strip()])

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
            title = _clean_title(_first_non_empty(title, obj.get("name")))
            description = _first_non_empty(description, obj.get("description"))
            thumb = obj.get("thumbnailUrl")
            if isinstance(thumb, list) and thumb:
                thumb = thumb[0]
            thumbnail = _first_non_empty(thumbnail, thumb if isinstance(thumb, str) else None)
            author = obj.get("author")
            if isinstance(author, dict):
                uploader = _first_non_empty(uploader, author.get("name"))
            elif isinstance(author, str):
                uploader = _first_non_empty(uploader, author)
            genre = obj.get("genre")
            if isinstance(genre, str):
                category = _first_non_empty(category, genre)

    if not duration:
        text = soup.get_text(" ", strip=True)
        m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
        if m:
            duration = m.group(0)

    if not views:
        vm = re.search(r"(\d[\d,\.]*\s*[KMB]?)\s*(?:views|view)\b", soup.get_text(" ", strip=True), re.IGNORECASE)
        if vm:
            views = _clean_views_text(vm.group(1))

    tags = list(dict.fromkeys(tags))
    video = _extract_streams(html)

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
    data = parse_video_page(html, url)
    v = data.get("video")
    if isinstance(v, dict) and v.get("streams"):
        page_ref = str(url).strip()
        if page_ref and not page_ref.endswith("/"):
            page_ref = page_ref + "/"
        await _resolve_video_streams_to_remote_playable(v, referer=page_ref)
    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or "pimpbunny.com"
    parts = [x for x in (p.path or "").split("/") if x]
    q = dict(parse_qsl(p.query, keep_blank_values=True))

    def out(path: str, query: dict[str, str]) -> str:
        qs = urlencode(query) if query else ""
        return urlunparse((scheme, netloc, path, "", qs, ""))

    if parts and parts[0] == "search":
        term = "/".join(parts[1:]) if len(parts) > 1 else ""
        path = f"/search/{term}/" if term else "/search/"
        if page > 1:
            q["page"] = str(page)
        return out(path, q)

    if not parts:
        path = "/videos/" if page <= 1 else f"/videos/{page}/"
        return out(path, {})

    if parts[0] == "videos":
        path = "/videos/" if page <= 1 else f"/videos/{page}/"
        return out(path, {})

    if parts[0] == "categories" and len(parts) >= 2:
        slug = parts[1]
        path = f"/categories/{slug}/" if page <= 1 else f"/categories/{slug}/{page}/"
        return out(path, {})

    base_path = (p.path or "/") if (p.path or "/").endswith("/") else (p.path or "/") + "/"
    if page > 1:
        q["page"] = str(page)
    return out(base_path, q)


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

        img = a.find("img")
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = (title or "").strip() or "Unknown Video"

        container = a.find_parent(["article", "li", "div"]) or a
        ctext = container.get_text(" ", strip=True) if container else ""

        duration = None
        dm = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", ctext)
        if dm:
            duration = dm.group(0)

        views = None
        vm = re.search(r"(\d[\d,\.]*\s*[KMB]?)\s*(?:views|view)\b", ctext, re.IGNORECASE)
        if vm:
            views = _clean_views_text(vm.group(1))

        uploader = None
        for sub in container.select("a[href]") if container else []:
            sh = sub.get("href") or ""
            if "/onlyfans-creators/" in sh or "/models/" in sh:
                uploader = sub.get_text(" ", strip=True) or None
                if uploader:
                    break

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
