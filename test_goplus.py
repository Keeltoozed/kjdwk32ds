import asyncio
import aiohttp

async def check():
    chain_id = "56" # BSC
    address = "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            print(data)

asyncio.run(check())
