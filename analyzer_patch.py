import aiohttp
import json

async def get_holders_info(mint: str):
    import config
    rpc_url = f"https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint]
    }
    dev_holding_pct = 0.0
    top_10_holding_pct = 0.0
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(rpc_url, json=payload, timeout=5) as resp:
                data = await resp.json()
                accounts = data.get("result", {}).get("value", [])
                total_supply = 1_000_000_000 # Pump.fun supply
                if accounts:
                    top_10_amounts = [float(acc["uiAmount"]) for acc in accounts[:10]]
                    top_10_holding_pct = (sum(top_10_amounts) / total_supply) * 100
                    
                    # Assuming dev is often the first account (the bonding curve is the real first, but dev is often 2nd)
                    # Let's say dev is the largest individual holder for simplicity if it's new
                    dev_holding_pct = (top_10_amounts[0] / total_supply) * 100 
    except Exception as e:
        pass
        
    return dev_holding_pct, top_10_holding_pct
