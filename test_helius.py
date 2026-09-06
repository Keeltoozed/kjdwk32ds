import asyncio
import aiohttp

async def test():
    HELIUS_API_KEY = "9efda6f4-fddb-42d3-a2b1-098bbbecd299"
    rpc_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [
            "6EF8rrecthR5Dkzon8Nwu78hRvfX9PNnS6p9PNnS6p9P",
            {"limit": 5}
        ]
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(rpc_url, json=payload) as resp:
            data = await resp.json()
            print(data)

asyncio.run(test())
