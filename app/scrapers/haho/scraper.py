from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup

def can_handle(host: str) -> bool:
    return "haho.moe" in host.lower()

def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []

async def fetch_html(url: str) -> str:
    # Try different impersonations to bypass blocks
    impersonations = ["chrome120", "chrome110", "safari15_3"]
    last_error = None
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://haho.moe/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Cookie": "loop-view=thumb"
    }
    
    for imp in impersonations:
        try:
            async with AsyncSession(
                impersonate=imp,
                headers=headers,
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

    print(f"⚠️ Haho all curl_cffi attempts failed. Last error: {last_error}. Falling back to httpx...")
    import httpx
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    
    # Identify page type
    is_series = "/anime/" in url and len(url.rstrip("/").split("/")) == 5 # https://haho.moe/anime/nzm0xmlo
    is_episode = "/anime/" in url and len(url.rstrip("/").split("/")) > 5 # https://haho.moe/anime/nzm0xmlo/1
    
    # Title extraction
    title = "Unknown"
    title_tag = soup.select_one(".breadcrumb-item.active")
    if title_tag:
        title = title_tag.get_text(strip=True)
    else:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" — ")[0]

    # Thumbnail extraction
    thumbnail = None
    # Priority 1: Poster image (series page)
    poster = soup.select_one(".anime-poster img")
    if poster:
        thumbnail = poster.get("data-src") or poster.get("src")
    
    # Priority 2: og:image
    if not thumbnail:
        meta_thumb = soup.find("meta", property="og:image")
        if meta_thumb:
            thumbnail = meta_thumb.get("content")
    
    if thumbnail:
        if thumbnail.startswith("//"):
            thumbnail = "https:" + thumbnail
        elif thumbnail.startswith("/") and not thumbnail.startswith("//"):
            thumbnail = "https://haho.moe" + thumbnail

    # Detailed Metadata Extraction
    uploader = "Haho"
    tags = []
    views = "0"
    upload_date = None
    description = None

    # Meta info list
    info_list = soup.select(".anime-info-v2 li")
    for li in info_list:
        text = li.get_text(" ", strip=True) # Use space separator to avoid sticking text
        if "Official Title" in text:
            pass
        elif "Synonym" in text:
            syn = li.select_one("span")
            if syn: tags.append(f"Synonym: {syn.get_text(strip=True)}")
        elif "Type" in text:
            val = li.find("a")
            if val: tags.append(f"Type: {val.get_text(strip=True)}")
        elif "Status" in text:
            val = li.find("a")
            if val: tags.append(f"Status: {val.get_text(strip=True)}")
        elif "Release Date" in text:
            upload_date = text.split(":", 1)[-1].strip()
        elif "Views" in text:
            m = re.search(r'Views\s*[:\s]\s*([\d,]+)', text)
            if m: views = m.group(1).replace(",", "")
            else: views = text.split(":", 1)[-1].strip()
        elif "Content Rating" in text:
            val = li.find("a")
            if val: tags.append(f"Rating: {val.get_text(strip=True)}")
        elif "Production" in text:
            val = li.find("a")
            if val: tags.append(f"Production: {val.get_text(strip=True)}")
        elif "Censorship" in text:
            val = li.find("a")
            if val: tags.append(f"Censorship: {val.get_text(strip=True)}")
        elif "Resolution" in text:
            res_links = li.select("a")
            if res_links:
                res_text = ", ".join([r.get_text(strip=True) for r in res_links])
                tags.append(f"Resolution: {res_text}")

    # Specific Tags (Genres)
    for tag_a in soup.select('a[href*="/genre/"]'):
        tag_text = tag_a.get_text(strip=True)
        if tag_text and tag_text not in tags:
            tags.append(tag_text)

    # Episode-specific tags (Censorship, Source, etc.)
    # These are usually buttons/badges above the player
    for btn in soup.select(".btn-group button, .badge"):
        btn_text = btn.get_text(strip=True)
        if btn_text in ["Censored", "Uncensored", "DVD", "WEB", "Blu-ray"]:
            tags.append(btn_text)

    # Video Streams
    streams = []
    default_url = None

    # If it's a series page, we need to find the first episode to get streams
    if is_series and not is_episode:
        first_ep = soup.select_one(".episodelist a")
        if first_ep:
            ep_url = first_ep.get("href")
            if ep_url:
                if ep_url.startswith("/"): ep_url = "https://haho.moe" + ep_url
                # Recursive call to get streams from episode page
                ep_data = await scrape(ep_url)
                streams = ep_data.get("video", {}).get("streams", [])
                default_url = ep_data.get("video", {}).get("default")
    
    # If it's an episode page, extract streams directly
    if not streams:
        # Search for m3u8 in the HTML source
        m3u8_links = re.findall(r'(https?://[^\s\'"]+\.m3u8[^\s\'"]*)', html)
        for link in m3u8_links:
            clean_link = link.replace("\\/", "/").replace("&amp;", "&")
            if clean_link not in [s["url"] for s in streams]:
                streams.append({
                    "quality": "default",
                    "url": clean_link,
                    "format": "hls"
                })
                if not default_url:
                    default_url = clean_link

        # Fallback to mp4
        mp4_links = re.findall(r'(https?://[^\s\'"]+\.mp4[^\s\'"]*)', html)
        for link in mp4_links:
            clean_link = link.replace("\\/", "/").replace("&amp;", "&")
            if clean_link not in [s["url"] for s in streams]:
                streams.append({
                    "quality": "default",
                    "url": clean_link,
                    "format": "mp4"
                })
                if not default_url:
                    default_url = clean_link

    video_data = {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": "N/A",
        "views": views,
        "uploader_name": uploader,
        "category": "Hentai",
        "tags": tags,
        "upload_date": upload_date,
        "video": video_data,
        "related_videos": []
    }

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    url = base_url
    if page > 1:
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}page={page}"

    try:
        html = await fetch_html(url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items = []
    
    # Support both Thumbnail view (a.film-grain) and List view (li)
    # Thumbnail View selector
    for item in soup.select('a.film-grain'):
        try:
            href = item.get("href")
            if not href: continue
            if href.startswith("/"): href = "https://haho.moe" + href
            
            # Title: Series + Episode
            series = item.select_one(".overlay .title")
            episode = item.select_one(".overlay .episode-title")
            title_parts = []
            if series: title_parts.append(series.get_text(strip=True))
            if episode: title_parts.append(episode.get_text(strip=True))
            title = " - ".join(title_parts) or "Unknown"
            
            # Thumbnail
            img = item.select_one("img.image")
            thumb = None
            if img:
                thumb = img.get("src") or img.get("data-src") or img.get("data-original")
                if thumb:
                    if thumb.startswith("//"): thumb = "https:" + thumb
                    elif thumb.startswith("/") and not thumb.startswith("//"): thumb = "https://haho.moe" + thumb
            
            # Views
            views = "0"
            view_tag = item.select_one(".top-overlay.views")
            if view_tag:
                views = view_tag.get_text(strip=True)
            
            # Rating and Date
            upload_date = None
            date_tag = item.select_one(".episode-date")
            if date_tag: upload_date = date_tag.get_text(strip=True)
            
            tags = []
            rating_tag = item.select_one(".top-overlay.rating")
            if rating_tag: tags.append(f"Rating: {rating_tag.get_text(strip=True)}")
            
            items.append({
                "url": href,
                "title": title.strip(),
                "thumbnail_url": thumb,
                "views": views,
                "upload_date": upload_date,
                "uploader_name": "Haho"
            })
        except Exception: continue

    # List View selector (if Thumbnail View found items, we might be done, but let's be safe)
    if not items:
        for li in soup.select('li[class^="episode-"]'):
            try:
                link = li.find("a")
                if not link: continue
                href = link.get("href")
                if not href: continue
                if href.startswith("/"): href = "https://haho.moe" + href
                
                # Title: Series + Episode
                series = link.select_one(".label .text-primary")
                episode = link.select_one(".label small")
                title_parts = []
                if series: title_parts.append(series.get_text(strip=True))
                if episode: title_parts.append(episode.get_text(strip=True))
                title = " - ".join(title_parts) or link.get("title") or "Unknown"
                
                # Views
                views = "0"
                view_tag = link.select_one(".view")
                if view_tag:
                    views = view_tag.get_text(strip=True)
                
                # Rating and Date
                upload_date = None
                date_tag = link.select_one(".date")
                if date_tag: upload_date = date_tag.get_text(strip=True)
                
                tags = []
                rating_tag = link.select_one(".rating")
                if rating_tag: tags.append(f"Rating: {rating_tag.get_text(strip=True)}")
                
                items.append({
                    "url": href,
                    "title": title.strip(),
                    "thumbnail_url": None, # List view usually lacks thumbs or has icons
                    "views": views,
                    "upload_date": upload_date,
                    "uploader_name": "Haho"
                })
            except Exception: continue
            
    # Anime Index View selector (if both previous views found nothing)
    if not items:
        # Looking for generic anime cards linking to /anime/
        for item in soup.find_all("a", href=re.compile(r"/anime/[\w-]+(\?.*)?$")):
            # Some buttons also link to anime, so exclude ones with button classes
            if "btn" in item.get("class", []): continue
            
            try:
                href = item.get("href")
                if not href: continue
                if href.startswith("/"): href = "https://haho.moe" + href
                
                # Title
                title = item.get("title")
                if not title:
                    title_divs = item.find_all("div", recursive=False)
                    if title_divs:
                        last_div = title_divs[-1]
                        title_span = last_div.find("span")
                        if title_span: title = title_span.get_text(strip=True)
                title = title or "Unknown"
                
                # Thumbnail
                img = item.find("img")
                thumb = None
                if img:
                    thumb = img.get("src") or img.get("data-src") or img.get("data-original")
                    if thumb:
                        if thumb.startswith("//"): thumb = "https:" + thumb
                        elif thumb.startswith("/") and not thumb.startswith("//"): thumb = "https://haho.moe" + thumb
                
                # Views and Rating
                views = "0"
                tags = []
                
                eye_icon = item.select_one("i.fa-eye")
                if eye_icon and eye_icon.parent:
                    views = eye_icon.parent.get_text(strip=True)
                        
                heart_icon = item.select_one("i.fa-heart")
                if heart_icon and heart_icon.parent:
                    tags.append(f"Rating: {heart_icon.parent.get_text(strip=True)}")
                
                items.append({
                    "url": href,
                    "title": title.strip(),
                    "thumbnail_url": thumb,
                    "views": views,
                    "upload_date": None,
                    "uploader_name": "Haho"
                })
            except Exception: continue

    # Filter out duplicates and items with too short titles
    seen = set()
    filtered_items = []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            # Clean title
            it["title"] = re.sub(r'[\s\n\r]+', ' ', it["title"]).strip()
            if len(it["title"]) > 1:
                filtered_items.append(it)
                
    return filtered_items[:limit]
