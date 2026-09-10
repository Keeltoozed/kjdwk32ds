import asyncio
import aiohttp
import config

async def test():
    mint = "4KsGXPQ6BZGgCdYVDrqacDUuhFhSf8TKfbjACcApgLPF" # The 114x missed rocket
    url = f"https://public-api.birdeye.so/defi/v3/token/trade-history?address={mint}&sort_type=asc&offset=0&limit=50"
    headers = {"X-API-KEY": config.BIRDEYE_API_KEY, "x-chain": "solana"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            print(data.keys())
            if data.get("success"):
                items = data.get("data", {}).get("items", [])
                print(f"Got {len(items)} items")
                if items:
                    print(items[0])
            else:
                print(data)

asyncio.run(test())
