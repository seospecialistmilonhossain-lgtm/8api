import asyncio
from app.scrapers.hqporner.scraper import list_videos

async def test():
    # Indian search has ~22 results (1 page)
    url = "https://hqporner.com/?q=indian"
    v1 = await list_videos(url, 1)
    v2 = await list_videos(url, 2)
    print(f"URL: {url}")
    print(f"Page 1: {len(v1)} items")
    print(f"Page 2: {len(v2)} items")
    
    if len(v2) > 0:
        print(f"P2 First item: {v2[0]['title']}")

if __name__ == "__main__":
    asyncio.run(test())
