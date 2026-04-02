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

# First argument: real cover URL (listing cards often use data: URLs in CSS instead)
_BANNER_RE = re.compile(
    r"loadBannerDirect\s*\(\s*['\"](https?://[^'\"]+)['\"]",
    re.I | re.DOTALL,
)
_STYLE_BG_URL_RE = re.compile(
    r"background-image\s*:\s*url\s*\(\s*(['\"]?)([^)]+?)\1\s*\)",
    re.I,
)

# og/twitter/json-ld often point at site logo, not the post cover
_THUMB_SKIP_SUBSTR = (
    "logo-2.png",
    "mirages/images/logo",
    "/usr/themes/mirages",
    "favicon",
    "/usr/plugins/tbxw/zw.png",
    "themes/mirages/images/51cg.png",
    "avatar.png",
    "icon-black.png",
    "/search/search@",
)


def _normalize_media_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    if not u.startswith("http"):
        return u
    m = re.match(r"(https?://[^/]+)(/.*)?$", u, re.I)
    if not m:
        return u
    base, path = m.group(1), m.group(2) or ""
    path = re.sub(r"/{2,}", "/", path)
    return base + path


def _is_placeholder_thumb(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in _THUMB_SKIP_SUBSTR)


def _first_post_content_image(soup: BeautifulSoup) -> Optional[str]:
    body = soup.select_one('.post-content[itemprop="articleBody"]')
    if body is None:
        body = soup.select_one("article.post .post-content")
    if body is None:
        return None
    for img in body.find_all("img"):
        for attr in ("data-xkrkllgl", "data-src", "data-original", "data-lazy-src", "src"):
            raw = img.get(attr)
            if not raw:
                continue
            v = str(raw).strip()
            if v.startswith("//"):
                v = "https:" + v
            if not v.startswith("http"):
                continue
            v = _normalize_media_url(v)
            if _is_placeholder_thumb(v):
                continue
            if v.lower().startswith("data:image"):
                continue
            return v
    return None


def _thumbnail_for_article_page(soup: BeautifulSoup) -> Optional[str]:
    thumb = _first_post_content_image(soup)
    if thumb:
        return thumb
    og = _meta(soup, prop="og:image")
    if og and not _is_placeholder_thumb(og):
        return _normalize_media_url(og.strip())
    tw = _meta(soup, name="twitter:image")
    if tw and not _is_placeholder_thumb(tw):
        return _normalize_media_url(tw.strip())
    for div in soup.find_all("div", class_=lambda c: c and "dplayer" in str(c).split()):
        if _dplayer_in_footer_ad_tree(div):
            continue
        raw = div.get("data-config")
        if not raw:
            continue
        try:
            cfg = json.loads(html_lib.unescape(raw))
        except json.JSONDecodeError:
            continue
        if not isinstance(cfg, dict) or _dplayer_cfg_is_ad(cfg):
            continue
        vid = cfg.get("video")
        if isinstance(vid, dict):
            pic = vid.get("pic") or vid.get("cover")
            if isinstance(pic, str) and pic.startswith("http") and not _is_placeholder_thumb(pic):
                return _normalize_media_url(pic)
    return None


def _tag_classes(el: Any) -> list[str]:
    if el is None:
        return []
    raw = el.get("class")
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return str(raw).split()


def _is_ad_listing_card(art: Any, a: Any) -> bool:
    """Mirror slots: post-card-ads and/or tjtagmanager; not all ads set rel=sponsored."""
    rel = a.get("rel") or []
    if any(str(x).lower() == "sponsored" for x in rel):
        return True
    if "tjtagmanager" in _tag_classes(a):
        return True
    mask = art.select_one(".post-card-mask")
    if "post-card-ads" in _tag_classes(mask):
        return True
    pc = art.select_one(".post-card")
    if "post-card-ads" in _tag_classes(pc):
        return True
    return False


def _dplayer_cfg_is_ad(cfg: dict[str, Any]) -> bool:
    v = cfg.get("video_ads_url")
    if v is not None and str(v).strip():
        return True
    v = cfg.get("ads_jump_url")
    if v is not None and str(v).strip():
        return True
    return False


def _dplayer_in_footer_ad_tree(div: Any) -> bool:
    el: Any = div
    for _ in range(28):
        if el is None:
            break
        eid = (el.get("id") or "").lower()
        ecls = " ".join(_tag_classes(el)).lower()
        if any(
            x in eid or x in ecls
            for x in (
                "article-bottom-ads",
                "article-bottom-apps",
                "adspop",
                "application-popup",
                "article-bottom-banner",
            )
        ):
            return True
        el = el.parent
    return False


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
    for div in soup.find_all("div", class_=lambda c: c and "dplayer" in str(c).split()):
        if _dplayer_in_footer_ad_tree(div):
            continue
        raw = div.get("data-config")
        if not raw:
            continue
        try:
            cfg = json.loads(html_lib.unescape(raw))
        except json.JSONDecodeError:
            continue
        if not isinstance(cfg, dict) or _dplayer_cfg_is_ad(cfg):
            continue
        vid = cfg.get("video")
        if not isinstance(vid, dict):
            continue
        u = vid.get("url")
        if not u or not isinstance(u, str):
            continue
        u = u.strip()
        if not u.startswith("http"):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
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
    thumb = _thumbnail_for_article_page(soup)

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
                if u and "zwrech.cn" in u and u not in seen_f:
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


def _url_from_blog_background_style(article: Any) -> Optional[str]:
    """Use CSS background only when it is a normal http(s) URL, not data:image base64."""
    for el in article.select(".blog-background"):
        style = el.get("style") or ""
        style = html_lib.unescape(style)
        m = _STYLE_BG_URL_RE.search(style)
        if not m:
            continue
        raw = (m.group(2) or "").strip().strip('"').strip("'")
        if not raw or raw.lower().startswith("data:"):
            continue
        if raw.startswith("//"):
            raw = "https:" + raw
        if raw.startswith("http") and not _is_placeholder_thumb(raw):
            return _normalize_media_url(raw)
    return None


def _card_thumbnail_from_article(article: Any) -> Optional[str]:
    # Prefer loadBannerDirect first URL arg (matches real CDN URL even when CSS is base64)
    blob = str(article)
    for m in _BANNER_RE.finditer(blob):
        u = _normalize_media_url(m.group(1).strip())
        if u.startswith("http") and not _is_placeholder_thumb(u):
            return u
    # Individual script nodes (in case str(article) omits edge cases)
    for script in article.find_all("script"):
        txt = script.string or script.get_text() or ""
        m = _BANNER_RE.search(txt)
        if m:
            u = _normalize_media_url(m.group(1).strip())
            if u.startswith("http") and not _is_placeholder_thumb(u):
                return u
    return _url_from_blog_background_style(article)


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
        if _is_ad_listing_card(art, a):
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
