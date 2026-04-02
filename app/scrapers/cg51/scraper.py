from __future__ import annotations

import html as html_lib
import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_HOST_MARKERS = (
    "51cg1.com",
    "cg51.com",
    "chigua.com",
)

_BANNER_RE = re.compile(r"loadBannerDirect\s*\(\s*['\"]([^'\"]+)['\"]")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    return any(h == m or h.endswith("." + m) for m in _HOST_MARKERS)


def get_categories() -> list[dict]:
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(d, "categories.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(25.0, connect=20.0),
        headers=headers,
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        t = soup.find("meta", attrs={"property": prop})
        if t and t.get("content"):
            return str(t["content"]).strip()
    if name:
        t = soup.find("meta", attrs={"name": name})
        if t and t.get("content"):
            return str(t["content"]).strip()
    return None


def _parse_dplayer_hls_urls(soup: BeautifulSoup) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for div in soup.find_all("div", class_=lambda c: c and "dplayer" in c.split()):
        raw = div.get("data-config")
        if not raw:
            continue
        try:
            cfg = json.loads(html_lib.unescape(raw))
        except json.JSONDecodeError:
            continue
        vid = cfg.get("video") if isinstance(cfg, dict) else None
        if not isinstance(vid, dict):
            continue
        u = vid.get("url")
        if u and isinstance(u, str) and u not in seen:
            seen.add(u)
            out.append(u.strip())
    return out


def _video_block_from_urls(urls: list[str]) -> dict[str, Any]:
    if not urls:
        return {
            "streams": [],
            "hls": None,
            "default": None,
            "has_video": False,
        }
    streams: list[dict[str, str]] = []
    for i, u in enumerate(urls):
        label = "adaptive" if len(urls) == 1 else f"part{i + 1}"
        streams.append({"quality": label, "url": u, "format": "hls"})
    primary = urls[0]
    return {
        "streams": streams,
        "hls": primary,
        "default": primary,
        "has_video": True,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1.post-title") or soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else (_meta(soup, prop="og:title") or "")

    description = _meta(soup, prop="og:description") or _meta(soup, name="description")
    thumb = _meta(soup, prop="og:image")

    author_el = soup.select_one('span[itemprop="author"]') or soup.find("meta", attrs={"name": "author"})
    uploader = None
    if author_el:
        uploader = author_el.get_text(strip=True) if hasattr(author_el, "get_text") else author_el.get("content")

    upload_date = None
    pd = soup.find("meta", attrs={"itemprop": "datePublished"})
    if pd and pd.get("content"):
        upload_date = str(pd["content"]).strip()
    elif soup.find("time", attrs={"datetime": True}):
        upload_date = soup.find("time", attrs={"datetime": True}).get("datetime")

    tags: list[str] = []
    kw = _meta(soup, name="keywords")
    if kw:
        tags = [x.strip() for x in re.split(r"[,，]", kw) if x.strip()]

    hls_urls = _parse_dplayer_hls_urls(soup)
    if not hls_urls:
        seen_f: set[str] = set()
        for script in soup.find_all("script"):
            txt = script.string or script.get_text() or ""
            for m in re.finditer(r"https?://[^\s\"']+\.m3u8[^\s\"']*", txt):
                u = m.group(0).rstrip("\\/,'\"")
                if u and u not in seen_f:
                    seen_f.add(u)
                    hls_urls.append(u)

    return {
        "url": url,
        "title": title or None,
        "description": description,
        "thumbnail_url": thumb,
        "duration": None,
        "views": None,
        "uploader_name": uploader,
        "category": None,
        "tags": tags or None,
        "upload_date": upload_date,
        "video": _video_block_from_urls(hls_urls),
    }


def _listing_page_url(base_url: str, page: int) -> str:
    base_url = (base_url or "").strip()
    if not base_url:
        return "https://51cg1.com/"
    u = base_url.rstrip("/")
    if page <= 1:
        return u + "/"
    return f"{u}/page/{page}/"


def _card_thumbnail_from_article(article: Any) -> Optional[str]:
    for script in article.find_all("script", type="text/javascript"):
        txt = script.string or script.get_text() or ""
        m = _BANNER_RE.search(txt)
        if m:
            return m.group(1).strip()
    return None


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    list_url = _listing_page_url(base_url, page)
    html = await fetch_html(list_url)
    soup = BeautifulSoup(html, "lxml")
    origin = f"{urlparse(list_url).scheme}://{urlparse(list_url).netloc}"

    out: list[dict[str, Any]] = []
    index = soup.select_one("#index") or soup.select_one("#archive") or soup
    for art in index.select("article[itemtype*='BlogPosting']"):
        a = art.find("a", href=True)
        if not a:
            continue
        rel = a.get("rel")
        if rel and any("sponsored" == str(x).lower() for x in rel):
            continue
        href = str(a["href"]).strip()
        if "/archives/" not in href:
            continue
        full = urljoin(origin + "/", href)

        title_el = art.select_one("h2.post-card-title") or art.select_one('[itemprop="headline"]')
        t = title_el.get_text(" ", strip=True) if title_el else None
        if t:
            t = re.sub(r"\s+", " ", t)

        thumb = _card_thumbnail_from_article(art)
        info_spans = art.select(".post-card-info span")
        uploader = None
        upload_date = None
        category_txt = None
        for sp in info_spans:
            if sp.get("itemprop") == "datePublished" and sp.get("content"):
                upload_date = str(sp["content"]).strip()
            elif sp.get("itemprop") == "author":
                uploader = sp.get_text(" ", strip=True)
            elif not sp.get("itemprop"):
                txt = sp.get_text(" ", strip=True)
                if txt and "•" not in txt and len(txt) > 2:
                    category_txt = txt

        item: dict[str, Any] = {"url": full}
        if t:
            item["title"] = t
        if thumb:
            item["thumbnail_url"] = thumb
        if uploader:
            item["uploader_name"] = uploader
        if upload_date:
            item["upload_date"] = upload_date
        if category_txt:
            item["category"] = category_txt
        out.append(item)
        if len(out) >= limit:
            break

    return out
