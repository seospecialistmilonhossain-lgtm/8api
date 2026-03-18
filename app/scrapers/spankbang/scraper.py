from __future__ import annotations

import json
import re
import os
import ast
from typing import Any, Optional

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

def can_handle(host: str) -> bool:
    return "spankbang.com" in host.lower()

def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

async def fetch_html(url: str) -> str:
    # Try different impersonations to bypass blocks
    impersonations = ["chrome120", "chrome110", "safari15_3"]
    last_error = None
    
    for imp in impersonations:
        try:
            async with AsyncSession(
                impersonate=imp,
                headers={
                    "Referer": "https://spankbang.com/",
                    "Cookie": "age_verified=1; sb_theme=dark",
                },
                timeout=20.0
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 403:
                    last_error = f"403 Forbidden with {imp}"
                    continue
                resp.raise_for_status()
                return resp.text
        except Exception as e:
            last_error = f"{imp} error: {e}"
            continue

    print(f"⚠️ SpankBang all curl_cffi attempts failed. Last error: {last_error}. Falling back to httpx...")
    # Fallback to httpx if curl_cffi fails
    from app.core import pool
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Cookie": "age_verified=1; sb_theme=dark",
    }
    resp = await pool.client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text


def _extract_video_streams(html: str) -> dict[str, Any]:
    streams = []
    seen_urls = set()
    
    # 1. Parse <source> tags from video element
    soup = BeautifulSoup(html, "lxml")
    sources = soup.select("video source, source") 
    for source in sources:
        src = source.get("src") or source.get("data-src")
        if src and (src.startswith("http") or src.startswith("//")):
            if src.startswith("//"): src = "https:" + src
            
            # Skip invalid URLs
            if "/t/" in src and "td.mp4" in src: continue
            if "tbv.sb-cd.com" in src: continue
            
            # Extract quality
            quality = source.get("size") or source.get("label") or source.get("data-res")
            if not quality:
                m = re.search(r'[-_](\d+p)\.mp4', src)
                quality = m.group(1).replace('p', '') if m else "unknown"
            
            fmt = "hls" if ".m3u8" in src else "mp4"
            
            if src not in seen_urls:
                streams.append({"quality": str(quality), "url": src, "format": fmt})
                seen_urls.add(src)

    # 2. ALWAYS Check for stream_data object (contains more qualities + 4k)
    m_data = re.search(r'var\s+stream_data\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m_data:
        try:
            # Try json.loads first, then ast.literal_eval for single quotes
            raw_data = m_data.group(1)
            try:
                data = json.loads(raw_data)
            except Exception:
                data = ast.literal_eval(raw_data)

            # print("DEBUG: stream_data keys:", list(data.keys()))
            for q, urls in data.items():
                # print(f"DEBUG: Processing key {q} with value {urls}")
                if not urls: continue
                
                # Filter out metadata keys
                if q in ['cover_image', 'thumbnail', 'stream_raw_id', 'stream_sheet', 'length', 'main']:
                    continue
                    
                # Clean key names (e.g. m3u8_1080p -> 1080p)
                clean_q = q.replace('m3u8_', '').replace('p', '')
                
                url = None
                if isinstance(urls, list) and len(urls) > 0:
                    url = urls[0]
                elif isinstance(urls, str):
                    url = urls
                    
                if url:
                    url = url.replace('\\/', '/')
                    fmt = "hls" if ".m3u8" in url else "mp4"
                    streams.append({
                        "quality": clean_q,
                        "url": url,
                        "format": fmt
                    })

        except Exception as e:
            pass

    # 3. Fallback: Check for simple stream_url variable
    if not streams:
        m = re.search(r'stream_url\s*=\s*["\'](https?://.*?)["\']', html)
        if m:
            video_url = m.group(1)
            streams.append({
                "quality": "default",
                "url": video_url,
                "format": "mp4"
            })

    # Sort streams: High quality first (4k > 1080 > 720 > 480 > 240)
    def quality_rank(s):
        q = s['quality']
        if 'k' in q.lower(): return 10000 
        if q.isdigit(): return int(q)
        return 0
    
    streams.sort(key=quality_rank, reverse=True)

    # Determine default: Prioritize "m3u8" quality (master playlist), then any HLS, then highest MP4
    default_url = None
    if streams:
        # 1. Try to find the master HLS playlist (quality="m3u8")
        master_hls = next((s for s in streams if s.get("quality") == "m3u8"), None)
        if master_hls:
            default_url = master_hls["url"]
        else:
            # 2. Try to find ANY HLS stream
            hls_stream = next((s for s in streams if s.get("format") == "hls"), None)
            if hls_stream:
                default_url = hls_stream["url"]
            else:
                # 3. Fallback to first (highest quality) stream
                default_url = streams[0]["url"]

    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    
    title = None
    t_tag = soup.select_one("h1")
    if t_tag: title = t_tag.get_text(strip=True)
    
    thumbnail = None
    # og:image
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb: thumbnail = meta_thumb.get("content")
    
    duration = "0:00"
    # Try to find duration in meta
    # <meta itemprop="duration" content="PT6M33S" /> is standard but SpankBang varies
    # Or parsing from sidebar
    
    uploader = "SpankBang"
    u_el = soup.select_one(".user a, .user-name")
    if u_el: uploader = u_el.get_text(strip=True)
    
    tags = []
    # SpankBang stores tags in meta keywords
    meta_keywords = soup.find("meta", attrs={"name": "keywords"})
    if meta_keywords and meta_keywords.get("content"):
        keywords = meta_keywords.get("content")
        # Split by comma and clean up
        tags = [t.strip() for t in keywords.split(",") if t.strip()]
    
    # Fallback: try HTML tags
    if not tags:
        for t in soup.select(".categories a, .tags a"):
            txt = t.get_text(strip=True)
            if txt and txt.lower() not in ["tags", "categories"]:
                tags.append(txt)
            
    video_data = _extract_video_streams(html)
    
    return {
        "url": url,
        "title": title or "Unknown",
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": "0", 
        "uploader_name": uploader or "SpankBang",
        "category": "SpankBang",
        "tags": tags,
        "video": video_data,
        "related_videos": [], 
        "preview_url": None
    }

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # Pagination: spankbang.com/upcoming/2
    
    url = base_url
    
    # Spankbang standard: /2 for page 2
    if page > 1:
        url = base_url.rstrip("/")
        if url == "https://spankbang.com":
             url = "https://spankbang.com/trending_videos"
        elif "/s/" in url:
             # Ensure /s/ URLs keep structure: /s/query/page
             # If url was .../s/amateur, make it .../s/amateur/2
             pass 
        
        # Append page number
        url = f"{url}/{page}"

    try:
        html = await fetch_html(url)
    except Exception:
        return []
        
    soup = BeautifulSoup(html, "lxml")
    items = []
    
    # Updated Selectors based on browser analysis
    # Strategy: Find all potential video items, then group by parent container.
    # The container with the most items is the Main List.
    container_selector = ".js-video-item, .video-item, .video-list-video, [data-testid='video-item']"
    
    # Target only the main content area to avoid featured items in the header
    main_content = soup.select_one('main[data-testid="main"]')
    if main_content:
        selected_items = main_content.select(container_selector)
    else:
        selected_items = soup.select(container_selector)
    
    for item in selected_items:
        try:
            # Get the main link (usually a.thumb for thumbnail)
            link = item.select_one("a")
            if not link: continue
            
            href = link.get("href")
            if not href: continue
             
            if href.startswith("/"): href = "https://spankbang.com" + href
            
            # Title: look for the title link in the info section
            title = "Unknown"
            # In new layout, title is in a p -> a -> span
            title_tag = item.select_one('p a span, .n')
            if title_tag:
                title = title_tag.get_text(strip=True)

            # Thumbnail
            img = item.find("img")
            thumb = None
            if img:
                thumb = img.get("data-src") or img.get("src")
                if thumb:
                    if thumb.startswith("//"): thumb = "https:" + thumb
                    # Upgrade resolution: w:300 -> w:1200
                    thumb = thumb.replace("w:300", "w:1200")
                
            # Duration: in data-testid="video-item-length"
            duration = "0:00"
            dur_tag = item.select_one('[data-testid="video-item-length"]')
            if dur_tag: 
                duration = dur_tag.get_text(strip=True)
            
            # Views: Use data-testid="views"
            views = "0"
            views_tag = item.select_one('[data-testid="views"]')
            if views_tag:
                # Often views are in a nested span with md:text-body-md class
                views_text_tag = views_tag.select_one('span.md\\:text-body-md, span:last-child')
                views = (views_text_tag or views_tag).get_text(strip=True)
            
            # Uploader: Often uses data-testid="title" for the user/pornstar link
            uploader = "Unknown"
            uploader_tag = item.select_one('[data-testid="title"] span, span.text-action-tertiary')
            if uploader_tag:
                uploader = uploader_tag.get_text(strip=True)
            
            items.append({
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader
            })
            
        except Exception:
            continue
            
    if "trending_videos" in url and page == 1:
        items = items[6:]
        
    return items
