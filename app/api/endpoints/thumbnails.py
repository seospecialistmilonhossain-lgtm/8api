from fastapi import APIRouter, HTTPException, Query, Response, Request
from urllib.parse import quote
import logging
import asyncio
from app.core.pool import pool

router = APIRouter()
logger = logging.getLogger(__name__)

# Global pool is used for connection pooling
# No need for local AsyncSession anymore

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
    is_youporn = any(x in url_lower for x in ["ypncdn.com", "youporn.com"])
    is_pornhub = any(x in url_lower for x in ["phncdn.com", "pornhub.com"])
    is_redtube = any(x in url_lower for x in ["rdtcdn.com", "redtube.com"])
    is_tube8 = any(x in url_lower for x in ["t8cdn.com", "tube8.com"])
    
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
        session = await pool.get_session()
        
        async with session.get(url, headers=headers, timeout=15.0) as resp:
            if resp.status >= 400:
                logger.warning(f"Thumbnail proxy upstream error {resp.status} for {url}")
                raise HTTPException(status_code=resp.status, detail=f"Upstream returned {resp.status}")
            
            content = await resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            
            # Forward the image content
            return Response(
                content=content,
                media_type=content_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "public, max-age=86400",  # 24 hour cache
                    "X-Proxy-Origin": "AppHub-Thumbnail-Proxy"
                }
            )
                
    except Exception as e:
        # Check specifically for curl_cffi errors if possible, or generic catch-all
        err_str = str(e).lower()
        if "timeout" in err_str:
            logger.warning(f"Thumbnail Proxy timeout for {url}: {e}")
            raise HTTPException(status_code=504, detail="Upstream timeout")
        
        logger.error(f"Thumbnail Proxy unexpected error for {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def wrap_thumbnail_url(url: str, api_base_url: str) -> str:
    """Helper to wrap specific thumbnails in the proxy URL."""
    if not url:
        return url
        
    url_lower = url.lower()
    is_hqporner = "hqporner.com" in url_lower
    is_youporn = any(x in url_lower for x in ["ypncdn.com", "youporn.com"])
    is_pornhub = any(x in url_lower for x in ["phncdn.com", "pornhub.com"])
    is_redtube = any(x in url_lower for x in ["rdtcdn.com", "redtube.com"])
    is_tube8 = any(x in url_lower for x in ["t8cdn.com", "tube8.com"])
    
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
