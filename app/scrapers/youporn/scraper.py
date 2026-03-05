from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

def can_handle(host: str) -> bool:
    return "youporn.com" in host.lower()

def _best_image_url(img: Any) -> Optional[str]:
    """Extract the best image URL from an img element, checking multiple lazy-load attributes."""
    if img is None:
        return None
    
    # Check common lazy-loading attributes in order of preference
    # Youporn uses: data-poster (primary image thumbnail)
    video_fallback = None  # Store first video URL as fallback
    
    for attr in ("data-poster", "data-src", "data-original", "data-lazy", "data-image", "src", "data-mediabook"):
        value = img.get(attr)
        if not value:
            continue
        url = str(value).strip()
        if not url or "data:image" in url:
            continue
        
        # Check if it's a video file
        url_lower = url.lower()
        is_video = any(url_lower.endswith(ext) or f'{ext}/' in url_lower or f'{ext}?' in url_lower 
                      for ext in ('.mp4', '.webm', '.m3u8', '.ts'))
        
        if is_video:
            # Save as fallback but keep looking for image
            if not video_fallback:
                video_fallback = url
            continue
        
        # Found a valid image URL!
        return url
    
    # If no image URL found, use video as fallback (better than null)
    return video_fallback

def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

async def fetch_html(url: str) -> str:
    from app.core import pool
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        # Mobile cookie might be needed? Usually desktop is safer for parsing.
        # YouPorn might not strict check 'platform=pc' but good practice if structure varies
    }
    resp = await pool.client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text

async def _resolve_proxy_url(proxy_url: str) -> list[dict]:
    """
    Resolve a YouPorn proxy URL (e.g., /media/mp4/?s=...) to actual CDN streams.
    Returns a list of stream objects with quality, url, format.
    """
    try:
        from app.core import pool
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        resp = await pool.client.get(proxy_url, headers=headers)
        if resp.status_code != 200:
            return []
            
        data = resp.json()
        if isinstance(data, list):
            streams = []
            for item in data:
                quality = item.get("quality")
                video_url = item.get("videoUrl")
                fmt = item.get("format", "mp4")
                
                if video_url:
                    # Convert quality to string
                    if isinstance(quality, int):
                        quality = str(quality)
                    
                    streams.append({
                        "quality": quality if quality else "unknown",
                        "url": video_url,
                        "format": fmt
                    })
            return streams
    except Exception:
        pass
    
    return []

def _extract_video_streams(html: str) -> dict[str, Any]:
    streams = []
    hls_url = None
    
    # Find mediaDefinitions in HTML and extract the array efficiently
    media_defs = []
    idx = html.find('mediaDefinitions')
    if idx != -1:
        # Find the opening bracket after mediaDefinitions
        chunk_start = idx
        chunk = html[chunk_start:chunk_start + 10000]  # Limit search space
        array_start_idx = chunk.find('[')
        
        if array_start_idx != -1:
            # Count brackets to find the matching closing bracket
            depth = 0
            array_content = None
            for i, char in enumerate(chunk[array_start_idx:], array_start_idx):
                if char == '[':
                    depth += 1
                elif char == ']':
                    depth -= 1
                    if depth == 0:
                        array_content = chunk[array_start_idx:i + 1]
                        break
            
            if array_content:
                try:
                    media_defs = json.loads(array_content)
                except Exception:
                    pass
    
    # Process mediaDefinitions
    if media_defs:
        for md in media_defs:
            video_url = md.get("videoUrl")
            if not video_url: continue
            
            # Skip poster/thumbnail images
            if video_url.endswith('.jpg') or video_url.endswith('.jpeg') or video_url.endswith('.png'):
                continue
            
            fmt = md.get("format") # mp4, hls
            quality = md.get("quality") # 720p, 1080p, etc
            
            if isinstance(quality, list): quality = str(quality[0])
            
            # Check format
            if fmt == "hls" or ".m3u8" in video_url:
                # Extract quality from URL or metadata
                parsed_quality = "adaptive"
                
                if quality:
                    if isinstance(quality, str) and quality.isdigit():
                         parsed_quality = f"{quality}p"
                    else:
                         parsed_quality = str(quality)
                
                # Try regex on URL if quality is not specific
                if not parsed_quality or parsed_quality == "adaptive":
                    # Try finding /1080P/ or similar patterns
                    m_q = re.search(r'/(\d{3,4})[pP]?/', video_url)
                    if not m_q:
                         m_q = re.search(r'(\d{3,4})[pP]_', video_url)
                         
                    if m_q:
                        parsed_quality = f"{m_q.group(1)}p"
                
                hls_url = video_url
                streams.append({
                    "quality": parsed_quality,
                    "url": video_url,
                    "format": "hls"
                })
            elif fmt == "mp4" or ".mp4" in video_url:
                 streams.append({
                    "quality": str(quality) if quality else "unknown",
                    "url": video_url,
                    "format": "mp4"
                })

    # Generic fallback if no JSON found: check for <video> sources
    if not streams:
        soup = BeautifulSoup(html, "lxml")
        video = soup.find("video")
        if video:
            src = video.get("src")
            # Skip poster images in video src attribute
            if src and not (src.endswith('.jpg') or src.endswith('.jpeg') or src.endswith('.png')):
                 streams.append({"quality": "unknown", "url": src, "format": "mp4"})
            for source in video.find_all("source"):
                src = source.get("src")
                type_ = source.get("type", "")
                # Skip poster images
                if src and not (src.endswith('.jpg') or src.endswith('.jpeg') or src.endswith('.png')):
                    fmt = "hls" if "mpegurl" in type_ or ".m3u8" in src else "mp4"
                    if fmt == "hls":
                        hls_url = src
                    streams.append({"quality": "unknown", "url": src, "format": fmt})

    # Determine default
    default_url = None
    if hls_url:
        default_url = hls_url
    elif streams:
        default_url = streams[0]["url"]

    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    
    # Title
    title = None
    meta_title = soup.find("meta", property="og:title")
    if meta_title: title = meta_title.get("content")
    if not title:
        t_tag = soup.find("title")
        if t_tag: title = t_tag.get_text(strip=True)
    
    if title:
        title = title.replace(" - YouPorn", "").replace(" | YouPorn", "")

    # Thumbnail
    thumbnail = None
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb: thumbnail = meta_thumb.get("content")

    # Duration
    duration = None
    meta_dur = soup.find("meta", property="video:duration")
    if meta_dur:
        try:
            secs = int(meta_dur.get("content"))
            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            if h > 0:
                duration = f"{h}:{m:02d}:{s:02d}"
            else:
                duration = f"{m}:{s:02d}"
        except Exception:
            pass
            
    # Views
    views = None
    # Usually in a div with class "video-infos" or similar
    # Look for explicit structure or regex
    # "x,xxx,xxx Views"
    text_blob = soup.get_text(" ", strip=True)
    m_views = re.search(r'([\d,]+)\s+views', text_blob, re.IGNORECASE)
    if m_views:
        views = m_views.group(1)

    # Uploader
    uploader = None
    # Look for "Uploaded by X" or class "submitter"
    submitter_div = soup.select_one(".submitter, .video-uploaded-by, .uploader-name")
    if submitter_div:
        uploader = submitter_div.get_text(strip=True).replace("Uploaded by:", "").strip()

    # Tags
    tags = []
    # common tag container
    for t in soup.select(".categories-wrapper a, .tags-wrapper a, .video-tags a"):
        txt = t.get_text(strip=True)
        if txt: tags.append(txt)
    
    # Streams
    video_data = _extract_video_streams(html)

    return {
        "url": url,
        "title": title,
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": "YouPorn",
        "tags": tags,
        "video": video_data,
        "related_videos": [], 
        "preview_url": None 
    }

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    result = parse_page(html, url)
    
    # Check if we have proxy URLs and resolve them to real CDN streams
    video_data = result.get("video", {})
    streams = video_data.get("streams", [])
    
    # If any stream is a proxy URL, try to resolve it
    for stream in streams[:]:  # Copy list to modify while iterating
        stream_url = stream.get("url", "")
        if "/media/" in stream_url and "?s=" in stream_url:
            # This is a proxy URL - resolve it
            resolved_streams = await _resolve_proxy_url(stream_url)
            if resolved_streams:
                # Remove the proxy stream
                streams.remove(stream)
                # Add all resolved streams
                streams.extend(resolved_streams)
    
    # Update default URL based on resolved streams
    if streams:
        # Find HLS adaptive stream or highest quality MP4
        hls_stream = next((s for s in streams if s.get("format") == "hls"), None)
        if hls_stream:
            video_data["default"] = hls_stream["url"]
        else:
            # Find highest quality MP4
            mp4_streams = [s for s in streams if s.get("format") == "mp4"]
            if mp4_streams:
                # Sort by quality (try to get 1080, 720, etc.)
                qualities = {"1080": 4, "720": 3, "480": 2, "240": 1}
                mp4_streams.sort(key=lambda s: qualities.get(s.get("quality", ""), 0), reverse=True)
                video_data["default"] = mp4_streams[0]["url"]
    
    return result

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # YouPorn pagination: homepage?page=2 or /category/asian?page=2
    # Unlike Pornhub, YouPorn doesn't have a /video endpoint
    
    url = base_url.rstrip("/")
    
    if "?" in url:
        url += f"&page={page}"
    else:
        url += f"?page={page}"

    try:
        html = await fetch_html(url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items = []
    
    # YouPorn listing: div.video-box or similar
    # Selector: .video-box
    for box in soup.select(".video-box"):
        try:
            link = box.select_one("a")
            if not link:
                continue
                
            href = link.get("href")
            if not href:
                continue
              
            # Make URL absolute
            if not href.startswith("http"):
                href = "https://www.youporn.com" + href
                
            # Thumbnail - REQUIRED (skip if missing)
            img = link.select_one("img")
            thumb = _best_image_url(img)
            if not thumb:
                # Try to find img anywhere in the box as fallback
                img = box.select_one("img")
                thumb = _best_image_url(img)
            
            # Skip if no valid thumbnail found
            if not thumb:
                continue
            
            # Title - try multiple sources
            title = None
            title_div = box.select_one(".video-title, .tm_video_title, .video-box-title")
            if title_div:
                title = title_div.get_text(strip=True)
            if not title:
                title = link.get("title")
            if not title and img:
                title = img.get("alt")
            
            # Duration
            duration = None
            dur_div = box.select_one(".duration, .tm_video_duration, .video-duration")
            if dur_div:
                duration = dur_div.get_text(strip=True)
            
            # Views
            views = None
            # Info usually in .video-infos -> "X views"
            infos = box.select_one(".video-infos, .video-views, .tm_video_views, .info-views")
            if infos:
                info_txt = infos.get_text(strip=True)
                # Try simple number match if "views" text is missing
                if info_txt and (info_txt.replace(',', '').replace('.', '').replace('K', '').replace('M', '').isdigit() or 
                                 re.match(r'^[\d,KM\.]+$', info_txt)):
                     views = info_txt
                else:
                    m = re.search(r'([\d,KM\.]+)\s*views', info_txt, re.IGNORECASE)
                    if m:
                        views = m.group(1)
            
            if not views:
                # Fallback: Search in entire box text
                box_text = box.get_text()
                m = re.search(r'([\d,KM\.]+)\s*views', box_text, re.IGNORECASE)
                if m:
                    views = m.group(1)
            
            # Uploader
            uploader = "YouPorn"  # Default
            submitter = box.select_one(".submitter, .video-uploaded-by, .author-title-text, .owner-name")
            if submitter:
                uploader_text = submitter.get_text(strip=True)
                if uploader_text:
                    uploader = uploader_text.replace("Uploaded by:", "").strip()

            items.append({
                "url": href,
                "title": title or "Unknown",
                "thumbnail_url": thumb,
                "duration": duration or "0:00",
                "views": views or "0",
                "uploader_name": uploader,
            })
        except Exception as e:
            # Log the error but continue processing other videos
            import logging
            logging.debug(f"Failed to extract Youporn video from box: {e}")
            continue
            
            
    return items
