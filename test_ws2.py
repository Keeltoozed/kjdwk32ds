import asyncio
import websockets
import json

async def test():
    async with websockets.connect("wss://pumpportal.fun/api/data") as ws:
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        print("Connected.")
        for _ in range(3):
            msg = await ws.recv()
            print("MSG:", msg)

asyncio.run(test())
