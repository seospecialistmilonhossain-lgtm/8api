import httpx
import re
from bs4 import BeautifulSoup
from typing import Any, Optional

def can_handle(host: str) -> bool:
    return "gosexpod.com" in host.lower()

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
    
    # Selectors from research
    title_el = soup.select_one('h1') or soup.select_one('.video-title')
    title = title_el.get_text(strip=True) if title_el else ""
    
    thumbnail = ""
    meta_img = soup.find('meta', property='og:image')
    if meta_img:
        thumbnail = meta_img.get('content', '')
    
    # Extract video source
    video_url = None
    # Method 1: <video> tag
    video_tag = soup.find('video')
    if video_tag:
        source = video_tag.find('source')
        video_url = source.get('src') if source else video_tag.get('src')
    
    # Method 2: Flashvars/Scripts
    if not video_url:
        scripts = soup.find_all('script')
        for script in scripts:
            content = script.string or ""
            # Look for video_url in flashvars or similar
            m = re.search(r'video_url["\']?\s*:\s*["\']([^"\']+)["\']', content)
            if m:
                video_url = m.group(1)
                break

    return {
        "url": url,
        "title": title,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": "Gosexpod",
        "video": {
            "streams": [{"quality": "720p", "url": video_url, "format": "mp4"}] if video_url else [],
            "hls": None,
            "default": video_url,
            "has_video": video_url is not None
        }
    }

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict]:
    # gosexpod pagination: ?page=2
    url = f"{base_url}?page={page}" if "?" not in base_url else f"{base_url}&page={page}"
            
    html = await fetch_html(url)
    soup = BeautifulSoup(html, 'lxml')
    videos = []
    
    # Selectors from research: a.thumbs__item
    cards = soup.select('a.thumbs__item')
    for card in cards:
        href = card.get('href')
        if not href: continue
        
        img_el = card.select_one('.thumbs__img-holder img')
        thumb = img_el.get('src') if img_el else ""
        
        title_el = card.select_one('p.thumbs__info_text')
        title = title_el.get_text(strip=True) if title_el else ""
        
        duration_el = card.select_one('.thumbs__bage_right .thumbs__bage_text')
        duration = duration_el.get_text(strip=True) if duration_el else None

        videos.append({
            "url": f"https://www.gosexpod.com{href}" if href.startswith('/') else href,
            "title": title,
            "thumbnail_url": thumb,
            "duration": duration,
            "views": None,
            "uploader_name": "Gosexpod"
        })
        
        if len(videos) >= limit:
            break
            
    return videos
