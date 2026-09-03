import asyncio
import websockets
import json

async def test():
    async with websockets.connect("wss://pumpportal.fun/api/data") as ws:
        # Subscribe to all trades just to see the format of a trade event
        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": ["*"]})) # PumpPortal might not support "*" but let's try, or just wait for a token
        print("Connected.")
        
        for _ in range(3):
            msg = await ws.recv()
            print("MSG:", msg)

asyncio.run(test())
