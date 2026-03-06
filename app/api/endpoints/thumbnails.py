import httpx
from fastapi import APIRouter, HTTPException, Query, Response, Request
from fastapi.responses import StreamingResponse
from urllib.parse import quote
import logging
from typing import Optional

router = APIRouter()
logger = logging.getLogger(__name__)

# Shared client for connection pooling
_client_instance: Optional[httpx.AsyncClient] = None

async def get_proxy_client() -> httpx.AsyncClient:
    """Get or create the shared HTTPX client for proxying."""
    global _client_instance
    if _client_instance is None or _client_instance.is_closed:
        _client_instance = httpx.AsyncClient(
            follow_redirects=True, 
            timeout=15.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _client_instance

@router.get("/proxy", summary="Thumbnail Proxy")
async def thumbnail_proxy(
    url: str = Query(..., description="Target Thumbnail URL"),
    referer: str = Query(None, description="Referer header to send"),
    user_agent: str = Query(None, description="User-Agent header to send"),
    request: Request = None
):
    """
    Proxy thumbnail images to bypass network or Referer restrictions.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")
    
    url_lower = url.lower()
    is_hqporner = "hqporner.com" in url_lower
    is_youporn = "ypncdn.com" in url_lower or "youporn.com" in url_lower
    is_pornhub = "phncdn.com" in url_lower or "pornhub.com" in url_lower
    is_redtube = "rdtcdn.com" in url_lower or "redtube.com" in url_lower
    is_tube8 = "t8cdn.com" in url_lower or "tube8.com" in url_lower
    
    if not (is_hqporner or is_youporn or is_pornhub or is_redtube or is_tube8):
        raise HTTPException(status_code=403, detail="Only allowed domains are supported")
        
    if (is_youporn or is_pornhub or is_redtube or is_tube8) and "/plain/" not in url_lower:
        raise HTTPException(status_code=403, detail="Only YouPorn/Pornhub/RedTube/Tube8 dynamic /plain/ previews are allowed via proxy")
    
    # Headers to send to upstream
    headers = {}
    
    # Forward User-Agent from request if available, or allow override via query
    ua = user_agent if user_agent else request.headers.get("user-agent")
    if ua:
        headers["User-Agent"] = ua
        
    if referer:
        headers["Referer"] = referer
    else:
        if is_hqporner:
            headers["Referer"] = "https://hqporner.com/"
        elif is_youporn:
            headers["Referer"] = "https://www.youporn.com/"
        elif is_pornhub:
            headers["Referer"] = "https://www.pornhub.com/"
        elif is_redtube:
            headers["Referer"] = "https://www.redtube.com/"
        elif is_tube8:
            headers["Referer"] = "https://www.tube8.com/"

    try:
        # Use shared pooled client to prevent resource exhaustion (500 errors during bursts)
        client = await get_proxy_client()
        resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            logger.warning(f"Thumbnail proxy upstream error {resp.status_code} for {url}")
            raise HTTPException(status_code=resp.status_code, detail=f"Upstream returned {resp.status_code}")
        
        content_type = resp.headers.get("content-type", "image/jpeg")
        
        # Forward the image content
        return Response(
            content=resp.content,
            media_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=86400",  # 24 hour cache
                "X-Proxy-Origin": "AppHub-Thumbnail-Proxy"
            }
        )
                
    except httpx.TimeoutException:
        logger.warning(f"Thumbnail Proxy timeout for {url}")
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.RequestError as e:
        logger.error(f"Thumbnail Proxy request error for {url}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to connect to upstream: {str(e)}")
    except Exception as e:
        logger.error(f"Thumbnail Proxy unexpected error for {url}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error in thumbnail proxy")

def wrap_thumbnail_url(url: str, api_base_url: str) -> str:
    """Helper to wrap specific thumbnails in the proxy URL."""
    if not url:
        return url
        
    url_lower = url.lower()
    is_hqporner = "hqporner.com" in url_lower
    is_youporn = "ypncdn.com" in url_lower or "youporn.com" in url_lower
    is_pornhub = "phncdn.com" in url_lower or "pornhub.com" in url_lower
    is_redtube = "rdtcdn.com" in url_lower or "redtube.com" in url_lower
    is_tube8 = "t8cdn.com" in url_lower or "tube8.com" in url_lower
    
    if not (is_hqporner or is_youporn or is_pornhub or is_redtube or is_tube8):
        return url
        
    if is_youporn or is_pornhub or is_redtube or is_tube8:
        # Only proxy dynamic previews (which contain /plain/ and require IP-bound validto tokens)
        # Leave standard static .jpg thumbnails unproxied to save backend bandwidth
        if "/plain/" not in url_lower:
            return url
    
    # If already proxied, don't double wrap
    if "/thumbnails/proxy?url=" in url_lower:
        return url
        
    return f"{api_base_url.rstrip('/')}/api/v1/thumbnails/proxy?url={quote(url)}"
