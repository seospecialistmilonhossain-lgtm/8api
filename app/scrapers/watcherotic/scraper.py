import httpx
import re
import json
from bs4 import BeautifulSoup
from typing import Any, Optional

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
    
    # Extract video source from flashvars
    video_url = None
    scripts = soup.find_all('script')
    for script in scripts:
        content = script.string or ""
        # The research showed flashvars.video_url
        m = re.search(r'flashvars\.video_url\s*=\s*["\']([^"\']+)["\']', content)
        if m:
            video_url = m.group(1)
            break
            
    # Alternative: check for VideoObject JSON-LD
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
        "duration": None,
        "views": None,
        "uploader_name": "Watcherotic",
        "video": {
            "streams": [{"quality": "720p", "url": video_url, "format": "mp4"}] if video_url else [],
            "hls": None,
            "default": video_url,
            "has_video": video_url is not None
        }
    }

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict]:
    # watcherotic pagination usually ?page=2 or similar
    # Home page has 0-indexed or 1-indexed? Research showed position 1 had links
    url = base_url
    if page > 1:
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}page={page}"
            
    html = await fetch_html(url)
    soup = BeautifulSoup(html, 'lxml')
    videos = []
    
    # Selectors from research: .item, .video-box
    cards = soup.select('.item, .video-box, div[class*="thumb"]')
    for card in cards:
        link_el = card.find('a')
        if not link_el: continue
        
        href = link_el.get('href')
        if not href or '/video/' not in href: continue
        
        img_el = card.find('img')
        thumb = img_el.get('src') or img_el.get('data-src') if img_el else ""
        
        title = link_el.get_text(strip=True) or img_el.get('alt', '') if img_el else ""
        
        duration = None
        dur_el = card.find(class_=re.compile(r'duration|time|length', re.I))
        if dur_el:
            duration = dur_el.get_text(strip=True)

        videos.append({
            "url": f"https://watcherotic.com{href}" if href.startswith('/') else href,
            "title": title,
            "thumbnail_url": thumb,
            "duration": duration,
            "views": None,
            "uploader_name": "Watcherotic"
        })
        
        if len(videos) >= limit:
            break
            
    return videos
