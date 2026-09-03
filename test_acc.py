import asyncio
import websockets
import json

async def test():
    # Let's track a random active trader from the create stream above: BL2uX147aTFis8hJsi5Gi8FjemVxkma7LdkYCMuNi8CW
    async with websockets.connect("wss://pumpportal.fun/api/data") as ws:
        await ws.send(json.dumps({
            "method": "subscribeAccountTrade", 
            "keys": ["BL2uX147aTFis8hJsi5Gi8FjemVxkma7LdkYCMuNi8CW"]
        }))
        print("Connected. Waiting for trade...")
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            print("MSG:", msg)
        except:
            print("No immediate message, but connection works.")

asyncio.run(test())
