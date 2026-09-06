import asyncio
import aiohttp
import json

async def test():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.dexscreener.com/token-profiles/latest/v1") as resp:
            data = await resp.json()
            pump_tokens = [t for t in data if t.get('chainId') == 'solana' and t.get('tokenAddress', '').endswith('pump')]
            print(f"Found {len(pump_tokens)} pump tokens in profiles")
            if pump_tokens:
                print(pump_tokens[0])

asyncio.run(test())
