from fastapi import APIRouter
from app.models.media_models import MediaConfigResponse, MediaConfigData, MediaProviderResponse, MediaCategoryResponse

router = APIRouter()

# Dummy Data matching the frontend implementation plan
# In the future, this data will be fetched from a database table
DUMMY_MEDIA_CONFIG = MediaConfigData(
    title="IPTV",
    description="Choose a provider and category to start watching",
    pin="1234",
    providers=[
        MediaProviderResponse(
            id="premium_iptv_1",
            name="Premium IPTV",
            logo_url="https://example.com/images/premium_logo.png",
            is_active=True,
            categories=[
                MediaCategoryResponse(
                    id="WorldWide",
                    title="World TV",
                    type="live",
                    logo_url="https://raw.githubusercontent.com/milon4999/apphub-release/refs/heads/main/world.svg",
                    color_hex="#2196F3",
                    playlist_url="https://iptv-org.github.io/iptv/index.m3u",
                ),
                MediaCategoryResponse(
                    id="movies",
                    title="Movies",
                    type="vod",
                    logo_url="https://raw.githubusercontent.com/milon4999/apphub-release/refs/heads/main/favicon%20(9).ico",
                    color_hex="#9C27B0",
                    playlist_url="https://raw.githubusercontent.com/milon4999/apphub-release/refs/heads/main/movie.m3u",
                ),
                MediaCategoryResponse(
                    id="sports",
                    title="Sports",
                    type="live",
                    logo_url="https://raw.githubusercontent.com/milon4999/apphub-release/refs/heads/main/trophy.png",
                    color_hex="#4CAF50",
                    playlist_url="https://raw.githubusercontent.com/milon4999/apphub-release/refs/heads/main/Sports2.m3u",
                ),
                MediaCategoryResponse(
                    id="BD",
                    title="Bangladesh",
                    type="live",
                    logo_url="https://raw.githubusercontent.com/milon4999/apphub-release/refs/heads/main/Flag_of_Bangladesh.svg",
                    color_hex="#4CAF50",
                    playlist_url="https://raw.githubusercontent.com/imShakil/tvlink/refs/heads/main/iptv.m3u8",
                ),
                MediaCategoryResponse(
                    id="adult_18",
                    title="Adult (18+)",
                    type="vod",
                    logo_url="https://raw.githubusercontent.com/milon4999/apphub-release/refs/heads/main/18-plus-age-restriction-icon.svg",
                    color_hex="#F44336",
                    playlist_url="https://raw.githubusercontent.com/milon4999/apphub-release/refs/heads/main/adult.m3u",
                    requires_pin=False,
                ),
            ],
        ),
        MediaProviderResponse(
            id="free_iptv_2",
            name="Global Free TV",
            logo_url="https://example.com/images/free_logo.png",
            is_active=True,
            categories=[
                MediaCategoryResponse(
                    id="world_news",
                    title="World News",
                    type="live",
                    icon="public_rounded",
                    color_hex="#00BCD4",
                    playlist_url="https://premium-iptv.com/api/live.m3u",
                ),
                MediaCategoryResponse(
                    id="documentaries",
                    title="Documentaries",
                    type="vod",
                    icon="landscape_rounded",
                    color_hex="#FF9800",
                    playlist_url="https://free-tv.com/api/docs.m3u",
                ),
            ],
        ),
    ],
)

@router.get("/media/providers", response_model=MediaConfigResponse, tags=["Media Center"])
async def get_media_providers() -> MediaConfigResponse:
    """
    Get the configuration for the Media Page, including active IPTV providers and their categories.
    """
    return MediaConfigResponse(
        status="success",
        data=DUMMY_MEDIA_CONFIG
    )
