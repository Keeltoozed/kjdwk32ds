import asyncio
import aiohttp

async def fetch_gecko():
    url = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            print(resp.status)
            if resp.status == 200:
                data = await resp.json()
                for pool in data.get("data", [])[:5]:
                    attr = pool.get("attributes", {})
                    name = attr.get("name")
                    address = attr.get("address")
                    volume = attr.get("volume_usd", {}).get("h24")
                    print(f"{name}: {address} (Vol: {volume})")

asyncio.run(fetch_gecko())
