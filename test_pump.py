import asyncio
import websockets
import json

async def run():
    uri = "wss://pumpportal.fun/api/data"
    async with websockets.connect(uri) as ws:
        payload = {"method": "subscribeNewToken"}
        await ws.send(json.dumps(payload))
        
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            if "mint" in data:
                mint = data["mint"]
                print(f"New token: {mint}")
                
                # Subscribe to trades
                trade_payload = {"method": "subscribeTokenTrade", "keys": [mint]}
                await ws.send(json.dumps(trade_payload))
                
                # Wait for 1 trade
                trade_msg = await ws.recv()
                trade_data = json.loads(trade_msg)
                print(json.dumps(trade_data, indent=2))
                break

asyncio.run(run())
