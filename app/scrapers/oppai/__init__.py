"""Oppai.stream scraper (main hentai domain only — not read.oppai.stream / rule34)."""
from .scraper import can_handle, get_categories, list_videos, scrape

__all__ = ["can_handle", "scrape", "list_videos", "get_categories"]
