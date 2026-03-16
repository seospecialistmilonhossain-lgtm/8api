import httpx
import re
import json
import os
from bs4 import BeautifulSoup
from typing import Any, Optional

def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def can_handle(host: str) -> bool:
    return "watcherotic.com" in host.lower()

async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

async def scrape(url: str) -> dict:
    html = await fetch_html(url)
    soup = BeautifulSoup(html, 'lxml')
    
    title_el = soup.select_one('h1')
    title = title_el.get_text(strip=True) if title_el else ""
    
    thumbnail = ""
    meta_img = soup.find('meta', property='og:image')
    if meta_img:
        thumbnail = meta_img.get('content', '')
    
    if not thumbnail:
        # Fallback to finding the first large image or video poster
        video_el = soup.find('video')
        if video_el and video_el.get('poster'):
            thumbnail = video_el.get('poster')
        else:
            img_el = soup.select_one('.video-player img, #player img')
            if img_el:
                thumbnail = img_el.get('src') or img_el.get('data-src')

    if thumbnail and thumbnail.startswith('//'):
        thumbnail = f"https:{thumbnail}"
    elif thumbnail and not thumbnail.startswith('http'):
        thumbnail = f"https://watcherotic.com{thumbnail}"
    
    # Extract metadata from header area
    duration = None
    views = None
    upload_date = None
    
    # The subagent identified icons for metadata. Let's try to find text near icons.
    # Usually in a container near the title.
    metadata_container = soup.select_one('.video-header, .video-info, .player-header')
    if not metadata_container:
        # Fallback to searching the whole page for metadata icons/patterns
        metadata_container = soup
        
    dur_el = metadata_container.find(class_=re.compile(r'time|duration', re.I))
    if dur_el:
        duration = dur_el.get_text(strip=True)
        
    views_el = metadata_container.find(class_=re.compile(r'views?|eye', re.I))
    if not views_el:
        # Try finding text next to eye icon
        eye_icon = metadata_container.find('i', class_=re.compile(r'icon-eye|fa-eye', re.I))
        if eye_icon:
            views_el = eye_icon.parent
    if views_el:
        views = views_el.get_text(strip=True).replace('views', '').strip()

    date_el = metadata_container.find(class_=re.compile(r'date|calendar', re.I))
    if date_el:
        upload_date = date_el.get_text(strip=True)

    # Extract video source from flashvars or ld+json
    video_url = None
    scripts = soup.find_all('script')
    for script in scripts:
        content = script.string or ""
        m = re.search(r'flashvars\.video_url\s*=\s*["\']([^"\']+)["\']', content)
        if m:
            video_url = m.group(1)
            break
            
    if not video_url:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if data.get("@type") == "VideoObject":
                    video_url = data.get("contentUrl")
                    break
            except:
                continue

    return {
        "url": url,
        "title": title,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "upload_date": upload_date,
        "uploader_name": "Watcherotic",
        "video": {
            "streams": [{"quality": "720p", "url": video_url, "format": "mp4"}] if video_url else [],
            "hls": None,
            "default": video_url,
            "has_video": video_url is not None
        }
    }

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict]:
    url = base_url
    if page > 1:
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}page={page}"
            
    html = await fetch_html(url)
    soup = BeautifulSoup(html, 'lxml')
    videos = []
    
    # Specific selectors for WatchErotic list items
    cards = soup.select('.item, .video-box, .thumb, .thumb_rel')
    for card in cards:
        link_el = card.find('a')
        if not link_el: continue
        
        href = link_el.get('href')
        if not href or '/video/' not in href: continue
        
        img_el = card.find('img')
        # Prioritize data-src/data-webp over src (which might be a placeholder)
        thumb = img_el.get('data-src') or img_el.get('data-webp') or img_el.get('src') if img_el else ""
        if thumb and thumb.startswith('//'):
            thumb = f"https:{thumb}"
        elif thumb and not thumb.startswith('http'):
            thumb = f"https://watcherotic.com{thumb}"
            
        # Title from div.title or img alt or link title
        title_div = card.select_one('.title')
        title = title_div.get_text(strip=True) if title_div else link_el.get('title') or img_el.get('alt', '') if img_el else ""
        
        # Duration from .time or .duration
        duration = None
        dur_el = card.select_one('.time, .duration, .length')
        if dur_el:
            duration = dur_el.get_text(strip=True)
            
        # Metadata from thumb-bottom
        views = None
        upload_date = None
        
        metadata_items = card.select('.thumb-item')
        for item in metadata_items:
            text = item.get_text(strip=True)
            if item.find('svg', class_='icon-eye') or item.find('i', class_='icon-eye'):
                views = text
            elif 'thumb-item-date' in item.get('class', []):
                upload_date = text
            elif item.find('svg', class_='icon-calendar') or item.find('i', class_='icon-calendar'):
                upload_date = text

        videos.append({
            "url": f"https://watcherotic.com{href}" if href.startswith('/') else href,
            "title": title,
            "thumbnail_url": thumb,
            "duration": duration,
            "views": views,
            "upload_date": upload_date,
            "uploader_name": "Watcherotic"
        })
        
        if len(videos) >= limit:
            break
            
    return videos
