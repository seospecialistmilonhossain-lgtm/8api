# How to Add a New Scraper

This guide matches the current backend layout and registration flow.

## Current Structure

```text
backend/
├── main.py
└── app/
    ├── main.py
    └── scrapers/
        ├── __init__.py
        ├── xnxx/
        │   ├── __init__.py
        │   ├── scraper.py
        │   └── categories.json
        └── <site_name>/
            ├── __init__.py
            ├── scraper.py
            └── categories.json
```

## Required Interface

Each scraper module must expose these functions:

- `can_handle(host: str) -> bool`
- `scrape(url: str) -> dict`
- `list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict]`
- `get_categories() -> list[dict]` (or async if the scraper requires it)

Optional:

- `crawl_videos(...)` only if you want `/api/v1/crawls` support

## Step-by-Step

### 1) Create the new scraper folder

Create `backend/app/scrapers/<site_name>/` with:

- `scraper.py`
- `__init__.py`
- `categories.json`

Fastest start:

```bash
cp -r backend/app/scrapers/xnxx backend/app/scrapers/<site_name>
```

Then rename/update internals.

### 2) Implement exports in `__init__.py`

Example:

```python
from .scraper import can_handle, scrape, list_videos, get_categories

__all__ = ["can_handle", "scrape", "list_videos", "get_categories"]
```

If your scraper has `crawl_videos`, include it in imports/`__all__`.

### 3) Register scraper package

Edit `backend/app/scrapers/__init__.py`:

1. Add `from . import <site_name>`
2. Add `"<site_name>"` to `__all__`

If you skip this, importing from `app.scrapers` in `app/main.py` will fail.

### 4) Register in `backend/app/main.py`

Update all required dispatcher/router spots:

1. **Top-level import from `app.scrapers`**
   - Add `<site_name>` to the import list.
2. **`_scrape_dispatch(...)`**
   - Add branch for `can_handle()` -> `scrape()`.
3. **`_list_dispatch(...)`**
   - Add branch for `can_handle()` -> `list_videos()`.
4. **`get_categories(source: str)` endpoint**
   - Add source alias mapping -> `<site_name>.get_categories()`.
5. **`_crawl_dispatch(...)` (optional)**
   - Add only if your scraper implements crawling.

## Minimal `scraper.py` Template

```python
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return "example.com" in h or "www.example.com" in h


async def scrape(url: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        res = await client.get(url)
        res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")

    title = soup.title.get_text(strip=True) if soup.title else ""
    return {
        "url": url,
        "title": title,
        "thumbnail_url": None,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "video": {
            "streams": [],
            "hls": None,
            "default": None,
            "has_video": False,
        },
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict]:
    return []


def get_categories() -> list[dict]:
    return []
```

## Categories File

`categories.json` should be a list of category objects your scraper understands. Keep the shape consistent with existing scraper folders so `/api/v1/categories` returns valid `CategoryItem` entries.

## Verification Checklist

Before shipping:

- New folder exists in `backend/app/scrapers/<site_name>/`
- `backend/app/scrapers/__init__.py` includes `<site_name>`
- `backend/app/main.py` updated in:
  - scraper imports
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping
  - optional `_crawl_dispatch`
- `can_handle()` matches real hostnames
- `scrape()` and `list_videos()` return dict keys expected by API schemas

Quick manual tests (replace URL and source):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://example.com/video/123\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://example.com/videos&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=<site_name>"
```

If all three endpoints return valid data, your scraper integration is complete.

## TNAFlix Implementation Notes

Use this as a concrete example for `tnaflix.com` support.

### Host aliases

- `tnaflix.com`
- `www.tnaflix.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "tnaflix.com" or h.endswith(".tnaflix.com")
```

### Metadata extraction fallback order

For `scrape(url)` on TNAFlix, this order is resilient:

1. `og:title` / `og:description` / `og:image`
2. `twitter:title` / `twitter:image`
3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`, `keywords`)
4. Visible text fallback (duration/views regex)

This keeps the response stable even when one source disappears.

### Stream extraction approach

TNAFlix video URLs are typically exposed in inline script blocks. For a first pass:

- Scan page HTML for `.m3u8` and `.mp4` URLs
- Unescape script-escaped URLs (`\\/` -> `/`, `\\u0026` -> `&`)
- Build `video.streams` with:
  - `quality`
  - `url`
  - `format` (`hls` or `mp4`)
- Set `video.default` to the best candidate after sorting by quality

Keep the response shape compatible with existing `ScrapeResponse` expectations.

### Listing and pagination patterns

For `list_videos(base_url, page, limit)`:

- Parse video cards by filtering links that contain `/video`
- Pull title from `a[title]`, image `alt`, or visible text
- Pull thumbnail from `data-src` / `data-original` / `src`
- Extract duration/views/uploader from nearest card container text/selectors
- Start with query pagination (`?page={page}`) for page > 1

### Registration checklist for TNAFlix

Besides creating `backend/app/scrapers/tnaflix/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=tnaflix`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text (optional)
- `backend/app/services/global_search.py`
  - `available_scrapers`
  - search URL pattern
  - trending registry
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` for TNAFlix

### TNAFlix verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.tnaflix.com/video/123456/demo\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.tnaflix.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=tnaflix"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.tnaflix.com/video/123456/demo"
```

## HornySimp Implementation Notes

HornySimp (`hornysimp.com`) is a WordPress/Elementor-style listing site where video pages typically embed third-party players via `<iframe>`, rather than exposing direct `.mp4`/`.m3u8` URLs on the main page HTML.

### Host aliases

- `hornysimp.com`
- `www.hornysimp.com` (if it ever appears)

### Pagination pattern

Section pages and the home page paginate using a query param:

- `?_page=2`
- `?_page=3`

So `list_videos(base_url, page)` should generally build `base_url + "?_page={page}"` (or `&` if `base_url` already has a query).

### Stream extraction approach (same idea as `xxxparodyhd`)

For `scrape(url)`:

- Extract metadata from `og:title`, `og:description`, `og:image`, plus `h1` fallback.
- Collect player embed URLs from `iframe[src]` (skip ad iframes). The site uses two tabs (`Server 1` / `Server 2`); expose each iframe as its own stream with `format="embed"` and `quality` set to `"Server 1"`, `"Server 2"`, … matching the UI.
- Set `video.default` to the **Byse / byseraguci.com** embed (“Server 2”) when present, else **hrnyvid / LuluStream**, else the first embed.
- `GET /api/v1/videos/stream` for `hornysimp.com` includes **flat per-source fields** (`Server 1`, `Server 2`, …) in the JSON response, same pattern as `xxxparodyhd.net` (see `get_stream_url` in `video_streaming.py`).

### Registration checklist for HornySimp

Besides creating `backend/app/scrapers/hornysimp/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=hornysimp`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/services/global_search.py`
  - `available_scrapers`
  - search URL pattern (`https://hornysimp.com/?s={query}`)
  - trending registry (use `https://hornysimp.com/`)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

### HornySimp verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hornysimp.com/<post-slug>/\"}"

curl \"http://127.0.0.1:8000/api/v1/videos?base_url=https://hornysimp.com/leaked-clips/&page=1&limit=20\"

curl \"http://127.0.0.1:8000/api/v1/categories?source=hornysimp\"

curl \"http://127.0.0.1:8000/api/v1/videos/info?url=https://hornysimp.com/<post-slug>/\"
```
