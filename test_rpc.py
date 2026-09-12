import asyncio
import aiohttp

async def test_rpc():
    url = "https://solana-rpc.publicnode.com"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getHealth"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
            print(resp.status)
            if resp.status == 200:
                print(await resp.json())

asyncio.run(test_rpc())
