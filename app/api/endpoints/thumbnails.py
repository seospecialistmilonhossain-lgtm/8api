from fastapi import APIRouter, HTTPException, Query, Response, Request
from urllib.parse import quote, unquote, urlparse
import logging
import re

import httpx

router = APIRouter()
logger = logging.getLogger(__name__)

# Match our thumbnail proxy path so we never fetch ourselves (avoids infinite HTTP recursion).
_PROXY_PATH_RE = re.compile(r"/thumbnails/proxy(?:\?|$|/)", re.IGNORECASE)
_PROXY_PATH_ENCODED_RE = re.compile(r"thumbnails%2fproxy(?:%3f|\?|$)", re.IGNORECASE)


def _targets_thumbnail_proxy_endpoint(target_url: str) -> bool:
    """
    True if the URL is (or repeatedly URL-decodes to) this app's thumbnail proxy.
    Prevents httpx from calling back into the same endpoint and looping.
    """
    if not target_url or not target_url.strip():
        return False
    current = target_url.strip()
    seen: set[str] = set()
    for _ in range(8):
        if current in seen:
            break
        seen.add(current)
        low = current.lower()
        if _PROXY_PATH_RE.search(low):
            return True
        if _PROXY_PATH_ENCODED_RE.search(low.replace("%2F", "%2f")):
            return True
        try:
            parsed = urlparse(current)
            path = (parsed.path or "").lower()
            if path.rstrip("/").endswith("/thumbnails/proxy"):
                return True
        except Exception:
            pass
        try:
            nxt = unquote(current)
        except Exception:
            break
        if nxt == current:
            break
        current = nxt
    return False


def _is_already_wrapped_thumbnail_url(url: str) -> bool:
    """Avoid nesting /api/v1/thumbnails/proxy?url=... inside another proxy query."""
    if not url:
        return False
    return _targets_thumbnail_proxy_endpoint(url)

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

    if _targets_thumbnail_proxy_endpoint(url):
        logger.warning("Thumbnail proxy refused recursive/self target url=%r", url[:500])
        raise HTTPException(
            status_code=400,
            detail="Refusing to proxy a thumbnail-proxy URL (would loop)",
        )

    url_lower = url.lower()
    is_hqporner = "hqporner.com" in url_lower
    is_youporn = any(x in url_lower for x in ["ypncdn.com", "youporn.com"])
    is_pornhub = any(x in url_lower for x in ["phncdn.com", "pornhub.com"])
    is_redtube = any(x in url_lower for x in ["rdtcdn.com", "redtube.com"])
    is_tube8 = any(x in url_lower for x in ["t8cdn.com", "tube8.com"])
    is_hanime = any(x in url_lower for x in ["hanime.tv", "hb00.io", "hanime-cdn.com", "hb01.io", "hb02.io"])

    if not (is_hqporner or is_youporn or is_pornhub or is_redtube or is_tube8 or is_hanime):
        raise HTTPException(status_code=403, detail="Only allowed domains are supported")
        
    if (is_youporn or is_pornhub or is_redtube or is_tube8) and "/plain/" not in url_lower:
        raise HTTPException(status_code=403, detail="Only YouPorn/Pornhub/RedTube/Tube8 dynamic /plain/ previews are allowed via proxy")
    
    # Build request headers
    headers = {}
    ua = user_agent if user_agent else (request.headers.get("user-agent") if request else None)
    if ua:
        headers["User-Agent"] = ua
    else:
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
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
        elif is_hanime:
            headers["Referer"] = "https://hanime.tv/"

    try:
        # Per-request client: avoids binding a pooled session to the wrong event loop
        # (see pool.py / a2wsgi); httpx matches other scrapers and resolves cleanly in the IDE.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
            max_redirects=8,
        ) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    f"Thumbnail proxy upstream error {resp.status_code} for {url}"
                )
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Upstream returned {resp.status_code}",
                )
            content = resp.content
            content_type = resp.headers.get("content-type", "image/jpeg")

            return Response(
                content=content,
                media_type=content_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "public, max-age=86400",
                    "X-Proxy-Origin": "AppHub-Thumbnail-Proxy",
                },
            )

    except HTTPException:
        raise
    except httpx.TimeoutException as e:
        logger.warning(f"Thumbnail Proxy timeout for {url}: {e}")
        raise HTTPException(status_code=504, detail="Upstream timeout") from e
    except httpx.RequestError as e:
        logger.error(f"Thumbnail Proxy request error for {url}: {e}")
        raise HTTPException(status_code=502, detail="Upstream connection error") from e
    except Exception as e:
        logger.error(f"Thumbnail Proxy unexpected error for {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

def wrap_thumbnail_url(url: str, api_base_url: str) -> str:
    """Helper to wrap specific thumbnails in the proxy URL."""
    if not url:
        return url

    if _is_already_wrapped_thumbnail_url(url):
        return url

    url_lower = url.lower()
    is_hqporner = "hqporner.com" in url_lower
    is_youporn = any(x in url_lower for x in ["ypncdn.com", "youporn.com"])
    is_pornhub = any(x in url_lower for x in ["phncdn.com", "pornhub.com"])
    is_redtube = any(x in url_lower for x in ["rdtcdn.com", "redtube.com"])
    is_tube8 = any(x in url_lower for x in ["t8cdn.com", "tube8.com"])
    is_hanime = any(x in url_lower for x in ["hanime.tv", "hb00.io", "hanime-cdn.com", "hb01.io", "hb02.io"])

    if not (is_hqporner or is_youporn or is_pornhub or is_redtube or is_tube8 or is_hanime):
        return url
        
    if is_youporn or is_pornhub or is_redtube or is_tube8:
        # Only proxy dynamic previews (which contain /plain/ and require IP-bound validto tokens)
        # Leave standard static .jpg thumbnails unproxied to save backend bandwidth
        if "/plain/" not in url_lower:
            return url

    return f"{api_base_url.rstrip('/')}/api/v1/thumbnails/proxy?url={quote(url)}"
