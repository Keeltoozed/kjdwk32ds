import asyncio
import json
import websockets

async def listen():
    uri = "wss://pumpportal.fun/api/data"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        for _ in range(3):
            msg = await ws.recv()
            print(json.loads(msg))

asyncio.run(listen())
