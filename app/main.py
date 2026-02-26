from __future__ import annotations

import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

# Logging
import logging

# Config
from app.config.settings import settings

# Core Modules
from app.core import cache, cache_cleanup, pool, rate_limit_middleware, rate_limit_cleanup

# Exception handlers
from app.exception_handlers import not_found_handler, internal_error_handler, general_exception_handler

# API Routers
from app.api.endpoints import recommendations, hls, media
# We will define new standardized routers here or import them if we moved them.
# For this refactor, we will define them inline or in a new api module. 
# To keep it clean, I will implement the Router structure within main.py for now, 
# ensuring they obey the /api/v1/ prefix.

from fastapi import APIRouter

# Scrapers & Models
from app.scrapers import masa49, xhamster, xnxx, xvideos, pornhub, youporn, redtube, beeg, spankbang, fapnut
from app.models.schemas import ScrapeResponse, ListItem, CategoryItem, ScrapeRequest, ListRequest

logging.basicConfig(level=logging.INFO)

# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    # Startup
    asyncio.create_task(cache_cleanup())
    asyncio.create_task(rate_limit_cleanup())
    logging.info("✅ Started background cleanup tasks")
    logging.info("✅ Zero-cost optimizations enabled")
    
    yield
    
    # Shutdown
    await pool.close()
    logging.info("✅ Closed HTTP connection pool")

# Create FastAPI app
app = FastAPI(
    title="AppHub API",
    description="Professional Standard API with Versioning, Plural Naming, and Queue Services",
    version="2.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Register exception handlers
app.add_exception_handler(404, not_found_handler)
app.add_exception_handler(500, internal_error_handler)
app.add_exception_handler(StarletteHTTPException, general_exception_handler)
app.add_exception_handler(HTTPException, general_exception_handler)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)

app.middleware("http")(rate_limit_middleware)

# ==============================================================================
# API V1 Router
# ==============================================================================
api_v1_router = APIRouter(prefix="/api/v1")

# --- Scraper / Resources Endpoints ---

from pydantic import BaseModel, HttpUrl, field_validator, Field
from typing import Any, Optional
import asyncio

class ScrapeRequestV1(BaseModel):
    url: HttpUrl

class CrawlRequestV1(BaseModel):
    base_url: HttpUrl
    start_page: int = Field(1, ge=1)
    max_pages: int = Field(5, ge=1, le=20)
    per_page_limit: int = Field(0, ge=0, le=200)
    max_items: int = Field(500, ge=1, le=1000)

# Import loose dispatch functions (re-using existing ones for now)
# Ideally these should be in services/scraper_service.py
async def _scrape_dispatch(url: str, host: str) -> dict[str, object]:
    if xhamster.can_handle(host): return await xhamster.scrape(url)
    if masa49.can_handle(host): return await masa49.scrape(url)
    if xnxx.can_handle(host): return await xnxx.scrape(url)
    if xvideos.can_handle(host): return await xvideos.scrape(url)
    if pornhub.can_handle(host): return await pornhub.scrape(url)
    if youporn.can_handle(host): return await youporn.scrape(url)
    if redtube.can_handle(host): return await redtube.scrape(url)
    if beeg.can_handle(host): return await beeg.scrape(url)
    if spankbang.can_handle(host): return await spankbang.scrape(url)
    if fapnut.can_handle(host): return await fapnut.scrape(url)
    raise HTTPException(status_code=400, detail="Unsupported host")

async def _list_dispatch(base_url: str, host: str, page: int, limit: int) -> list[dict[str, object]]:
    if xhamster.can_handle(host): return await xhamster.list_videos(base_url=base_url, page=page, limit=limit)
    if masa49.can_handle(host): return await masa49.list_videos(base_url=base_url, page=page, limit=limit)
    if xnxx.can_handle(host): return await xnxx.list_videos(base_url=base_url, page=page, limit=limit)
    if xvideos.can_handle(host): return await xvideos.list_videos(base_url=base_url, page=page, limit=limit)
    if pornhub.can_handle(host): return await pornhub.list_videos(base_url=base_url, page=page, limit=limit)
    if youporn.can_handle(host): return await youporn.list_videos(base_url=base_url, page=page, limit=limit)
    if redtube.can_handle(host): return await redtube.list_videos(base_url=base_url, page=page, limit=limit)
    if beeg.can_handle(host): return await beeg.list_videos(base_url=base_url, page=page, limit=limit)
    if spankbang.can_handle(host): return await spankbang.list_videos(base_url=base_url, page=page, limit=limit)
    if fapnut.can_handle(host): return await fapnut.list_videos(base_url=base_url, page=page, limit=limit)
    raise HTTPException(status_code=400, detail="Unsupported host")

async def _crawl_dispatch(base_url: str, host: str, start_page: int, max_pages: int, per_page_limit: int, max_items: int) -> list[dict[str, object]]:
    if xhamster.can_handle(host):
        return await xhamster.crawl_videos(base_url=base_url, start_page=start_page, max_pages=max_pages, per_page_limit=per_page_limit, max_items=max_items)
    raise HTTPException(status_code=400, detail="Unsupported host")


@api_v1_router.post("/scrapes", response_model=ScrapeResponse, tags=["Scraping"])
async def create_scrape(body: ScrapeRequestV1) -> ScrapeResponse:
    """
    Scrape a single video URL.
    Renamed from /scrape to POST /scrapes (create a scrape).
    """
    try:
        data = await _scrape_dispatch(str(body.url), body.url.host or "")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e
    return ScrapeResponse(**data)

@api_v1_router.get("/videos", response_model=list[ListItem], response_model_exclude_unset=True, tags=["Videos"])
async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[ListItem]:
    """
    List videos from a category/channel URL.
    Renamed from /list to GET /videos.
    """
    if page < 1: page = 1
    if limit < 1: limit = 1
    if limit > 60: limit = 60
    
    # Check cache (v2 optimization)
    cache_key = f"list:{base_url}:p{page}:l{limit}"
    cached_items = await cache.get(cache_key)
    if cached_items:
        logging.info(f"⚡ Cache HIT for list {base_url} page {page}")
        return [ListItem(**it) for it in cached_items]

    host = ""
    try:
        parsed = HttpUrl(base_url)
        host = parsed.host or ""
    except:
        pass 

    try:
        items = await _list_dispatch(base_url, host, page, limit)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e
    
    if items:
        await cache.set(cache_key, items, ttl_seconds=900)
    
    return [ListItem(**it) for it in items]

@api_v1_router.post("/crawls", response_model=list[ListItem], tags=["Crawling"])
async def create_crawl(body: CrawlRequestV1) -> list[ListItem]:
    """
    Crawl a site for videos.
    Renamed from /crawl to POST /crawls.
    """
    try:
        items = await _crawl_dispatch(
            str(body.base_url),
            body.base_url.host or "",
            body.start_page,
            body.max_pages,
            body.per_page_limit,
            body.max_items,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e

    return [ListItem(**it) for it in items]

# --- Categories ---
# Aggregating categories into a cleaned up endpoint
# GET /api/v1/categories?source=xnxx
@api_v1_router.get("/categories", response_model=list[CategoryItem], tags=["Categories"])
async def get_categories(source: str) -> list[CategoryItem]:
    """
    Get categories for a specific source.
    """
    s = source.lower()
    try:
        if s == "xnxx": return [CategoryItem(**c) for c in xnxx.get_categories()]
        if s == "masa": return [CategoryItem(**c) for c in masa49.get_categories()]
        if s == "xvideos": return [CategoryItem(**c) for c in xvideos.get_categories()]
        if s == "xhamster": return [CategoryItem(**c) for c in xhamster.get_categories()]
        if s == "youporn": return [CategoryItem(**c) for c in youporn.get_categories()]
        if s == "pornhub": return [CategoryItem(**c) for c in pornhub.get_categories()]
        if s == "redtube": return [CategoryItem(**c) for c in redtube.get_categories()]
        if s == "beeg": return [CategoryItem(**c) for c in beeg.get_categories()]
        if s == "spankbang": return [CategoryItem(**c) for c in spankbang.get_categories()]
        if s == "onlyfans" or s == "fapnut": return [CategoryItem(**c) for c in await fapnut.get_categories()]
        raise HTTPException(status_code=400, detail="Unknown source")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")

# --- Global Search & Trending (Pro Features) ---
from app.services.global_search import global_search as _global_search, global_trending
from fastapi import Query

@api_v1_router.get("/search/global", tags=["Search"])
async def global_search_endpoint(
    query: str = Query(..., description="Search keyword"),
    sites: Optional[list[str]] = Query(None, description="Sites to search"),
    limit_per_site: int = Query(10, ge=1, le=50),
    max_sites: int = Query(30, ge=1, le=50)
):
    return await _global_search(query, sites, limit_per_site, max_sites)

@api_v1_router.get("/trending/global", tags=["Trending"])
async def global_trending_endpoint(
    sites: Optional[list[str]] = Query(None),
    limit_per_site: int = Query(10, ge=1, le=50)
):
    return await global_trending(sites, limit_per_site)

# --- Video Streaming Info ---
from app.services.video_streaming import get_video_info, get_stream_url
from fastapi import Request

@api_v1_router.get("/videos/info", tags=["Streaming"])
async def video_info_endpoint(request: Request, url: str = Query(..., description="Video page URL")):
    from app.config.settings import settings
    api_base = settings.BASE_URL or str(request.base_url)
    try:
        return await get_video_info(url, api_base_url=api_base)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch video info: {str(e)}")

@api_v1_router.get("/videos/stream", tags=["Streaming"])
async def direct_stream_endpoint(
    request: Request,
    url: str = Query(..., description="Video page URL"),
    quality: str = Query("default")
):
    from app.config.settings import settings
    api_base = settings.BASE_URL or str(request.base_url)
    try:
        return await get_stream_url(url, quality, api_base_url=api_base)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch stream URL: {str(e)}")


# --- Explore Sources ---
@api_v1_router.get("/sources", tags=["Sources"])
async def get_sources():
    """
    Returns the list of supported scraper sources for the Explore page.
    Moving this out of the Flutter app means sources can be updated remotely.
    """
    return {
        "status": "success",
        "data": [
            {
                "baseUrl": "https://masa49.org/",
                "nickname": "Masa49",
                "favicon": "https://masa49.org/favicon.ico",
                "accentColor": "#7C4DFF",
                "category": "free",
                "isVerified": False,
            },
            {
                "baseUrl": "https://xhamster.com/",
                "nickname": "xHamster",
                "favicon": "https://xhamster.com/favicon.ico",
                "accentColor": "#FF5252",
                "category": "free",
                "isVerified": False,
            },
            {
                "baseUrl": "https://www.xnxx.com/",
                "nickname": "XNXX",
                "favicon": "https://www.xnxx.com/favicon.ico",
                "accentColor": "#448AFF",
                "category": "free",
                "isVerified": True,
            },
            {
                "baseUrl": "https://www.xvideos.com/",
                "nickname": "XVideos",
                "favicon": "https://www.xvideos.com/favicon.ico",
                "accentColor": "#FFAB40",
                "category": "free",
                "isVerified": True,
            },
            {
                "baseUrl": "https://www.pornhub.com/",
                "nickname": "Pornhub",
                "favicon": "https://www.pornhub.com/favicon.ico",
                "accentColor": "#FF9100",
                "category": "free",
                "isVerified": False,
            },
            {
                "baseUrl": "https://www.youporn.com/",
                "nickname": "YouPorn",
                "favicon": "https://www.youporn.com/favicon.ico",
                "accentColor": "#FF4081",
                "category": "free",
                "isVerified": False,
            },
            {
                "baseUrl": "https://www.redtube.com/",
                "nickname": "RedTube",
                "favicon": "https://www.redtube.com/favicon.ico",
                "accentColor": "#D32F2F",
                "category": "free",
                "isVerified": False,
            },
            {
                "baseUrl": "https://beeg.com/",
                "nickname": "Beeg",
                "favicon": "https://cdn.brandfetch.io/id21sFe_5X/w/180/h/180/theme/dark/logo.png?c=1bxid64Mup7aczewSAYMX&t=1764366461758",
                "accentColor": "#00BFA5",
                "category": "free",
                "isVerified": False,
            },
            {
                "baseUrl": "https://spankbang.com/",
                "nickname": "SpankBang",
                "favicon": "https://spankbang.com/favicon.ico",
                "accentColor": "#FFC107",
                "category": "free",
                "isVerified": False,
            },
            {
                "baseUrl": "https://fapnut.net/",
                "nickname": "OnlyFans",
                "favicon": "https://fapnut.net/favicon.ico",
                "accentColor": "#00AFF0",
                "category": "paid",
                "isVerified": False,
            },
        ],
    }


# --- AppHub Version ---
from app import apphub_version
@api_v1_router.get("/apphub/version", tags=["System"])
async def get_apphub_version():
    return {
        "version": apphub_version.VERSION,
        "buildNumber": apphub_version.BUILD_NUMBER,
        "downloadUrl": apphub_version.DOWNLOAD_URL,
        "changelog": apphub_version.CHANGELOG.strip(),
        "changelogTitle": apphub_version.CHANGELOG_TITLE,
        "isMandatory": apphub_version.IS_MANDATORY,
        "sizeBytes": apphub_version.SIZE_BYTES,
    }


# Include Routers
app.include_router(api_v1_router)
app.include_router(recommendations.router, prefix="/api/v1/recommendations", tags=["AI Recommendations"])
app.include_router(hls.router, prefix="/api/v1/hls", tags=["HLS Proxy"])
app.include_router(media.router, prefix="/api/v1")


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    return {"status": "ok"}

