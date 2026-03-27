from __future__ import annotations
import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from app.core.pool import fetch_html

def can_handle(host: str) -> bool:
    return "rule34video.com" in host.lower()

def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None

def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    t = getattr(el, "get_text", None)
    if callable(t):
        return t(strip=True) or None
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
    for k in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return None

async def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _first_non_empty(_meta(soup, prop="og:title"), _text(soup.find("title")))
    if title and title.endswith(" - Rule 34 Video"):
        title = title[:-18]

    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))
    thumbnail = _meta(soup, prop="og:image")
    
    # rule34video embeds the video url directly in javascript
    streams = []
    default_url = None
    
    # Check scripts for video_url specifically
    m_video = re.search(r"video_url\s*:\s*['\"](https?://[^'\"]+)['\"]", html)
    potential_urls = []
    if m_video:
        potential_urls.append(m_video.group(1).replace("&amp;", "&"))
    
    # Fallback: find any mp4 link in scripts
    for s in soup.find_all("script"):
        content = s.string or ""
        urls = re.findall(r'(https?://[^\s\'"]+\.(?:mp4|m3u8)[^\s\'"]*)', content)
        for u in urls:
            clean_u = u.replace("&amp;", "&")
            if clean_u not in potential_urls:
                potential_urls.append(clean_u)

    # Resolve redirects for the first few potential URLs to find the real CDN link
    resolved_urls = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://rule34video.com/"
    }
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10.0) as client:
        for u in potential_urls[:2]:  # Only check first two to avoid excessive latency
            if "get_file" in u or "rule34video.com" in u:
                try:
                    # Use HEAD to quickly follow redirects without downloading content
                    resp = await client.head(u)
                    final_url = str(resp.url)
                    if final_url not in resolved_urls:
                        resolved_urls.append(final_url)
                except Exception:
                    resolved_urls.append(u)
            else:
                if u not in resolved_urls:
                    resolved_urls.append(u)

    for r_url in resolved_urls:
        streams.append({
            "quality": "default",
            "url": r_url,
            "format": "hls" if ".m3u8" in r_url.lower() else "mp4"
        })
        if not default_url:
            default_url = r_url

    video_data = {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

    tags = []
    # try to hit a tags div, usually class="tags" or related
    for a in soup.select('a[href*="/tags/"]'):
        t = _text(a)
        if t and t not in tags:
            tags.append(t)
            
    # Try parsing meta keywords
    if not tags:
        kw = _meta(soup, name="keywords")
        if kw:
            tags = [k.strip() for k in kw.split(",") if k.strip()]
            
    # Extract views, duration by generic patterns if needed
    duration = None
    dur_elem = soup.find(class_=re.compile("duration|time"))
    if dur_elem:
        duration = _text(dur_elem)

    # uploader
    uploader = None
    up_elem = soup.select_one('a[href*="/user/"], a[href*="/channel/"]')
    if up_elem:
        uploader = _text(up_elem)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
        "category": None,
        "tags": tags,
        "video": video_data,
        "related_videos": []
    }

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return await parse_page(html, url)

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    root = base_url if base_url.endswith("/") else base_url + "/"

    candidates: list[str] = []
    sep = "&" if "?" in root else "?"
    if page <= 1:
        candidates.append(root)
    else:
        if "/categories/" in root or "/tags/" in root:
             candidates.append(f"{root.rstrip('/')}/{page}/")
        candidates.extend([
            f"{root}latest-updates/{page}/",
            f"{root}{sep}page={page}",
        ])

    html = ""
    used = ""
    for c in candidates:
        try:
            html = await fetch_html(c)
            used = c
            if html:
                break
        except Exception:
            continue

    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    base_uri = httpx.URL(used)

    items = []
    seen = set()

    for item in soup.select('.item'):
        a = item.find('a', href=True)
        if not a:
            continue
            
        href = a.get("href")
        if not href or "/video/" not in href:
            continue
            
        try:
            abs_url = str(base_uri.join(href))
        except Exception:
            abs_url = href
            
        if abs_url in seen:
            continue
            
        seen.add(abs_url)
        img = item.find('img')
        thumb = _best_image_url(img)

        title_el = item.select_one('.title')
        title = _text(title_el) or (img.get('alt') if img else None)

        dur_el = item.select_one('.duration')
        duration = _text(dur_el)

        items.append({
            "url": abs_url,
            "title": title or "Unknown Video",
            "thumbnail_url": thumb,
            "duration": duration,
            "views": None,
            "uploader_name": "rule34video",
        })

    return items
