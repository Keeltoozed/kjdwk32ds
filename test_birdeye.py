import asyncio
import aiohttp
import config

async def test():
    urls = [
        "https://public-api.birdeye.so/defi/token_trending?sort_by=rank&sort_type=asc&offset=0&limit=50",
        "https://public-api.birdeye.so/defi/token_trending",
        "https://public-api.birdeye.so/defi/v1/token/trending",
        "https://public-api.birdeye.so/defi/v3/token/trending"
    ]
    headers = {"X-API-KEY": config.BIRDEYE_API_KEY, "x-chain": "solana"}
    
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url, headers=headers) as resp:
                    print(f"{url} -> {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        print(data.get("success"))
            except Exception as e:
                print(e)

asyncio.run(test())
