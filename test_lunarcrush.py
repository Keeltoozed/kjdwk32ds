import asyncio
import aiohttp
import config

async def test_lc():
    mint = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm" # WIF
    symbol = "WIF"
    headers = {"Authorization": f"Bearer {config.LUNARCRUSH_API_KEY}"}
    async with aiohttp.ClientSession() as session:
        # try mint
        url = f"https://lunarcrush.com/api4/public/coins/{mint}/v1"
        async with session.get(url, headers=headers) as resp:
            print("By Mint:", resp.status)
            if resp.status == 200:
                data = await resp.json()
                print(data)
                
        # try symbol
        url = f"https://lunarcrush.com/api4/public/coins/{symbol}/v1"
        async with session.get(url, headers=headers) as resp:
            print("By Symbol:", resp.status)
            if resp.status == 200:
                data = await resp.json()
                print(data)
                
asyncio.run(test_lc())
