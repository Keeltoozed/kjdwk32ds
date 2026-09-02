import asyncio
import websockets
import json

async def test_wss():
    try:
        async with websockets.connect("wss://pumpportal.fun/api/data") as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            print("Connected. Waiting for token...")
            message = await asyncio.wait_for(ws.recv(), timeout=10)
            print("Received:", message)
    except Exception as e:
        print("Error:", e)

asyncio.run(test_wss())
