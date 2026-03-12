"""
Scrapers package - Contains all site-specific scraper modules

Each scraper is in its own folder with:
- scraper.py: Main scraping logic
- __init__.py: Package exports

Each scraper implements:
- can_handle(host: str) -> bool
- scrape(url: str) -> dict
- list_videos(url: str, page: int, limit: int) -> list[dict]
"""

from . import xnxx
from . import xhamster
from . import xvideos
from . import masa49
from . import pornhub
from . import youporn
from . import redtube
from . import beeg
from . import spankbang
from . import fapnut
from . import pornxp
from . import hqporner
from . import xxxparodyhd
from . import pornwex
from . import tube8
from . import pornhat
from . import brazzpw
from . import gosexpod
from . import watcherotic

__all__ = ['xnxx', 'xhamster', 'xvideos', 'masa49', 'pornhub', 'youporn', 'redtube', 'beeg', 'spankbang', 'fapnut', 'pornxp', 'hqporner', 'xxxparodyhd', 'pornwex', 'tube8', 'pornhat', 'brazzpw', 'gosexpod', 'watcherotic']
