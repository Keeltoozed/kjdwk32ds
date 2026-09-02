import asyncio
import websockets
import json

async def test_wss():
    async with websockets.connect("wss://pumpportal.fun/api/data") as ws:
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        print("Connected.")
        for _ in range(5):
            message = await ws.recv()
            print("Received:", message)

asyncio.run(test_wss())
