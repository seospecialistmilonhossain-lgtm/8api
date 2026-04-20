from __future__ import annotations

from app.config.settings import settings


def static_asset_url(path: str) -> str:
    """Return CDN-backed static URL when configured, otherwise local /static URL."""
    normalized = path.lstrip("/")
    if normalized.startswith("static/"):
        normalized = normalized[len("static/"):]

    cdn_base = settings.STATIC_CDN_BASE_URL.strip()
    if cdn_base:
        return f"{cdn_base.rstrip('/')}/{normalized}"
    return f"/static/{normalized}"
