import asyncio
import websockets
import json

HELIUS_WSS = "wss://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"

async def test():
    async with websockets.connect(HELIUS_WSS) as ws:
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "transactionSubscribe",
            "params": [
                {"accountInclude": ["BL2uX147aTFis8hJsi5Gi8FjemVxkma7LdkYCMuNi8CW"]},
                {"commitment": "processed"}
            ]
        }))
        print("Connected.")
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            print("Response:", msg)
        except Exception as e:
            print(e)

asyncio.run(test())
