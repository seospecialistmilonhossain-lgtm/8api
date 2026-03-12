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
    return "brazzpw.com" in host.lower()

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
    
    title = soup.find('h1').get_text(strip=True) if soup.find('h1') else ""
    thumbnail = ""
    meta_img = soup.find('meta', property='og:image')
    if meta_img:
        thumbnail = meta_img.get('content', '')
    
    # Extract video source from iframe
    video_url = None
    iframe = soup.find('iframe', src=re.compile(r'/player/|/get_file/'))
    if iframe:
        iframe_src = iframe.get('src')
        if iframe_src:
            if not iframe_src.startswith('http'):
                iframe_src = f"https://brazzpw.com{iframe_src}"
            
            # Extract id and encoded path from player URL
            # https://brazzpw.com/player/?id=11472677&p=aHR0cHM6...
            import base64
            from urllib.parse import urlparse, parse_qs
            
            parsed_src = urlparse(iframe_src)
            params = parse_qs(parsed_src.query)
            
            p_val = params.get('p', [None])[0]
            if p_val:
                try:
                    # The 'p' parameter often contains a base64 encoded thumbnail URL
                    # which reveals the media server structure.
                    decoded_p = base64.b64decode(p_val).decode()
                    # Example: https://media-public-fl.project1content.com/.../poster/poster_01.jpg
                    # Usually the video is in the same folder as the poster, often named 'video.mp4' or similar.
                    # Or we can just use the iframe_src directly as it might be an embed player.
                    # But for AppHub, we prefer direct MP4.
                    
                    if 'poster_01.jpg' in decoded_p:
                        # Try to guess video URL from poster URL
                        video_url = decoded_p.replace('poster/poster_01.jpg', 'video_hd.mp4')
                        # Check if it works or fallback to get_file
                except:
                    pass
            
    # Fallback to script extraction if needed
    hls_url = None
    if not video_url:
        if iframe:
            iframe_src = iframe.get('src')
            if iframe_src:
                if not iframe_src.startswith('http'):
                    iframe_src = f"https://brazzpw.com{iframe_src}"
                
                try:
                    player_html = await fetch_html(iframe_src)
                    
                    # Try to find MP4
                    m = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4[^"\']*)["\']', player_html)
                    if m:
                        video_url = m.group(1)
                    
                    # Try to find HLS
                    # src: "m3u8_11473421.m3u8?hash=177332&time=177332"
                    m_hls = re.search(r'src\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', player_html)
                    if m_hls:
                        hls_val = m_hls.group(1)
                        if not hls_val.startswith('http'):
                            from urllib.parse import urljoin
                            hls_url = urljoin(iframe_src, hls_val)
                        else:
                            hls_url = hls_val
                except:
                    pass


    if not video_url and not hls_url:
        scripts = soup.find_all('script')
        for script in scripts:
            content = script.string or ""
            # Look for common patterns
            m = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.mp4[^"\']*)["\']', content)
            if m:
                video_url = m.group(1)
                break
            
            m_hls = re.search(r'src\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', content)
            if m_hls:
                hls_url = m_hls.group(1)
                if not hls_url.startswith('http'):
                     hls_url = f"https://brazzpw.com/player/{hls_url}"
                break

    return {
        "url": url,
        "title": title,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": "BrazzPW",
        "video": {
            "streams": [{"quality": "720p", "url": video_url, "format": "mp4"}] if video_url else [],
            "hls": hls_url,
            "default": video_url or hls_url,
            "has_video": video_url is not None or hls_url is not None
        }
    }

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict]:
    # brazzpw pagination: /videos/page/2/
    url = base_url
    if page > 1:
        if base_url.endswith('/'):
            url = f"{base_url}page/{page}/"
        else:
            url = f"{base_url}/page/{page}/"
            
    html = await fetch_html(url)
    soup = BeautifulSoup(html, 'lxml')
    videos = []
    
    # Selectors for video cards based on research
    # brazzpw uses standard tube layout, often .item or .video-box
    cards = soup.select('div.item, div.video-box, .thumb-block')
    for card in cards:
        link_el = card.find('a')
        if not link_el: continue
        
        href = link_el.get('href')
        if not href or '/video/' not in href: continue
        
        img_el = card.find('img')
        thumb = img_el.get('data-src') or img_el.get('src') if img_el else ""
        
        title_el = card.find('p', class_='title') or card.find('span', class_='title') or link_el
        title = title_el.get_text(strip=True) if title_el else ""
        
        # Duration check
        duration = None
        dur_el = card.find(class_='duration')
        if dur_el:
            duration = dur_el.get_text(strip=True)

        videos.append({
            "url": f"https://brazzpw.com{href}" if href.startswith('/') else href,
            "title": title,
            "thumbnail_url": thumb,
            "duration": duration,
            "views": None,
            "uploader_name": "BrazzPW"
        })
        
        if len(videos) >= limit:
            break
            
    return videos
