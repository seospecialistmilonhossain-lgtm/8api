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

    async def _extract_streams_from_embed(embed_url: str) -> list:
        """Fetch the embed page and pull streams from <source> and regex."""
        s_list = []
        try:
            e_html = await fetch_html(embed_url)
            e_soup = BeautifulSoup(e_html, "lxml")
            # Priority: <source> tags inside <video>
            for src_tag in e_soup.select("video source"):
                src = src_tag.get("src", "")
                if not src: continue
                quality = src_tag.get("title") or src_tag.get("label") or "default"
                fmt = "hls" if ".m3u8" in src else "mp4"
                s_list.append({"quality": quality, "url": src, "format": fmt})
            if not s_list:
                # Fallback regex in embed HTML
                for lnk in re.findall(r'(https?://[^\s\'"]+\.m3u8[^\s\'"]*)', e_html):
                    s_list.append({"quality": "default", "url": lnk.replace("\\/", "/"), "format": "hls"})
                for lnk in re.findall(r'(https?://[^\s\'"]+\.mp4[^\s\'"]*)', e_html):
                    s_list.append({"quality": "default", "url": lnk.replace("\\/", "/"), "format": "mp4"})
        except Exception as ex:
            print(f"⚠️ Embed fetch failed: {ex}")
        return s_list

    async def _get_streams_for_episode(ep_url: str) -> list:
        """Fetch episode page, find iframe embed, return streams."""
        try:
            ep_html = await fetch_html(ep_url)
            ep_soup = BeautifulSoup(ep_html, "lxml")
            iframe = ep_soup.find("iframe")
            if iframe:
                iframe_src = iframe.get("src", "")
                if iframe_src:
                    if iframe_src.startswith("/"): iframe_src = "https://haho.moe" + iframe_src
                    return await _extract_streams_from_embed(iframe_src)
            # Fallback: regex on episode HTML itself
            s_list = []
            for lnk in re.findall(r'(https?://[^\s\'"]+\.m3u8[^\s\'"]*)', ep_html):
                s_list.append({"quality": "default", "url": lnk.replace("\\/", "/"), "format": "hls"})
            for lnk in re.findall(r'(https?://[^\s\'"]+\.mp4[^\s\'"]*)', ep_html):
                s_list.append({"quality": "default", "url": lnk.replace("\\/", "/"), "format": "mp4"})
            return s_list
        except Exception as ex:
            print(f"⚠️ Episode fetch failed: {ex}")
            return []

    embed_url = None
    if is_series and not is_episode:
        # Series page: find first episode via a.film-grain cards
        first_ep = soup.select_one("a.film-grain")
        if first_ep:
            ep_url = first_ep.get("href", "")
            if ep_url:
                if ep_url.startswith("/"): ep_url = "https://haho.moe" + ep_url
                try:
                    ep_html = await fetch_html(ep_url)
                    ep_soup = BeautifulSoup(ep_html, "lxml")
                    iframe = ep_soup.find("iframe")
                    if iframe:
                        embed_url = iframe.get("src", "")
                        if embed_url and embed_url.startswith("/"): 
                            embed_url = "https://haho.moe" + embed_url
                except Exception:
                    pass
    elif is_episode:
        iframe = soup.find("iframe")
        if iframe:
            embed_url = iframe.get("src", "")
            if embed_url and embed_url.startswith("/"): 
                embed_url = "https://haho.moe" + embed_url

    if embed_url:
        # 1. Add embed as a stream
        streams.append({
            "quality": "Embed Player",
            "url": embed_url,
            "format": "embed"
        })
        # 2. Extract direct links from embed
        direct_streams = await _extract_streams_from_embed(embed_url)
        streams.extend(direct_streams)
        # 3. Use embed as default
        default_url = embed_url

    if not streams:
        # Last resort: regex on this page
        for lnk in re.findall(r'(https?://[^\s\'"]+\.m3u8[^\s\'"]*)', html):
            streams.append({"quality": "default", "url": lnk.replace("\\/", "/"), "format": "hls"})
        for lnk in re.findall(r'(https?://[^\s\'"]+\.mp4[^\s\'"]*)', html):
            streams.append({"quality": "default", "url": lnk.replace("\\/", "/"), "format": "mp4"})

    if streams and not default_url:
        default_url = streams[0]["url"]

    video_data = {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

    # Extract Related Videos / Episodes
    related_videos = []
    
    # Check if the URL points to a specific episode (e.g. /anime/mx1d9guh/1)
    is_episode_url = bool(re.search(r"https?://haho\.moe/anime/[^/]+/[^/]+", url))
    
    if is_episode or is_episode_url:
        # If we are on an episode page, the episode list might not be full or structured the same way.
        # Fetch the main series page to reliably get all related episodes.
        base_match = re.search(r"(https?://haho\.moe/anime/[^/]+)", url)
        if base_match:
            series_url = base_match.group(1)
            try:
                series_html = await fetch_html(series_url)
                if series_html:
                    soup = BeautifulSoup(series_html, "lxml")
                    is_series = True
            except Exception:
                pass

    if is_series:
        # If it's a series page (or we just loaded the series page), find all episodes in the "Episodes" section
        for ep_a in soup.select('a.film-grain'):
            try:
                ep_href = ep_a.get("href", "")
                if not ep_href: continue
                if ep_href.startswith("/"): ep_href = "https://haho.moe" + ep_href
                
                # Title: Series + Episode
                t_series = ep_a.select_one(".overlay .title")
                t_ep = ep_a.select_one(".overlay .episode-title")
                ep_slug = ep_a.select_one(".episode-slug")
                
                ep_title_parts = []
                if t_series: 
                    t_text = t_series.get_text(strip=True)
                    if t_text and t_text.lower() != "no title":
                        ep_title_parts.append(t_text)
                
                if t_ep:
                    ep_text = t_ep.get_text(strip=True)
                    if ep_text and ep_text.lower() != "no title":
                        ep_title_parts.append(ep_text)
                        
                if not ep_title_parts and ep_slug:
                    slug_text = ep_slug.get_text(strip=True)
                    if slug_text:
                        ep_title_parts.append(slug_text)

                ep_title = " - ".join(ep_title_parts) or "Episode"
                
                # Thumbnail
                ep_img = ep_a.select_one("img.image")
                ep_thumb = None
                if ep_img:
                    ep_thumb = ep_img.get("src") or ep_img.get("data-src") or ep_img.get("data-original")
                    if ep_thumb:
                        if ep_thumb.startswith("//"): ep_thumb = "https:" + ep_thumb
                        elif ep_thumb.startswith("/") and not ep_thumb.startswith("//"): ep_thumb = "https://haho.moe" + ep_thumb
                
                # Views
                ep_views = "0"
                v_el = ep_a.select_one(".top-overlay.views")
                if v_el:
                    ep_views = (v_el.get("title") or "").replace(",", "") or v_el.get_text(strip=True).upper().replace("VIEWS", "").strip()

                related_videos.append({
                    "url": ep_href,
                    "title": ep_title.strip(),
                    "thumbnail_url": ep_thumb,
                    "views": ep_views,
                    "uploader_name": "Haho"
                })
            except Exception: continue

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": views,
        "uploader_name": uploader,
        "category": "Anime",
        "tags": tags if tags else None,
        "upload_date": upload_date,
        "video": video_data,
        "related_videos": related_videos
    }

async def get_episode_list(series_id: str) -> list[dict[str, Any]]:
    """
    Lightweight function to fetch ONLY the episode list for a Haho series.
    Used by the /videos/related endpoint to avoid a full double-page scrape.
    Fetches only the base series page and extracts all a.film-grain episode cards.
    """
    series_url = f"https://haho.moe/anime/{series_id}"
    try:
        html = await fetch_html(series_url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    episodes = []

    for ep_a in soup.select('a.film-grain'):
        try:
            ep_href = ep_a.get("href", "")
            if not ep_href:
                continue
            if ep_href.startswith("/"):
                ep_href = "https://haho.moe" + ep_href

            # Title
            t_series = ep_a.select_one(".overlay .title")
            t_ep = ep_a.select_one(".overlay .episode-title")
            ep_slug = ep_a.select_one(".episode-slug")

            ep_title_parts = []
            if t_series:
                t_text = t_series.get_text(strip=True)
                if t_text and t_text.lower() != "no title":
                    ep_title_parts.append(t_text)
            if t_ep:
                ep_text = t_ep.get_text(strip=True)
                if ep_text and ep_text.lower() != "no title":
                    ep_title_parts.append(ep_text)
            if not ep_title_parts and ep_slug:
                slug_text = ep_slug.get_text(strip=True)
                if slug_text:
                    ep_title_parts.append(slug_text)

            ep_title = " - ".join(ep_title_parts) or "Episode"

            # Thumbnail
            ep_img = ep_a.select_one("img.image")
            ep_thumb = None
            if ep_img:
                ep_thumb = ep_img.get("src") or ep_img.get("data-src") or ep_img.get("data-original")
                if ep_thumb:
                    if ep_thumb.startswith("//"):
                        ep_thumb = "https:" + ep_thumb
                    elif ep_thumb.startswith("/") and not ep_thumb.startswith("//"):
                        ep_thumb = "https://haho.moe" + ep_thumb

            # Views
            ep_views = "0"
            v_el = ep_a.select_one(".top-overlay.views")
            if v_el:
                ep_views = (v_el.get("title") or "").replace(",", "") or v_el.get_text(strip=True).upper().replace("VIEWS", "").strip()

            episodes.append({
                "url": ep_href,
                "title": ep_title.strip(),
                "thumbnail_url": ep_thumb,
                "views": ep_views,
                "uploader_name": "Haho"
            })
        except Exception:
            continue

    return episodes


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    url = base_url
    if page > 1:
        if "page=" in base_url:
            url = re.sub(r'([?&])page=\d+', fr'\1page={page}', base_url)
        else:
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
            views_el = item.select_one(".top-overlay.views")
            if views_el:
                # Priority 1: Exact count from title attribute (clean commas)
                exact_views = (views_el.get("title") or "").replace(",", "")
                if exact_views and exact_views.isdigit():
                    views = exact_views
                else:
                    # Priority 2: Keep formatted text (e.g., 47.9K, 154K, 1.2M)
                    v_text = views_el.get_text(strip=True).upper().replace("VIEW", "").replace("S", "").replace(",", "").strip()
                    views = v_text
            
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
                    # Priority 1: Exact count from title attribute (clean commas)
                    exact_views = (view_tag.get("title") or "").replace(",", "")
                    if exact_views and exact_views.isdigit():
                        views = exact_views
                    else:
                        # Priority 2: Keep formatted text (e.g., 47.9K, 154K, 1.2M)
                        v_text = view_tag.get_text(strip=True).upper().replace("VIEW", "").replace("S", "").replace(",", "").strip()
                        views = v_text
                
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
                
                view_tag = item.select_one("i.fa-eye")
                if view_tag and view_tag.parent:
                    # Priority 1: Exact count from title attribute (clean commas)
                    exact_views = (view_tag.parent.get("title") or "").replace(",", "")
                    if exact_views and exact_views.isdigit():
                        views = exact_views
                    else:
                        # Priority 2: Keep formatted text (e.g., 47.9K, 154K, 1.2M)
                        v_text = view_tag.parent.get_text(strip=True).upper().replace("VIEW", "").replace("S", "").replace(",", "").strip()
                        views = v_text
                        
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
