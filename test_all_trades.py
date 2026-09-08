import asyncio
import websockets
import json

async def run():
    uri = "wss://pumpportal.fun/api/data"
    async with websockets.connect(uri) as ws:
        payload = {"method": "subscribeTokenTrade"}
        await ws.send(json.dumps(payload))
        print("Subscribed to all trades...")
        count = 0
        while count < 5:
            msg = await ws.recv()
            print(msg)
            count += 1

asyncio.run(run())
