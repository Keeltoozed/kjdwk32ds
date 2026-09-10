import asyncio
from birdeye_scanner import fetch_birdeye_trending

async def test():
    print("Testing Birdeye...")
    tokens = await fetch_birdeye_trending()
    print("Tokens found:", tokens)

asyncio.run(test())
