import asyncio
import aiohttp

async def test_pump():
    url = "https://frontend-api.pump.fun/coins?offset=0&limit=10&sort=market_cap&order=DESC&includeNsfw=false"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            print(resp.status)
            if resp.status == 200:
                data = await resp.json()
                for coin in data:
                    print(coin.get("symbol"), coin.get("usd_market_cap"), coin.get("mint"))

asyncio.run(test_pump())
