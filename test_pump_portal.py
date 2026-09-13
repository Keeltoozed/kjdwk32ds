import asyncio
import websockets
import json

async def listen():
    uri = "wss://pumpportal.fun/api/data"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        for _ in range(3): # Listen to a few messages
            msg = await ws.recv()
            data = json.loads(msg)
            print("Received:", list(data.keys()))
            if "marketCapSol" in data:
                print("MARKET CAP SOL:", data["marketCapSol"])

asyncio.run(listen())
