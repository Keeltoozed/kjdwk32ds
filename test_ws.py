import asyncio
import websockets
import json

async def test():
    uri = "wss://api.mainnet-beta.solana.com"
    async with websockets.connect(uri) as ws:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": ["6EF8rrecthR5Dkzon8Nwu78hRvfX9PNnS6p9PNnS6p9P"]},
                {"commitment": "processed"}
            ]
        }
        await ws.send(json.dumps(payload))
        resp = await ws.recv()
        print(f"Subscribed: {resp}")
        
        for _ in range(3):
            msg = await ws.recv()
            data = json.loads(msg)
            print("Log:", data['params']['result']['value']['logs'][:2])

asyncio.run(test())
